"""Decision service: system-wide primitive for dynamic decisions (architecture principle 8).

`decide(name, state)` evaluates a registered, versioned question battery (Jev-compatible)
against loose/dynamic state. Backends — jev (remote), llm, rules — are interchangeable; a
backend failure degrades along the definition's declared fallback chain, thresholds turn
distributions into auto/confirm/escalate statuses, labeled pii/payment state is masked before
any remote egress, and every call is audited.
"""

from __future__ import annotations

import copy
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from kernel.audit import AuditLog, canonical
from kernel.config import Settings
from kernel.jev_client import JevUnavailable, run_battery

REMOTE_BACKENDS = {"jev"}


class DecisionUnavailable(Exception):
    pass


@dataclass(frozen=True)
class DecisionDefinition:
    id: str
    version: str
    description: str = ""
    backend: str = "jev"          # primary backend: jev | llm | rules
    fallback: str = "rules"       # llm | rules | escalate
    questions: dict[str, dict] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)
    defaults: dict[str, dict] = field(default_factory=dict)  # rules-backend answers
    data_labels: dict[str, str] = field(default_factory=dict)  # dotted path -> public|pii|payment
    cache_ttl_seconds: int = 0

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

    @classmethod
    def from_yaml(cls, path: Path) -> DecisionDefinition:
        raw = yaml.safe_load(Path(path).read_text())
        return cls(
            id=str(raw["id"]),
            version=str(raw.get("version", "1")),
            description=raw.get("description", ""),
            backend=raw.get("backend", "jev"),
            fallback=raw.get("fallback", "rules"),
            questions=raw.get("questions", {}),
            thresholds=raw.get("thresholds", {}),
            defaults=raw.get("defaults", {}),
            data_labels=raw.get("data_labels", {}),
            cache_ttl_seconds=int(raw.get("cache_ttl_seconds", 0)),
        )


class Answer(BaseModel):
    type: str  # choice | score | noul
    value: Any  # option name | level float | probability float
    probabilities: dict[str, float] = {}
    confidence: float | None = None
    status: str = "auto"  # auto | confirm | escalate


class DecisionOutcome(BaseModel):
    decision: str
    version: str
    backend: str
    degraded: bool
    answers: dict[str, Answer]
    cached: bool = False


class DecisionRegistry:
    def __init__(self, decisions_dir: Path) -> None:
        self._defs: dict[str, DecisionDefinition] = {}
        for path in sorted(Path(decisions_dir).glob("*.yaml")):
            defn = DecisionDefinition.from_yaml(path)
            self._defs[defn.id] = defn

    def get(self, decision_id: str) -> DecisionDefinition | None:
        return self._defs.get(decision_id)

    def require(self, decision_id: str) -> DecisionDefinition:
        if decision_id not in self._defs:
            raise KeyError(f"unknown decision definition: {decision_id}")
        return self._defs[decision_id]

    def ids(self) -> list[str]:
        return sorted(self._defs)


def _mask_paths(state: Any, labels: dict[str, str]) -> Any:
    """Mask fields whose dotted path is labeled pii/payment (IFC-lite egress control)."""
    sensitive = {path for path, cls in labels.items() if cls in {"pii", "payment"}}

    def mask(node: Any, prefix: str) -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                path = f"{prefix}.{k}" if prefix else str(k)
                if any(path == s or path.startswith(s + ".") for s in sensitive):
                    out[k] = "***"
                else:
                    out[k] = mask(v, path)
            return out
        if isinstance(node, list):
            return [mask(v, prefix) for v in node]
        return node

    return mask(copy.deepcopy(state), "")


def _apply_thresholds(answers: dict[str, Answer], defn: DecisionDefinition) -> None:
    t = defn.thresholds
    for name, a in answers.items():
        q = defn.questions.get(name, {})
        if a.value is None:  # escalate outcomes carry no value
            a.status = "escalate"
            continue
        if a.type == "choice":
            conf = a.confidence
            auto = t.get("choice_auto", 0.5)
            confirm = t.get("choice_confirm", 0.35)
            if conf is None or conf >= auto:
                a.status = "auto"
            elif conf >= confirm:
                a.status = "confirm"
            else:
                a.status = "escalate"
        elif a.type == "score":
            conf = a.confidence
            auto = t.get("score_auto", 0.5)
            if conf is None or conf >= auto:
                a.status = "auto"
            else:
                a.status = "escalate"
        elif a.type == "noul":
            p = float(a.value)
            yes = t.get("noul_yes", 0.7)
            no = t.get("noul_no", 0.3)
            a.status = "auto" if (p >= yes or p <= no) else "escalate"
        _ = q  # question metadata reserved for future per-question threshold overrides


class DecisionService:
    def __init__(
        self,
        settings: Settings,
        registry: DecisionRegistry,
        audit: AuditLog,
        jev_runner: Callable[[DecisionDefinition, dict, Settings], dict[str, Answer]] | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._audit = audit
        self._jev_runner = jev_runner or run_battery
        self._cache: dict[tuple[str, str], tuple[float, DecisionOutcome]] = {}

    def decide(
        self,
        name: str,
        state: dict,
        request_id: str = "-",
        principal: str = "system",
    ) -> DecisionOutcome:
        defn = self._registry.require(name.split("@")[0])

        cache_key = (defn.ref, hashlib.sha256(canonical(state).encode()).hexdigest())
        if defn.cache_ttl_seconds > 0:
            hit = self._cache.get(cache_key)
            if hit and time.monotonic() - hit[0] < defn.cache_ttl_seconds:
                outcome = hit[1].model_copy(deep=True)
                outcome.cached = True
                self._audit.append("DECISION", request_id, principal, {
                    "decision": defn.ref, "backend": outcome.backend, "degraded": outcome.degraded,
                    "cached": True, "state_digest": cache_key[1][:16],
                })
                return outcome

        # Data-egress policy: remote backends only ever see masked state.
        backend_state = _mask_paths(state, defn.data_labels) if defn.backend in REMOTE_BACKENDS else state

        attempts: list[str] = []
        outcome: DecisionOutcome | None = None
        for backend in [defn.backend] + ([defn.fallback] if defn.fallback not in {"escalate"} else []):
            try:
                answers = self._run_backend(backend, defn, backend_state)
            except (JevUnavailable, DecisionUnavailable) as e:
                attempts.append(f"{backend}: {e}")
                continue
            outcome = DecisionOutcome(
                decision=defn.id, version=defn.version, backend=backend,
                degraded=(backend != defn.backend), answers=answers,
            )
            break

        if outcome is None:
            # Every backend failed: return a typed escalate outcome so callers degrade safely.
            outcome = DecisionOutcome(
                decision=defn.id, version=defn.version, backend="escalate", degraded=True,
                answers={n: Answer(type=q["type"], value=None, status="escalate")
                         for n, q in defn.questions.items()},
            )

        _apply_thresholds(outcome.answers, defn)
        if defn.cache_ttl_seconds > 0:
            self._cache[cache_key] = (time.monotonic(), outcome.model_copy(deep=True))

        self._audit.append("DECISION", request_id, principal, {
            "decision": defn.ref, "backend": outcome.backend, "degraded": outcome.degraded,
            "attempts": attempts, "state_digest": cache_key[1][:16],
            "answers": {n: {"value": a.value, "confidence": a.confidence, "status": a.status}
                        for n, a in outcome.answers.items()},
        })
        return outcome

    def _run_backend(self, backend: str, defn: DecisionDefinition, state: dict) -> dict[str, Answer]:
        if backend == "jev":
            return self._jev_runner(defn, state, self._settings)
        if backend == "llm":
            return self._llm_backend(defn, state)
        if backend == "rules":
            answers = {}
            for name, spec in defn.defaults.items():
                answers[name] = Answer(type=spec["type"], value=spec.get("value"))
            missing = set(defn.questions) - set(answers)
            if missing:
                raise DecisionUnavailable(f"rules backend lacks defaults for: {sorted(missing)}")
            return answers
        raise DecisionUnavailable(f"unknown backend: {backend}")

    def _llm_backend(self, defn: DecisionDefinition, state: dict) -> dict[str, Answer]:
        from kernel.llm_decider import run_decision_battery_llm

        if not self._settings.llm_enabled:
            raise DecisionUnavailable("LLM backend not configured (LLMC_API_KEY/LLMC_BASE_URL)")
        # battery fallbacks are light classification tasks — use the fast model when set
        model_name = self._settings.llmc_small_model or None
        return run_decision_battery_llm(self._settings, defn, state, model_name=model_name)
