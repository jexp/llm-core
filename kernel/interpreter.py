"""T1 interpreter: turns requests into plan-DSL ops bound to contract operations (AIOS).

Primary path is an LLM agent with a typed Plan result; degraded path is a deterministic rules
interpreter so the kernel keeps serving (and tests stay hermetic) without provider keys. Plans
are validated against the contract registry before execution; in M0 only read-only ops are
allowed (mutating ops arrive with the orders module in M1).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from kernel.broker import ExecutionError

if TYPE_CHECKING:
    from kernel.audit import AuditLog
    from kernel.broker import ToolBroker
    from kernel.config import Settings
    from kernel.contracts import ContractRegistry
    from kernel.router import Envelope, RouteDecision

CATEGORIES = ("camping", "hiking", "climbing", "apparel")


class PlanOp(BaseModel):
    op: str
    args: dict = Field(default_factory=dict)
    expect: str = ""  # response schema ref/id — recorded for audit
    compensate: str | None = None


class Plan(BaseModel):
    ops: list[PlanOp]
    rationale: str = ""


class Interpreter:
    def __init__(self, settings: Settings, contracts: ContractRegistry,
                 broker: ToolBroker, audit: AuditLog) -> None:
        self._settings = settings
        self._contracts = contracts
        self._broker = broker
        self._audit = audit

    def handle(self, envelope: Envelope, decision: RouteDecision,
               request_id: str, principal: str) -> dict:
        plan, backend, error = self._build(envelope.payload)
        if plan is None:
            self._audit.append("ESCALATED", request_id, principal, {"reason": "plan build failed", "detail": error})
            return {"tier": "t1_interpreter", "status": "escalated", "reason": error or "plan build failed"}

        # Plan-level governance: ops must exist and be implemented; M0 is read-only.
        violations = []
        for p in plan.ops:
            op = self._contracts.get(p.op)
            if op is None:
                violations.append(f"{p.op}: not a contract operation")
            elif op.mutating:
                violations.append(f"{p.op}: mutating ops not enabled in M0")
            elif not self._broker.implemented(p.op):
                violations.append(f"{p.op}: no implementation registered")
        if violations:
            self._audit.append("ESCALATED", request_id, principal,
                               {"reason": "plan governance", "violations": violations})
            return {"tier": "t1_interpreter", "status": "escalated",
                    "reason": "plan rejected by governance", "violations": violations}

        self._audit.append("PLAN", request_id, principal, {
            "backend": backend,
            "ops": [p.model_dump() for p in plan.ops],
            "rationale": plan.rationale,
        })

        results: list[dict] = []
        errors: list[dict] = []
        for p in plan.ops:
            try:
                output = self._broker.execute(p.op, p.args, request_id, principal)
                results.append({"op": p.op, "ok": True, "output": output})
            except ExecutionError as e:
                errors.append({"op": p.op, "ok": False, "error": str(e)})
                break  # M0 ops are read-only; compensation starts with M1 orders

        return {
            "tier": "t1_interpreter",
            "status": "completed" if not errors else "partial",
            "plan_backend": backend,
            "plan": plan.model_dump(),
            "results": results,
            "errors": errors,
            "summary": self._summarize(plan, results),
        }

    def _summarize(self, plan: Plan, results: list[dict]) -> str:
        if not plan.ops:
            return "no plan ops"
        outputs = [r for r in results if r.get("ok")]
        if outputs and "count" in outputs[0].get("output", {}):
            return f"{len(plan.ops)} op(s), {outputs[0]['output']['count']} item(s)"
        return f"{len(plan.ops)} op(s), {len(outputs)} succeeded"

    # ---- plan builders -----------------------------------------------------------

    def _build(self, payload: dict) -> tuple[Plan | None, str, str | None]:
        if self._settings.llm_enabled:
            try:
                return self._build_llm(payload), "llm", None
            except Exception as e:  # noqa: BLE001 — degrade to rules interpreter
                plan = self._build_rules(payload)
                return plan, "rules-fallback", f"llm interpreter unavailable: {e}"
        return self._build_rules(payload), "rules-fallback", None

    def _build_llm(self, payload: dict) -> Plan:
        import json

        from kernel.llm_decider import run_typed

        ops = [
            {"op": o["op"], "scenario": o["scenario"], "request_schema": o["request_schema"]}
            for o in self._contracts.capability_summary()
        ]
        system = (
            "You are the interpreter of an LLM-native retail kernel. Turn the request into a "
            "plan: a short sequence of ops chosen ONLY from the listed contract operations, "
            "with args matching each op's request_schema exactly. Prefer the fewest ops. "
            "If no op can serve the request, return an empty ops list."
        )
        prompt = (
            f"REQUEST:\n{json.dumps(payload, default=str)[:4000]}\n\n"
            f"CONTRACT OPS:\n{json.dumps(ops, default=str)[:6000]}"
        )
        plan = run_typed(self._settings, system, prompt, Plan)
        return plan

    def _build_rules(self, payload: dict) -> Plan:
        text = str(payload.get("text", ""))
        args: dict = {"limit": 10}
        price = _parse_price_ceiling(text)
        if price is not None:
            args["max_price"] = price
        category = next((c for c in CATEGORIES if c in text.lower()), None)
        if category:
            args["category"] = category
        return Plan(
            ops=[PlanOp(op="catalog.list", args=args, expect="CatalogListResponse")],
            rationale="rules interpreter: price/category extraction from natural language",
        )


_PRICE_PATTERNS = [
    (
        r"(?:under|below|less than|up to|max(?:imum)? of|cheaper than|at most|no more than)"
        r"[\s$]*(\d+(?:\.\d{1,2})?)"
    ),
    r"\$\s*(\d+(?:\.\d{1,2})?)",
]


def _parse_price_ceiling(text: str) -> float | None:
    for pattern in _PRICE_PATTERNS:
        if m := re.search(pattern, text, flags=re.IGNORECASE):
            return float(m.group(1))
    return None
