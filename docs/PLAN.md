# Delivery plan

Milestones map to `docs/ARCHITECTURE.md`. Each has acceptance criteria — a milestone is done when
its persona/judge/auditor runs are green, not when the code exists.

## M0 — Skeleton: kernel loop, no retail depth

**Status: delivered (2026-10-25)** — 29 unit tests green, ruff clean; live acceptance passed:
`make up` + kernel server + smoke persona GREEN (Postgres hash-chained audit verified over the
full RECEIVED→DECIDED→PLAN→OP_RESULT→COMPLETED lifecycle; natural-language request served by T1
with plan recorded). Jev/LLM paths exercised in degraded mode (no keys in dev environment);
set `TYPESAFE_API_KEY` / `LLMC_*` to activate them.

**Scope**
- Repo scaffold per the architecture layout; docker-compose (Postgres, Neo4j, stripe-mock).- Gateway: request envelope, static principal authn (personas/keys), append-only **hash-chained
  audit log** in Postgres.
- Contract registry v0: `contracts/` as source of truth, semver per operation, `/specs/openapi.json`
  + MCP manifest generation.
- Decision router: **rules → Jev decider → LLM fallback** chain behind one internal contract.
  M0 ships rules + the Jev integration (routing question battery v1 via `typesafe-sdk`,
  confidence-gated tiers) with the LLM decider as the degraded path when `TYPESAFE_API_KEY` is
  unset. Known op → T0, unknown → T1.
- **Decision service**: `kernel.decide()` + decision registry (definitions in
  `contracts/decisions/`, semver, thresholds, fallbacks, data labels, caching) + per-call audit;
  seed definitions `router.route@1` and `demo.classify@1`.
- T0: two seeded ops (`health`, `catalog.list` with a few seeded products).
- T1: Pydantic AI interpreter wired to `LLMC_*` env — emits plan DSL, executes via tool broker,
  schema-validates every output.
- Auditor job: hash-chain verification.
- One smoke persona client (API): calls `catalog.list`, asserts contract conformance, verifies
  its own request appears in the audit chain.

**Acceptance**: `make up` brings the stack up; smoke persona green; a natural-language request
("what costs under $20?") is served end-to-end by T1 with its plan recorded in the audit log.

## M1 — Single-merchant slice (locked milestone)

**Scope**
- Modules: identity, catalog/inventory, orders/checkout, payments (stripe-mock), campaigns/policy,
  warehouse.
- Invariants 1–7 (see architecture) enforced pre-commit at the tool broker.
- **Full JIT ladder**: profiler → codegen → static gate (IFC-lite) → judge → shadow → promote/
  rollback; natural-language product search is the first promotion candidate.
- Chat agent: negotiation within policy envelope (typed outcomes only).
- Persona harness: 6 personas (bargain hunter, negotiator, browser, bulk buyer, red-team
  fraudster, returns customer), scenario DSL, judges, client-side request logs.
- Router evaluation: shadow-compare Jev decider vs LLM decider on recorded traffic; tune
  confidence thresholds per risk_class from the observed calibration (Typesafe three-range
  pattern).
- Registered decisions in live use (via `kernel.decide()`): `orders.fraud_score@1` (Score —
  manual review above cutoff), `campaigns.eligibility@1` (Noul — within campaign scope),
  `catalog.categorize@1` (Choice on free-text descriptions), `chat.preclassify@1` (Choice),
  `auditor.anomaly_triage@1` (Score). Egress masking of `pii`/`payment` state fields enforced
  per decision `data_labels`.
- Auditor: cross-check audit chain vs client logs; recompute trust scores.
- DuckDB analytics over the event log (tier distribution, JIT stats); Neo4j product–category
  graph with a first recommendation query.
- Bootstrap: `bootstrap/spec_prompt.md` → spec-draft agent → OpenAPI v0.1.0 promoted and published.

**Acceptance**: happy path end-to-end; every persona scenario passes; fraudster scenarios produce
zero invariant violations (logged safe refusals); one op JIT-promoted with shadow evidence; auditor
report clean.

## M2 — Marketplace

Seller onboarding as a **contract-extension event**; per-seller catalog/stock/campaigns/policy
scoping; seller-scoped invariants; ACE pairwise-independent capability matching (seller
descriptions are data, never router inputs); seller trust scores; seller–product graph edges;
personas: seller-side operator, marketplace comparison shopper.

## M3 — Hardening & polish

Adversarial campaign runner (drift → demotion → rollback drills); negotiation benchmark;
DuckDB-backed dashboards; **model-swap demo** (swap router/interpreter/judge models behind their
contracts live) to prove replaceability.

## Working agreements

- Python 3.12+; `uv` for env/deps; ruff + pytest; strict typing on anything in `contracts/`.
- All state changes flow through the kernel broker — no direct module-to-module calls.
- `contracts/` is the single source of truth; every implementation (handwritten or JIT) conforms
  to it and is regression-tested against its exemplars.
- Audit log is append-only; auditors only read; persona clients keep their own request logs for
  cross-checking.
