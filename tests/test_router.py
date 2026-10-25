from pathlib import Path

from kernel.contracts import ContractRegistry
from kernel.decide import DecisionRegistry, DecisionService
from kernel.router import Envelope, Router

REPO = Path(__file__).resolve().parents[1]


def make_router(settings, audit) -> Router:
    contracts = ContractRegistry(REPO / "contracts" / "openapi" / "openapi.yaml")
    decisions = DecisionService(settings, DecisionRegistry(REPO / "contracts" / "decisions"), audit)
    return Router(settings, contracts, decisions, audit)


def test_rules_exact_op_routes_t0(settings, audit):
    d = make_router(settings, audit).route(
        Envelope(payload={"op": "catalog.list", "args": {}}), "r1", "tester"
    )
    assert d.tier == "t0_deterministic" and d.backend == "rules" and d.degraded is False
    assert d.risk_class == "read_only"


def test_rules_unknown_op_routes_t1(settings, audit):
    d = make_router(settings, audit).route(
        Envelope(payload={"op": "orders.create", "args": {}}), "r1", "tester"
    )
    assert d.tier == "t1_interpreter" and d.degraded is True


def test_nl_defaults_to_t1_degraded_without_backends(settings, audit):
    d = make_router(settings, audit).route(
        Envelope(payload={"text": "what do you have under $20?"}), "r1", "tester"
    )
    assert d.tier == "t1_interpreter" and d.degraded is True
    assert d.backend == "escalate"  # battery exhausted (no jev key, no llm key)
    events = [r.event for r in audit.all()]
    assert "DECISION" in events and "DECIDED" in events
