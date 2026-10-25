"""Gateway: single entry point + spec promotion endpoints.

Every request is authenticated, audited (RECEIVED … COMPLETED), routed through the decision
chain, and executed on a tier. The audit backend is Postgres when reachable, otherwise an
in-memory sink with the degraded path flagged.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, Request

import modules.catalog
from kernel.audit import AuditLog, InMemoryAuditSink, PostgresAuditSink
from kernel.broker import ExecutionError, ToolBroker
from kernel.config import Settings
from kernel.contracts import ContractRegistry
from kernel.decide import DecisionRegistry, DecisionService
from kernel.interpreter import Interpreter
from kernel.router import Envelope, Router

logger = logging.getLogger("llm-core.gateway")


def _build_audit(settings: Settings) -> AuditLog:
    if settings.audit_backend == "memory" or not settings.database_url:
        return AuditLog(InMemoryAuditSink())
    if settings.audit_backend == "postgres":
        return AuditLog(PostgresAuditSink(settings.database_url))
    try:  # auto: prefer Postgres, degrade to memory
        sink = PostgresAuditSink(settings.database_url)
        sink._connect()
        return AuditLog(sink)
    except Exception as e:  # noqa: BLE001
        logger.warning("Postgres audit sink unavailable (%s); degrading to in-memory audit", e)
        return AuditLog(InMemoryAuditSink())


def create_app(settings: Settings | None = None, audit: AuditLog | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    audit = audit or _build_audit(settings)

    contracts = ContractRegistry(settings.contracts_dir / "openapi" / "openapi.yaml")
    decisions = DecisionService(settings, DecisionRegistry(settings.contracts_dir / "decisions"), audit)
    broker = ToolBroker(contracts, audit)
    modules.catalog.register(broker)
    router = Router(settings, contracts, decisions, audit)
    interpreter = Interpreter(settings, contracts, broker, audit)

    app = FastAPI(title="llm-core kernel", version="0.1.0")
    kernel = SimpleNamespace(
        settings=settings, audit=audit, contracts=contracts, decisions=decisions,
        broker=broker, router=router, interpreter=interpreter,
    )
    app.state.kernel = kernel

    def _principal(request: Request):
        auth = request.headers.get("Authorization", "")
        key = auth.removeprefix("Bearer ").strip()
        if not key:
            raise HTTPException(401, "missing bearer key")
        principal = settings.principal_for_key(key)
        if principal is None:
            raise HTTPException(401, "unknown principal")
        return principal

    @app.post("/v1/request")
    def handle_request(env: Envelope, request: Request):
        principal = _principal(request)
        request_id = str(uuid.uuid4())
        payload_view = {"op": env.payload.get("op"), "has_text": bool(env.payload.get("text")),
                        "args_digest": str(len(str(env.payload.get("args", {}))))}
        audit.append("RECEIVED", request_id, principal.name, {
            "payload_view": payload_view, "capability_hint": env.capability_hint,
            "trust": principal.trust, "roles": list(principal.roles),
        })

        decision = router.route(env, request_id, principal.name)
        if decision.tier == "escalate":
            audit.append("ESCALATED", request_id, principal.name, {"reason": decision.reason})
            return {"request_id": request_id, "tier": "escalate", "status": "escalated",
                    "reason": decision.reason}

        if decision.tier == "t0_deterministic":
            args = env.payload.get("args", {})
            if not isinstance(args, dict):
                audit.append("ESCALATED", request_id, principal.name, {"reason": "args not an object"})
                return {"request_id": request_id, "tier": "t0_deterministic", "status": "escalated",
                        "reason": "args must be an object"}
            try:
                output = broker.execute(env.payload["op"], args, request_id, principal.name)
            except ExecutionError as e:
                audit.append("COMPLETED", request_id, principal.name, {"ok": False, "error": str(e)})
                return {"request_id": request_id, "tier": "t0_deterministic", "status": "error",
                        "error": str(e)}
            audit.append("COMPLETED", request_id, principal.name, {"ok": True, "tier": "t0_deterministic"})
            return {"request_id": request_id, "tier": "t0_deterministic", "status": "completed",
                    "op": env.payload["op"], "result": output}

        # t1_interpreter
        response = interpreter.handle(env, decision, request_id, principal.name)
        response["request_id"] = request_id
        audit.append("COMPLETED", request_id, principal.name,
                     {"ok": response.get("status") == "completed", "tier": "t1_interpreter"})
        return response

    @app.post("/v1/decide")
    def handle_decide(body: dict, request: Request):
        principal = _principal(request)
        decision_id = body.get("decision", "")
        state = body.get("state", {})
        if not decision_id or not isinstance(state, dict):
            raise HTTPException(422, "body must be {decision, state}")
        request_id = str(uuid.uuid4())
        outcome = decisions.decide(decision_id, state, request_id, principal.name)
        return {"request_id": request_id, **outcome.model_dump()}

    @app.get("/specs/openapi.json")
    def spec():
        return contracts.openapi_dict()

    @app.get("/specs/mcp-manifest.json")
    def mcp_manifest():
        return contracts.mcp_manifest()

    @app.get("/v1/audit")
    def audit_records(request: Request, request_id: str | None = None):
        _principal(request)
        records = audit.for_request(request_id) if request_id else audit.all()
        return {"backend": audit.backend, "records": [r.as_dict() for r in records]}

    @app.get("/v1/audit/verify")
    def audit_verify(request: Request):
        _principal(request)
        report = audit.verify()
        return {"backend": audit.backend, **report}

    @app.get("/ui")
    def dashboard():
        from fastapi.responses import HTMLResponse

        from kernel.dashboard import DASHBOARD_HTML

        return HTMLResponse(DASHBOARD_HTML)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "audit_backend": audit.backend,
                "implemented_ops": broker.implemented_ops()}

    return app
