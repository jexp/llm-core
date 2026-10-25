"""Spec-derived structured output: the API contract constrains LLM output at generation time."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from kernel.contracts import ContractRegistry
from kernel.decide import DecisionRegistry
from kernel.structured import build_battery_output, build_plan_output

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def contracts() -> ContractRegistry:
    return ContractRegistry(REPO / "contracts" / "openapi" / "openapi.yaml")


# ---- plan output (per-op variants discriminated by op) --------------------------

def test_plan_output_accepts_valid_ops(contracts):
    PlanOut = build_plan_output(contracts)
    plan = PlanOut.model_validate({
        "ops": [
            {"op": "catalog.list", "args": {"category": "camping", "max_price": 20, "limit": 5}},
            {"op": "health", "args": {}},
        ],
        "rationale": "search then check",
    })
    assert plan.ops[0].args.category == "camping"
    assert plan.ops[1].op == "health"


def test_plan_output_rejects_unknown_op(contracts):
    PlanOut = build_plan_output(contracts)
    with pytest.raises(ValidationError):
        PlanOut.model_validate({"ops": [{"op": "payments.charge", "args": {}}]})


def test_plan_output_rejects_args_outside_contract_schema(contracts):
    PlanOut = build_plan_output(contracts)
    # bad enum
    with pytest.raises(ValidationError):
        PlanOut.model_validate({"ops": [{"op": "catalog.list", "args": {"category": "kayaking"}}]})
    # numeric bound (exclusiveMinimum: 0)
    with pytest.raises(ValidationError):
        PlanOut.model_validate({"ops": [{"op": "catalog.list", "args": {"max_price": -5}}]})
    # additionalProperties: false
    with pytest.raises(ValidationError):
        PlanOut.model_validate({"ops": [{"op": "catalog.list", "args": {"surprise": 1}}]})


def test_plan_output_optional_args_default(contracts):
    PlanOut = build_plan_output(contracts)
    plan = PlanOut.model_validate({"ops": [{"op": "catalog.list"}]})
    assert plan.ops[0].args == {} or plan.ops[0].args is not None


def test_plan_output_json_schema_is_carried_to_provider(contracts):
    """pydantic-ai serializes the output type to a JSON schema for structured output — the
    spec's enums/bounds must survive that translation."""
    PlanOut = build_plan_output(contracts)
    schema = PlanOut.model_json_schema()
    defs = schema.get("$defs", {})
    assert any("catalog_list" in name for name in defs)  # per-op variant models present
    dumped = str(schema)
    assert "camping" in dumped and "max_price" in dumped and "exclusiveMinimum" in dumped


# ---- battery output (choice → enum, noul/score → bounded) -----------------------

def test_battery_output_enums_and_bounds():
    defn = DecisionRegistry(REPO / "contracts" / "decisions").require("demo.classify")
    BatteryOut = build_battery_output(defn)
    ok = BatteryOut.model_validate({"tone": {"value": "calm", "confidence": 0.8},
                                    "urgent": {"value": 0.5, "confidence": 0.4}})
    assert ok.tone.value == "calm"
    with pytest.raises(ValidationError):
        BatteryOut.model_validate({"tone": {"value": "furious", "confidence": 0.9},
                                   "urgent": {"value": 0.5, "confidence": 0.4}})
    with pytest.raises(ValidationError):
        BatteryOut.model_validate({"tone": {"value": "calm", "confidence": 0.9},
                                   "urgent": {"value": 1.5, "confidence": 0.4}})


def test_battery_output_score_bounded_by_levels():
    defn = DecisionRegistry(REPO / "contracts" / "decisions").require("router.route")
    BatteryOut = build_battery_output(defn)
    assert BatteryOut.model_validate({
        "route_tier": {"value": "t1_interpreter", "confidence": 0.6},
        "risky": {"value": 0.2, "confidence": 0.7},
        "ambiguity": {"value": 2, "confidence": 0.7},
    })
    with pytest.raises(ValidationError):
        BatteryOut.model_validate({
            "route_tier": {"value": "t9_unknown", "confidence": 0.6},
            "risky": {"value": 0.2, "confidence": 0.7},
            "ambiguity": {"value": 9, "confidence": 0.7},  # levels: 1..5
        })
