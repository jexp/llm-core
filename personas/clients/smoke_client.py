"""Smoke persona client: contract conformance, router tiers, audit chain.

Runs live against a kernel at --base-url (default http://localhost:8000), keeps its own
client-side request log (data/client_logs/smoke.jsonl), and exits non-zero on any failure —
the auditor later cross-checks these logs against the server chain.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
CATEGORIES = ("camping", "hiking", "climbing", "apparel")


class SmokeRun:
    def __init__(self, base_url: str, key: str) -> None:
        self.http = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {key}"}, timeout=30)
        self.log_path = REPO_ROOT / "data" / "client_logs" / "smoke.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.failures: list[str] = []

    def _log(self, kind: str, payload: dict) -> None:
        with self.log_path.open("a") as fh:
            fh.write(json.dumps({"ts": time.time(), "kind": kind, **payload}, default=str) + "\n")

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            self.failures.append(label)

    def run(self) -> int:
        spec = self.http.get("/specs/openapi.json").json()
        op_ids = {
            op["operationId"]
            for methods in spec.get("paths", {}).values()
            for op in methods.values()
            if isinstance(op, dict) and "operationId" in op
        }
        self.check("catalog.list" in op_ids and "health" in op_ids, "spec promoted with contract ops")

        manifest = self.http.get("/specs/mcp-manifest.json").json()
        tools = {t["name"] for t in manifest.get("tools", [])}
        self.check("catalog.list" in tools, "mcp manifest generated from same source of truth")

        # --- T0: structured call --------------------------------------------------
        t0 = self.http.post("/v1/request", json={"payload": {"op": "catalog.list",
                                                             "args": {"max_price": 20}}})
        self._log("t0_request", {"status": t0.status_code, "body": t0.json()})
        t0_body = t0.json()
        self.check(t0.status_code == 200 and t0_body.get("tier") == "t0_deterministic",
                   "T0 tier for exact op match")
        result = t0_body.get("result", {})
        prices_ok = all(item["price"] <= 20 for item in result.get("items", []))
        self.check(result.get("count", 0) > 0 and prices_ok, "T0 filter honored (max_price<=20)")

        resp_schema = _resolve(spec, "#/components/schemas/CatalogListResponse")
        schema_errors = list(Draft202012Validator(resp_schema).iter_errors(result))
        self.check(not schema_errors, "T0 output conforms to promoted contract",
                   "; ".join(e.message for e in schema_errors[:3]))

        # --- T1: natural language -------------------------------------------------
        text = "what do you have under $20 for camping?"
        t1 = self.http.post("/v1/request", json={"payload": {"text": text}})
        t1_body = t1.json()
        self._log("t1_request", {"status": t1.status_code, "body": t1_body})
        self.check(t1.status_code == 200 and t1_body.get("tier") == "t1_interpreter",
                   "T1 tier for natural-language request")
        plan = t1_body.get("plan", {})
        plan_ok = plan.get("ops") and plan["ops"][0].get("op") == "catalog.list"
        self.check(bool(plan_ok), "T1 plan recorded (plan-DSL op bound to contract)")
        items = (t1_body.get("results") or [{}])[0].get("output", {}).get("items", [])
        self.check(bool(items) and all(i["price"] <= 20 and i["category"] == "camping" for i in items),
                   "T1 results honor parsed constraints (<=20, camping)")

        # --- Audit chain ----------------------------------------------------------
        rid = t1_body.get("request_id", "")
        audit = self.http.get("/v1/audit", params={"request_id": rid}).json()
        events = {r["event"] for r in audit.get("records", [])}
        need = {"RECEIVED", "DECIDED", "PLAN", "OP_RESULT", "COMPLETED"}
        self.check(need <= events, "audit trail covers full lifecycle",
                   f"missing: {sorted(need - events)}")
        verify = self.http.get("/v1/audit/verify").json()
        self.check(verify.get("ok") is True, "audit hash chain verified",
                   f"backend={verify.get('backend')} checked={verify.get('checked')}")

        print(f"\nclient log: {self.log_path}")
        if self.failures:
            print(f"FAILED: {len(self.failures)} check(s): {self.failures}")
            return 1
        print("SMOKE PERSONA GREEN")
        return 0


def _resolve(spec: dict, ref: str) -> dict:
    node = spec
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    schemas = spec.get("components", {}).get("schemas", {})

    def walk(n):
        if isinstance(n, dict):
            if set(n) == {"$ref"}:
                return walk(schemas[n["$ref"].rsplit("/", 1)[-1]])
            return {k: walk(v) for k, v in n.items()}
        if isinstance(n, list):
            return [walk(v) for v in n]
        return n

    return walk(node)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--key", default="dev-smoke-key")
    args = parser.parse_args()
    return SmokeRun(args.base_url, args.key).run()


if __name__ == "__main__":
    sys.exit(main())
