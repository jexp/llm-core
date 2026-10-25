from pathlib import Path

from kernel.contracts import ContractRegistry

REPO = Path(__file__).resolve().parents[1]


def registry() -> ContractRegistry:
    return ContractRegistry(REPO / "contracts" / "openapi" / "openapi.yaml")


def test_ops_loaded_with_scenario():
    reg = registry()
    assert set(reg.ids()) == {"health", "catalog.list"}
    op = reg.get("catalog.list")
    assert op is not None and "product" in op.scenario.lower()
    assert op.mutating is False


def test_validate_args_enums_and_types():
    reg = registry()
    assert reg.validate_args("catalog.list", {"category": "camping", "max_price": 20}) == []
    assert reg.validate_args("catalog.list", {"category": "kayaking"}) != []
    assert reg.validate_args("catalog.list", {"max_price": -5}) != []
    assert reg.validate_args("catalog.list", {"unexpected": 1}) != []


def test_validate_output_resolves_refs():
    reg = registry()
    good = {"items": [{"id": "AO-001", "name": "Tent", "category": "camping", "price": 10.0,
                       "stock": 1, "description": "d"}], "count": 1}
    assert reg.validate_output("catalog.list", good) == []
    bad = {"items": [{"id": "X", "price": "cheap"}], "count": 1}
    assert reg.validate_output("catalog.list", bad) != []


def test_mcp_manifest_generated_from_ops():
    reg = registry()
    manifest = reg.mcp_manifest()
    tools = {t["name"]: t for t in manifest["tools"]}
    assert set(tools) == {"health", "catalog.list"}
    assert tools["catalog.list"]["input_schema"]  # request schema inlined
