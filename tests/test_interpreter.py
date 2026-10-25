from pathlib import Path

import pytest

import modules.catalog
from kernel.broker import ExecutionError, ToolBroker
from kernel.contracts import ContractRegistry
from kernel.interpreter import Interpreter, Plan, PlanOp, _parse_price_ceiling
from kernel.router import Envelope, RouteDecision

REPO = Path(__file__).resolve().parents[1]


def make_interpreter(settings, audit) -> tuple[Interpreter, ToolBroker]:
    contracts = ContractRegistry(REPO / "contracts" / "openapi" / "openapi.yaml")
    broker = ToolBroker(contracts, audit)
    modules.catalog.register(broker)
    return Interpreter(settings, contracts, broker, audit), broker


def nl_decision() -> RouteDecision:
    return RouteDecision(tier="t1_interpreter", intent="natural_language", backend="escalate",
                         degraded=True, reason="test")


def test_price_parsing_variants():
    assert _parse_price_ceiling("what do you have under $20 for camping?") == 20.0
    assert _parse_price_ceiling("anything below 35 bucks") == 35.0
    assert _parse_price_ceiling("show me the cheapest tent") is None


def test_rules_interpreter_builds_contract_bound_plan(settings, audit):
    interp, _ = make_interpreter(settings, audit)
    response = interp.handle(
        Envelope(payload={"text": "what do you have under $20 for camping?"}),
        nl_decision(), "r1", "tester",
    )
    assert response["status"] == "completed"
    plan = response["plan"]
    assert plan["ops"][0]["op"] == "catalog.list"
    args = plan["ops"][0]["args"]
    assert args["max_price"] == 20.0 and args["category"] == "camping"
    items = response["results"][0]["output"]["items"]
    assert items and all(i["price"] <= 20 and i["category"] == "camping" for i in items)
    events = [r.event for r in audit.for_request("r1")]
    assert events == ["PLAN", "OP_RESULT"]


def test_unknown_plan_op_rejected_by_governance(settings, audit):
    interp, _ = make_interpreter(settings, audit)
    interp._build = lambda payload: (
        Plan(ops=[PlanOp(op="payments.charge")], rationale="injected plan"),
        "rules-fallback",
        None,
    )
    response = interp.handle(Envelope(payload={"text": "charge me"}), nl_decision(), "r2", "tester")
    assert response["status"] == "escalated"
    assert any("not a contract operation" in v for v in response["violations"])


def test_broker_rejects_contract_violating_output(settings, audit):
    _, broker = make_interpreter(settings, audit)
    broker.register("catalog.list", lambda args: {"wrong": True})
    with pytest.raises(ExecutionError):
        broker.execute("catalog.list", {}, "r3", "tester")
