"""Decision router: rules → Jev decider → LLM fallback (architecture, request lifecycle 3).

Rules run first and trusted-only: the routing decision is computed from the request envelope,
policy, and the system-owned capability registry — never from caller text fed to control
position. The Jev battery classifies tier/intent/risk; low-confidence or unavailable deciders
degrade along the declared chain with the degraded path recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from kernel.audit import AuditLog
    from kernel.config import Settings
    from kernel.contracts import ContractRegistry
    from kernel.decide import DecisionService

TIERS = {"t0_deterministic", "t1_interpreter", "t2_compile_candidate", "escalate"}


class Envelope(BaseModel):
    capability_hint: str | None = None
    payload: dict[str, Any]
    idempotency_key: str | None = None


class RouteDecision(BaseModel):
    tier: str  # TIERS
    intent: str = ""
    confidence: float | None = None
    risk_class: str = "read_only"  # read_only | state_changing | payment
    backend: str = "rules"  # rules | jev | llm
    degraded: bool = False
    reason: str = ""


class Router:
    def __init__(self, settings: Settings, contracts: ContractRegistry,
                 decisions: DecisionService, audit: AuditLog) -> None:
        self._settings = settings
        self._contracts = contracts
        self._decisions = decisions
        self._audit = audit

    def route(self, envelope: Envelope, request_id: str, principal: str) -> RouteDecision:
        decision = self._route(envelope, request_id, principal)
        self._audit.append("DECIDED", request_id, principal, {
            "tier": decision.tier, "intent": decision.intent, "risk_class": decision.risk_class,
            "backend": decision.backend, "degraded": decision.degraded,
            "confidence": decision.confidence, "reason": decision.reason,
        })
        return decision

    def _route(self, envelope: Envelope, request_id: str, principal: str) -> RouteDecision:
        payload = envelope.payload or {}

        # 1. Rules (deterministic, always first)
        op = payload.get("op")
        if isinstance(op, str) and op:
            contract = self._contracts.get(op)
            if contract is not None:
                return RouteDecision(
                    tier="t0_deterministic", intent=op, confidence=1.0, backend="rules",
                    risk_class="state_changing" if contract.mutating else "read_only",
                    reason="exact contract-op match",
                )
            return RouteDecision(
                tier="t1_interpreter", intent=op, backend="rules", degraded=True,
                reason="unknown op — interpreter explains/plans (JIT candidate signal)",
            )

        # 2. Jev battery (router.route@1)
        state = {
            "request": payload,
            "capability_hint": envelope.capability_hint,
            "candidate_ops": self._contracts.capability_summary(),
        }
        outcome = self._decisions.decide("router.route", state, request_id, principal)
        tier_answer = outcome.answers.get("route_tier")
        risky_answer = outcome.answers.get("risky")
        tier: str | None = None
        if (
            tier_answer is not None
            and tier_answer.value in TIERS
            and tier_answer.status in {"auto", "confirm"}
        ):
            tier = str(tier_answer.value)

        # 3. LLM decider fallback when the battery couldn't decide
        if tier is None and self._settings.llm_enabled:
            try:
                tier, conf, reason = self._llm_decide(payload)
            except Exception:  # noqa: BLE001 — degrade, never crash
                tier, conf, reason = None, None, "llm decider unavailable"
            if tier in TIERS:
                return RouteDecision(
                    tier=tier, intent="natural_language", confidence=conf, backend="llm",
                    degraded=outcome.degraded or outcome.backend != "jev", reason=reason or "llm decider",
                )

        # 4. Deterministic default: NL payloads are read-only browsing in M0 → interpreter.
        if tier is None:
            tier = "t1_interpreter"
        risk_class = "read_only"
        if (
            risky_answer is not None
            and risky_answer.type == "noul"
            and risky_answer.value is not None
            and float(risky_answer.value) >= 0.7
        ):
            risk_class = "state_changing"
        return RouteDecision(
            tier=tier,
            intent="natural_language",
            confidence=(tier_answer.confidence if tier_answer else None),
            backend=outcome.backend,
            degraded=outcome.degraded or outcome.backend != "jev",
            risk_class=risk_class,
            reason=f"decision battery {outcome.decision}@{outcome.version}",
        )

    def _llm_decide(self, payload: dict) -> tuple[str | None, float | None, str]:
        import json

        from kernel.llm_decider import run_typed

        class _Route(BaseModel):
            tier: str
            confidence: float | None = None
            reason: str = ""

        system = (
            "You are the routing decider of an LLM-native retail kernel. Classify the request "
            "into exactly one tier: t0_deterministic (a precise structured call to a known op), "
            "t1_interpreter (needs interpretation/planning over contract ops), or escalate. "
            "T2 is never chosen at request time."
        )
        prompt = (
            f"REQUEST:\n{json.dumps(payload, default=str)[:4000]}\n\n"
            f"CONTRACT OPS:\n{json.dumps(self._contracts.capability_summary(), default=str)[:6000]}"
        )
        route = run_typed(
            self._settings, system, prompt, _Route,
            model_name=self._settings.llmc_small_model or None,  # fast model for routing
        )
        return route.tier, route.confidence, route.reason
