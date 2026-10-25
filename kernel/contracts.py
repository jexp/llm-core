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
        self._exemplars: dict[str, list[dict]] = self._load_exemplars(Path(openapi_path).parent.parent / "exemplars")
        issues = self._validate_document()
        if issues:
            raise ValueError("invalid contract document:\n  - " + "\n  - ".join(issues))
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

    # ---- document validation ------------------------------------------------------

    def _validate_document(self) -> list[str]:
        """Validate the spec itself — critical because the document is LLM-generated at
        bootstrap. Checks: OpenAPI 3.x document validity, every schema is valid 2020-12,
        and house rules for LLM-structured-output compatibility (object schemas must close
        themselves with additionalProperties; ops must carry x-scenario)."""
        issues: list[str] = []
        try:
            from openapi_spec_validator import validate as validate_openapi

            validate_openapi(self._doc)
        except ImportError:
            issues.append("openapi-spec-validator not installed — document validity unchecked")
        except Exception as e:  # noqa: BLE001 — validator raises many shapes
            issues.append(f"not a valid OpenAPI document: {e}")

        for name, schema in self._doc.get("components", {}).get("schemas", {}).items():
            issues += self._schema_issues(schema, f"components.schemas.{name}")
        for path, item in self._doc.get("paths", {}).items():
            for method, op in item.items():
                if method not in {"get", "post", "put", "patch", "delete"} or not isinstance(op, dict):
                    continue
                if "operationId" in op and not str(op.get("x-scenario", "")).strip():
                    issues.append(f"{method} {path}: operationId without x-scenario")
                where = f"{method} {path}"
                for kind in ("requestBody", "responses"):
                    node = op.get(kind)
                    if isinstance(node, dict):
                        for code, resp in node.items():
                            schema = (
                                resp.get("content", {}).get("application/json", {}).get("schema")
                                if kind == "responses"
                                else node.get("content", {}).get("application/json", {}).get("schema")
                            )
                            if schema:
                                issues += self._schema_issues(schema, f"{where} {kind}[{code}]")
        return issues

    def _schema_issues(self, schema: dict, where: str) -> list[str]:
        issues: list[str] = []
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as e:  # noqa: BLE001
            return [f"{where}: invalid JSON Schema — {e}"]

        def walk(node: Any, loc: str) -> None:
            if isinstance(node, dict):
                if set(node) == {"$ref"}:
                    return
                if node.get("type") == "object" and "additionalProperties" not in node:
                    issues.append(f"{loc}: object schema must set additionalProperties (closed-world)")
                for key, value in node.items():
                    if key in {"properties", "items", "anyOf", "oneOf", "allOf", "prefixItems"}:
                        if isinstance(value, dict):
                            for sub_key, sub in value.items():
                                walk(sub, f"{loc}.{key}.{sub_key}")
                        elif isinstance(value, list):
                            for i, sub in enumerate(value):
                                walk(sub, f"{loc}.{key}[{i}]")

        walk(schema, where)
        return issues

    def _load_exemplars(self, exemplars_dir: Path) -> dict[str, list[dict]]:
        """Golden request→response pairs per op (contracts/exemplars/<op>.yaml). These are the
        client-visible contract behavior, regression tests for implementations, and the shadow
        corpus for T2 promotion."""
        loaded: dict[str, list[dict]] = {}
        if not exemplars_dir.is_dir():
            return loaded
        for path in sorted(exemplars_dir.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text()) or {}
            for ex in raw.get("exemplars", []):
                loaded.setdefault(raw.get("op", path.stem), []).append(ex)
        return loaded

    def exemplars(self, op_id: str) -> list[dict]:
        return self._exemplars.get(op_id, [])

    def exemplared_ops(self) -> list[str]:
        return sorted(self._exemplars)

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
