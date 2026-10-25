"""Contract registry: contracts/openapi.yaml is the single source of truth.

Operations carry the AIOS two-element tool-API: the machine contract (request/response JSON
Schema) plus a system-written `x-scenario` description used by the router/decision models —
never by callers or sellers (ACE: descriptions are attack surface).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator


@dataclass(frozen=True)
class ContractOp:
    op_id: str
    path: str
    method: str
    scenario: str
    request_schema: dict
    response_schema: dict
    mutating: bool
    impl_version: str = "0.1.0"


def _resolve_refs(node: Any, schemas: dict) -> Any:
    """Inline $ref:'#/components/schemas/X' so jsonschema can validate without a registry."""
    if isinstance(node, dict):
        if set(node) == {"$ref"}:
            ref = node["$ref"]
            if ref.startswith("#/components/schemas/"):
                return _resolve_refs(schemas[ref.rsplit("/", 1)[-1]], schemas)
            raise ValueError(f"unsupported $ref: {ref}")
        return {k: _resolve_refs(v, schemas) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(v, schemas) for v in node]
    return node


class ContractRegistry:
    def __init__(self, openapi_path: Path) -> None:
        self._doc: dict = yaml.safe_load(Path(openapi_path).read_text())
        self._ops: dict[str, ContractOp] = {}
        schemas = self._doc.get("components", {}).get("schemas", {})
        for path, item in self._doc.get("paths", {}).items():
            for method, op in item.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if "operationId" not in op or "x-scenario" not in op:
                    continue  # transport endpoints (e.g. /v1/request) are not contract ops
                req_schema = {}
                rb = op.get("requestBody", {}).get("content", {}).get("application/json", {})
                if "schema" in rb:
                    req_schema = _resolve_refs(rb["schema"], schemas)
                resp_schema = {}
                r200 = op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {})
                if "schema" in r200:
                    resp_schema = _resolve_refs(r200["schema"], schemas)
                self._ops[op["operationId"]] = ContractOp(
                    op_id=op["operationId"],
                    path=path,
                    method=method,
                    scenario=op["x-scenario"],
                    request_schema=req_schema,
                    response_schema=resp_schema,
                    mutating=bool(op.get("x-mutating", False)),
                )

    def ids(self) -> list[str]:
        return sorted(self._ops)

    def get(self, op_id: str) -> ContractOp | None:
        return self._ops.get(op_id)

    def require(self, op_id: str) -> ContractOp:
        op = self._ops.get(op_id)
        if op is None:
            raise KeyError(f"unknown contract operation: {op_id}")
        return op

    def validate_args(self, op_id: str, args: dict) -> list[str]:
        op = self._ops.get(op_id)
        if op is None:
            return [f"unknown operation: {op_id}"]
        if not op.request_schema:
            return []
        return [e.message for e in Draft202012Validator(op.request_schema).iter_errors(args)]

    def validate_output(self, op_id: str, output: dict) -> list[str]:
        op = self._ops.get(op_id)
        if op is None or not op.response_schema:
            return []
        return [
            f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}"
            for e in Draft202012Validator(op.response_schema).iter_errors(output)
        ]

    def capability_summary(self) -> list[dict]:
        """System-written capability list fed to deciders (trusted inputs only)."""
        return [
            {"op": o.op_id, "scenario": o.scenario, "mutating": o.mutating,
             "request_schema": o.request_schema or {"type": "object"}}
            for o in sorted(self._ops.values(), key=lambda o: o.op_id)
        ]

    def openapi_dict(self) -> dict:
        return self._doc

    def mcp_manifest(self) -> dict:
        """Promoted MCP tool manifest generated from the same source of truth."""
        return {
            "manifest_version": "0.1",
            "server": {"name": "llm-core", "version": "0.1.0"},
            "transport": "POST /v1/request with {payload: {op, args}}",
            "tools": [
                {"name": o.op_id, "description": o.scenario, "input_schema": o.request_schema}
                for o in sorted(self._ops.values(), key=lambda o: o.op_id)
            ],
        }
