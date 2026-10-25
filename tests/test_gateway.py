from pathlib import Path

from fastapi.testclient import TestClient

from kernel.audit import AuditLog, InMemoryAuditSink
from kernel.config import Principal, Settings
from kernel.gateway import create_app

REPO = Path(__file__).resolve().parents[1]
HEADERS = {"Authorization": "Bearer test-key"}


def make_client() -> TestClient:
    settings = Settings(
        llmc_base_url="", llmc_api_key="", typesafe_api_key="",
        database_url="", audit_backend="memory",
        contracts_dir=REPO / "contracts",
        principals=(Principal(name="tester", key="test-key", roles=("customer",)),),
    )
    app = create_app(settings=settings, audit=AuditLog(InMemoryAuditSink()))
    return TestClient(app)


def test_healthz_reports_audit_backend_and_ops():
    client = make_client()
    body = client.get("/healthz").json()
    assert body["status"] == "ok" and body["audit_backend"] == "memory"
    assert "catalog.list" in body["implemented_ops"]


def test_specs_promoted():
    client = make_client()
    spec = client.get("/specs/openapi.json").json()
    assert "paths" in spec and "/ops/catalog.list" in spec["paths"]
    manifest = client.get("/specs/mcp-manifest.json").json()
    assert any(t["name"] == "catalog.list" for t in manifest["tools"])


def test_t0_flow_audited_end_to_end():
    client = make_client()
    resp = client.post("/v1/request", headers=HEADERS,
                       json={"payload": {"op": "catalog.list", "args": {"max_price": 20}}})
    body = resp.json()
    assert resp.status_code == 200 and body["tier"] == "t0_deterministic"
    assert body["result"]["count"] > 0
    events = [r["event"] for r in client.get("/v1/audit",
                                             headers=HEADERS,
                                             params={"request_id": body["request_id"]}).json()["records"]]
    assert events == ["RECEIVED", "DECIDED", "OP_RESULT", "COMPLETED"]


def test_t1_nl_flow_records_plan():
    client = make_client()
    resp = client.post("/v1/request", headers=HEADERS,
                       json={"payload": {"text": "what do you have under $20 for camping?"}})
    body = resp.json()
    assert body["tier"] == "t1_interpreter" and body["status"] == "completed"
    assert body["plan"]["ops"][0]["op"] == "catalog.list"
    assert client.get("/v1/audit/verify", headers=HEADERS).json()["ok"] is True


def test_decide_endpoint_degrades_to_rules_without_keys():
    client = make_client()
    resp = client.post("/v1/decide", headers=HEADERS,
                       json={"decision": "demo.classify", "state": {"message": "where is my order?"}})
    body = resp.json()
    assert resp.status_code == 200
    assert body["backend"] == "rules" and body["degraded"] is True
    assert body["answers"]["tone"]["value"] == "calm"


def test_unauthenticated_requests_rejected():
    client = make_client()
    assert client.post("/v1/request", json={"payload": {"op": "health"}}).status_code == 401
    assert client.get("/v1/audit").status_code == 401


def test_t0_bad_args_return_typed_error():
    client = make_client()
    resp = client.post("/v1/request", headers=HEADERS,
                       json={"payload": {"op": "catalog.list", "args": {"category": "kayaking"}}})
    body = resp.json()
    assert resp.status_code == 200 and body["status"] == "error"
    assert "invalid args" in body["error"]
