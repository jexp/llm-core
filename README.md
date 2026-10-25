# llm-core

An LLM-native retail system experiment: the application's core is a **decision model + LLM
kernel**, not traditional code. Requests are routed to deterministic handlers (T0), an LLM
interpreter that emits plan-DSL operations bound to versioned contracts (T1), or a background
JIT compiler that profiles request patterns and promotes LLM-generated handlers behind the same
contracts after a validation gauntlet (T2).

- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Paper grounding: [docs/RESEARCH-NOTES.md](docs/RESEARCH-NOTES.md) (AIOS, ACE NDSS'26, Next
  Frontier, LLM-Native ICSE-Comp'26)
- Roadmap: [docs/PLAN.md](docs/PLAN.md) — M0 skeleton (delivered) → M1 single-merchant slice →
  M2 marketplace → M3 hardening

## Architecture at a glance

```
clients ──► GATEWAY (authn, trust, audit chain)
                └─ ROUTER: rules → Jev battery (System One) → LLM fallback → safe default
                     ├─ T0 deterministic handlers behind versioned contracts
                     ├─ T1 interpreter: plan-DSL bound to contract ops (LLM or rules fallback)
                     └─ T2 JIT compiler (M1): profile → codegen → validate → shadow → promote
DECISIONS: kernel.decide() — registered Jev question batteries anywhere in the codebase
           (thresholds, fallbacks, pii/payment egress masking, per-call audit)
AUDIT:     append-only hash-chained log in Postgres; auditor verifies + personas cross-check
```

## Repository layout

```
kernel/      gateway, router, decide service, broker, interpreter, audit, tail CLI, dashboard
contracts/   openapi/ (promoted spec) · decisions/ (Jev battery definitions)
modules/     catalog (M0); identity/orders/payments/campaigns arrive in M1
personas/    profiles/, clients/ (smoke persona; six personas in M1)
infra/       docker-compose.yml (postgres, neo4j, stripe-mock), db-init/
bootstrap/   spec_prompt.md — the textual description the system boots from
docs/        ARCHITECTURE.md · RESEARCH-NOTES.md · PLAN.md
tests/       29 hermetic tests
```

## Learnings so far

**From the papers** (full mapping in [docs/RESEARCH-NOTES.md](docs/RESEARCH-NOTES.md)):
- LLM planning is unreliable (AIOS's own citations) → every model output is typed data behind
  schema validation, invariants are deterministic code, and every decider has a declared fallback.
- ACE's "outputs are pure typed data, never control input" shapes the whole kernel: prompt
  injection degrades to a `TypeError`/safe refusal, never to a different route. Routing decisions
  use trusted inputs only (request + policy + system-written capability descriptions).
- Jev/decision models fit wherever a judgment is needed but prose is not — classification,
  gating, triage — cheaper and more structured than an LLM call; confidence (distribution
  peakedness) drives act/confirm/escalate thresholds scaled by risk.

**From building M0:**
- Degradation must be a first-class outcome, not an exception: the first `decide()` version
  raised when all backends failed, which would have 500'd every natural-language request in a
  no-keys environment. It now returns a typed `escalate` outcome; the gateway never crashes on
  provider outage.
- Contract tests catch data bugs specs can't: the seed catalog had no camping item under $20, so
  the canonical demo query legitimately returned empty. Persona scenarios encode such
  expectations as data, which is exactly what M1's six personas will scale up.
- Hash-chained audit from day one is cheap and changes behavior: because every hop is recorded,
  watching `kernel.tail` during a smoke run *is* the demo of the routing tiers.
- Isolate provider SDKs (pydantic-ai, typesafe-sdk) behind thin adapters — API drift becomes a
  degraded path plus a log line instead of an outage.

## Quickstart (M0)

```bash
cp .env.example .env   # optional: set LLMC_API_KEY / TYPESAFE_API_KEY for live LLM/Jev paths
make up                # postgres, neo4j, stripe-mock (docker)
make run               # kernel server on :8000 (Postgres audit chain)
```

No keys and no docker? `LLMC_AUDIT_BACKEND=memory uv run uvicorn kernel.server:app` runs fully
standalone in degraded mode.

## Using it

| URL / command | What it is |
|---|---|
| `http://localhost:8000/ui` | **Live dashboard**: audit-chain feed, tier routing, request composer |
| `http://localhost:8000/docs` | FastAPI Swagger UI for every gateway endpoint |
| `make smoke` | Run the smoke persona (contract conformance + audit checks) |
| `make verify` | Auditor CLI: verify the hash chain |
| `uv run python -m kernel.tail` | `tail -f` the audit chain in your terminal |

Dev principal keys (see `kernel/principals.json`): `dev-smoke-key` (customer),
`dev-operator-key` (staff), `dev-auditor-key` (auditor). Send `Authorization: Bearer <key>`.

```bash
# structured call → T0
curl -s localhost:8000/v1/request -H "Authorization: Bearer dev-smoke-key" \
  -H "Content-Type: application/json" \
  -d '{"payload": {"op": "catalog.list", "args": {"max_price": 20}}}'

# natural language → router → T1 interpreter (plan recorded in audit chain)
curl -s localhost:8000/v1/request -H "Authorization: Bearer dev-smoke-key" \
  -H "Content-Type: application/json" \
  -d '{"payload": {"text": "what do you have under $20 for camping?"}}'

# dynamic decision (Jev battery; degrades to rules defaults without a key)
curl -s localhost:8000/v1/decide -H "Authorization: Bearer dev-smoke-key" \
  -H "Content-Type: application/json" \
  -d '{"decision": "demo.classify", "state": {"message": "where is my order?"}}'
```

## Tests

```bash
make test        # uv run pytest -q — 29 hermetic tests (no docker, no keys; degraded paths)
make test-watch  # rerun automatically whenever kernel/ modules/ tests/ contracts/ change
make lint        # uv run ruff check .
make smoke       # live persona run against a running server (BASE_URL=... to override)
```

Filtering/verbosity: `uv run pytest -q -k router` (subset), `--tb=short` (compact tracebacks),
`-s` (stdout passthrough).

### Watching the live tests

The most instructive setup is three terminals:

1. `make run` — kernel server.
2. `uv run python -m kernel.tail` — live audit feed (or open `http://localhost:8000/ui` in a
   browser: chain badge + color-coded event feed + request composer).
3. `make smoke` — persona run; each check prints `[PASS]`/`[FAIL]` and you watch the same
   requests traverse RECEIVED → DECIDED → PLAN → OP_RESULT → COMPLETED in the feed.

The persona also writes its own request log to `data/client_logs/smoke.jsonl` — the client-side
artifact the auditor cross-checks against the server chain (`make verify`).

The system is spec-first: it boots from [bootstrap/spec_prompt.md](bootstrap/spec_prompt.md),
promotes an OpenAPI spec before implementations exist, and fills endpoints in as requests arrive.
