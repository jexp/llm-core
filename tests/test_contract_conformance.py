"""Contract conformance: the spec document itself is valid, house rules hold, and every
registered implementation reproduces its golden exemplars exactly."""

from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

import modules.catalog
from kernel.contracts import ContractRegistry

REPO = Path(__file__).resolve().parents[1]
HANDLERS = {"catalog.list": modules.catalog.catalog_list}


@pytest.fixture(scope="module")
def contracts() -> ContractRegistry:
    # __init__ raises on any document/house-rule violation — loading is the first test
    return ContractRegistry(REPO / "contracts" / "openapi" / "openapi.yaml")


def test_spec_document_is_valid_openapi(contracts):
    assert contracts.ids()  # sanity: registry constructed without raising


def test_exemplars_exist_for_promoted_ops(contracts):
    assert "catalog.list" in contracts.exemplared_ops()


def test_implementations_reproduce_exemplars_exactly(contracts):
    for op_id in contracts.exemplared_ops():
        for ex in contracts.exemplars(op_id):
            output = HANDLERS[op_id](ex["args"])
            assert output == ex["output"], f"{op_id}/{ex['name']}: implementation drifted from exemplar"
            errors = list(Draft202012Validator(
                contracts.get(op_id).response_schema).iter_errors(output))
            assert not errors, f"{op_id}/{ex['name']}: exemplar output violates contract"


def test_exemplar_args_validate_against_request_schema(contracts):
    for op_id in contracts.exemplared_ops():
        for ex in contracts.exemplars(op_id):
            assert contracts.validate_args(op_id, ex["args"]) == [], f"{op_id}/{ex['name']}: bad args"


def test_house_rules_reject_open_object_schema(tmp_path):
    bad = yaml.safe_load((REPO / "contracts" / "openapi" / "openapi.yaml").read_text())
    bad["components"]["schemas"]["CatalogListRequest"].pop("additionalProperties")
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(bad))
    with pytest.raises(ValueError, match="additionalProperties"):
        ContractRegistry(p)


def test_house_rules_reject_operationid_without_scenario(tmp_path):
    bad = yaml.safe_load((REPO / "contracts" / "openapi" / "openapi.yaml").read_text())
    bad["paths"]["/ops/health"]["get"].pop("x-scenario")
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(bad))
    with pytest.raises(ValueError, match="x-scenario"):
        ContractRegistry(p)
