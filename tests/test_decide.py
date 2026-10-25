from pathlib import Path

import pytest

from kernel.decide import (
    Answer,
    DecisionRegistry,
    DecisionService,
    JevUnavailable,
    _mask_paths,
)

REPO = Path(__file__).resolve().parents[1]


class FakeJev:
    """Replaces the Jev backend in tests; can capture state and fail on demand."""

    def __init__(self, answers=None, fail=False):
        self.answers = answers or {}
        self.fail = fail
        self.seen_states: list[dict] = []

    def __call__(self, defn, state, settings):
        if self.fail:
            raise JevUnavailable("fake outage")
        self.seen_states.append(state)
        return {name: Answer(**a) for name, a in self.answers.items()}


def service(settings, audit, jev) -> DecisionService:
    registry = DecisionRegistry(REPO / "contracts" / "decisions")
    return DecisionService(settings, registry, audit, jev_runner=jev)


def test_registry_loads_definitions():
    registry = DecisionRegistry(REPO / "contracts" / "decisions")
    assert set(registry.ids()) == {"router.route", "demo.classify"}
    route = registry.require("router.route")
    assert set(route.questions) == {"route_tier", "risky", "ambiguity"}
    assert route.fallback == "llm"


def test_decide_jev_backend_with_threshold_statuses(settings, audit):
    jev = FakeJev(answers={
        "tone": {"type": "choice", "value": "calm", "confidence": 0.8,
                 "probabilities": {"calm": 0.85, "frustrated": 0.1, "angry": 0.05}},
        "urgent": {"type": "noul", "value": 0.5},  # unsure band -> escalate
    })
    outcome = service(settings, audit, jev).decide("demo.classify", {"message": "hello there"})
    assert outcome.backend == "jev" and outcome.degraded is False
    assert outcome.answers["tone"].status == "auto"
    assert outcome.answers["urgent"].status == "escalate"
    # demo.classify labels message as pii -> remote backend must see masked state
    assert jev.seen_states[0]["message"] == "***"


def test_decide_falls_back_to_rules_defaults(settings, audit):
    outcome = service(settings, audit, FakeJev(fail=True)).decide(
        "demo.classify", {"message": "where is my order?"}
    )
    assert outcome.backend == "rules" and outcome.degraded is True
    assert outcome.answers["tone"].value == "calm"
    assert outcome.answers["urgent"].value == 0.2


def test_decide_escalate_when_no_backend_and_fallback_escalate(settings, audit):
    jev = FakeJev(fail=True)
    outcome = service(settings, audit, jev).decide("router.route", {"request": {"text": "hi"}})
    assert outcome.backend == "escalate" and outcome.degraded is True
    assert all(a.status == "escalate" for a in outcome.answers.values())


def test_decide_audits_every_call(settings, audit):
    service(settings, audit, FakeJev(answers={
        "tone": {"type": "choice", "value": "calm", "confidence": 0.9},
        "urgent": {"type": "noul", "value": 0.1},
    })).decide("demo.classify", {"message": "x"}, request_id="r1", principal="tester")
    events = [r.event for r in audit.for_request("r1")]
    assert events == ["DECISION"]


def test_mask_paths_masks_nested_sensitive_fields():
    labels = {"customer.email": "pii", "card.number": "payment", "note": "public"}
    state = {"customer": {"email": "a@b.c", "name": "Ann"}, "card": {"number": "4111"}, "note": "hi"}
    masked = _mask_paths(state, labels)
    assert masked["customer"]["email"] == "***"
    assert masked["card"]["number"] == "***"
    assert masked["customer"]["name"] == "Ann"
    assert masked["note"] == "hi"
    assert state["customer"]["email"] == "a@b.c"  # original untouched


def test_cache_returns_cached_outcome(settings, audit):
    jev = FakeJev(answers={
        "tone": {"type": "choice", "value": "calm", "confidence": 0.9},
        "urgent": {"type": "noul", "value": 0.1},
    })
    svc = service(settings, audit, jev)
    o1 = svc.decide("demo.classify", {"message": "same"})
    o2 = svc.decide("demo.classify", {"message": "same"})
    assert o1.cached is False and o2.cached is True
    assert len(jev.seen_states) == 1


def test_unknown_decision_raises(settings, audit):
    with pytest.raises(KeyError):
        service(settings, audit, FakeJev()).decide("nope.nope", {})
