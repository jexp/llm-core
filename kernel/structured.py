"""Spec-derived structured output: the promoted API contract *is* the LLM output schema.

LLM planning is unreliable, so model outputs are typed data — and the types come from the
contract registry itself. `build_plan_output()` turns every contract operation's request
schema into a per-op Pydantic variant, discriminated by `op`, so a plan the model emits is
constrained at generation time to known ops with schema-valid args. `build_battery_output()`
does the same for decision batteries (choice → enum, noul/score → bounded floats).

Generation-time constraint is defense-in-depth; the tool broker's post-hoc validation against
the same registry remains the enforcement point (ACE: outputs are data, verified at one point).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union
from weakref import WeakKeyDictionary

from pydantic import BaseModel, ConfigDict, Field, create_model

from kernel.contracts import ContractOp, ContractRegistry
from kernel.decide import DecisionDefinition

# weak-keyed on the registry instance so a garbage-collected registry's models go too
_MODEL_CACHE: WeakKeyDictionary[ContractRegistry, dict[tuple, type[BaseModel]]] = WeakKeyDictionary()


def _bounds(schema: dict) -> dict:
    """Map JSON Schema numeric bounds to pydantic Field kwargs."""
    kwargs: dict[str, Any] = {}
    if "exclusiveMinimum" in schema:
        kwargs["gt"] = schema["exclusiveMinimum"]
    if "minimum" in schema:
        kwargs.setdefault("ge", schema["minimum"])
    if "exclusiveMaximum" in schema:
        kwargs["lt"] = schema["exclusiveMaximum"]
    if "maximum" in schema:
        kwargs.setdefault("le", schema["maximum"])
    return kwargs


def _annotation(schema: dict, name: str, counter: list[int]) -> tuple[type, Field | None]:
    """JSON Schema subset → (annotation, Field). Supported: enum, object, array, number,
    integer, string, boolean — exactly what contract request schemas use."""
    if "enum" in schema:
        return Literal[tuple(schema["enum"])], None
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        counter[0] += 1
        nested = _object_model(schema, f"{name}M{counter[0]}", counter)
        return nested, None
    if t == "array":
        inner, inner_field = _annotation(schema.get("items", {}), f"{name}Item", counter)
        return list[inner], inner_field  # type: ignore[valid-type]
    if t == "integer":
        return int, Field(**_bounds(schema)) if _bounds(schema) else None
    if t == "number":
        return float, Field(**_bounds(schema)) if _bounds(schema) else None
    if t == "string":
        return str, Field(pattern=schema["pattern"]) if "pattern" in schema else None
    if t == "boolean":
        return bool, None
    return Any, None  # unconstrained passthrough (never for enforced fields)


def _object_model(schema: dict, name: str, counter: list[int]) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    required = set(schema.get("required", []))
    for prop, prop_schema in schema.get("properties", {}).items():
        annotation, field_info = _annotation(prop_schema, f"{name}_{prop}", counter)
        if prop in required:
            fields[prop] = (annotation, field_info if field_info else ...)
        elif "default" in prop_schema:
            fields[prop] = (annotation, field_info or Field(default=prop_schema["default"]))
        else:
            fields[prop] = (annotation, field_info) if field_info else (annotation, None)
    return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)


def _args_model(op: ContractOp, counter: list[int]) -> type:
    if not op.request_schema or not op.request_schema.get("properties"):
        return dict
    counter[0] += 1
    return _object_model(op.request_schema, f"Args_{op.op_id.replace('.', '_')}", counter)


def build_plan_output(contracts: ContractRegistry, allowed_ops: list[str] | None = None):
    """Dynamic plan output type: per-op variants discriminated by `op`, args typed by each
    op's promoted request schema. Cached on (id(contracts), allowed_ops)."""
    op_ids = sorted(allowed_ops) if allowed_ops else contracts.ids()
    per_registry = _MODEL_CACHE.setdefault(contracts, {})
    cache_key = tuple(op_ids)
    if cache_key in per_registry:
        return per_registry[cache_key]

    counter = [0]
    variants = []
    for op_id in op_ids:
        op = contracts.get(op_id)
        if op is None or op.mutating:  # M0: read-only ops only in plans
            continue
        variants.append(
            create_model(
                f"PlanOp_{op_id.replace('.', '_')}",
                __config__=ConfigDict(extra="forbid"),
                op=(Literal[op_id], ...),
                args=(_args_model(op, counter), Field(default_factory=dict)),
            )
        )
    if not variants:
        raise ValueError("no plannable (read-only, implemented) contract ops")

    union: Any = (
        variants[0]
        if len(variants) == 1
        else Annotated[Union[*variants], Field(discriminator="op")]
    )
    plan_out = create_model(
        "PlanOut",
        __config__=ConfigDict(extra="forbid"),
        ops=(list[union], Field(default_factory=list)),
        rationale=(str, ""),
    )
    per_registry[cache_key] = plan_out
    return plan_out


def build_battery_output(defn: DecisionDefinition) -> type[BaseModel]:
    """Dynamic output type for a decision battery: choice values become enums, noul/score
    values and confidence become 0..1 floats — the same semantics Jev answers carry."""
    counter = [0]
    fields: dict[str, Any] = {}
    for name, q in defn.questions.items():
        counter[0] += 1
        if q["type"] == "choice":
            options = list(q["criteria"]) if isinstance(q.get("criteria"), dict) else list(q.get("criteria", []))
            value_type: Any = Literal[tuple(options)] if options else str
            value_field: Any = ...
        elif q["type"] == "noul":
            value_type, value_field = float, Field(..., ge=0.0, le=1.0)
        else:  # score: level position 1..len(levels); bound by levels when known
            levels = q.get("criteria") or []
            value_type, value_field = (
                (float, Field(..., ge=1.0, le=float(len(levels)))) if levels else (float, ...)
            )
        answer = create_model(
            f"Answer_{defn.id.split('.')[-1]}_{name}",
            __config__=ConfigDict(extra="forbid"),
            value=(value_type, value_field),
            confidence=(float, Field(default=0.0, ge=0.0, le=1.0)),
        )
        fields[name] = (answer, ...)
    return create_model(f"Battery_{defn.id.split('.')[-1]}", __config__=ConfigDict(extra="forbid"), **fields)
