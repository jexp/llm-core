# Research notes — how the four papers shaped this architecture

Full texts in `refs/`. Digests below are design-oriented, not exhaustive.

## 1. AIOS — "LLM as OS, Agents as Apps" (Ge et al., Rutgers, arXiv:2312.03815, 2023)

Vision paper (no measurements): LLM as kernel, agents as applications, context window as memory,
tools as devices/libraries.

**Adopted:**
- **"LLM as Interpreter" of a plan DSL** → our T1 interpreter emits plans whose ops are contract
  registry operations; the DSL is the audit artifact and the JIT compilation target.
- **Self-made tools** → JIT-compiled handlers register as first-class tools behind the same
  Tool-API; interpreter traces seed codegen.
- **Two-element Tool-API (scenario + invocation)** → every capability publishes a machine
  contract *and* a system-written scenario description used by the router.
- **Agent profiles for personas** → personas as declarative, versioned artifacts.
- **Memory tiering** → context = hot memory; Postgres = long-term; DuckDB/Neo4j = per-purpose
  "file formats"; SQL over context-stuffing for invariant checks.

**Warnings honored:** LLM planning unreliable (invariants + deterministic fallback mandatory);
NL artifacts resist static analysis (audit over structured plans, not transcripts); NL apps
resist security scanning (red-team personas before any payment side effects, even mocked).

## 2. ACE — security architecture for LLM-integrated app systems (Li, Mallick, Rose et al.,
Northeastern, NDSS 2026)

Splits LLM processing into trusted-only abstract planning, verified concrete planning, and
isolated rule-based execution where app outputs are pure typed data. InjecAgent: 100% security,
utility ≥84% (GPT-4o-class planners). Overhead 11–48s/query, dominated by container spin-up →
validation must be lean and largely offline/async in our design.

**Adopted:** trusted-only routing; pairwise-independent tool matching (load-bearing for the M2
marketplace); outputs-as-typed-data at a single enforcement point; static information-flow
labels (PII/payment/order) in the codegen gate; sandboxed least-privilege execution of generated
code with brokered, schema-checked calls.

**Warnings honored:** planner manipulation via app/tool descriptions; confused-deputy and
execution-hijack via injected content; availability attacks (bounded retries, per-plan budgets);
"model-level defenses are not enough" (we keep structural enforcement); sandboxes can't prove
worker state (we audit execution traces independently).

## 3. Next Frontier — open ecosystems & hardware synergy (Hou, Zhao, Wang, HUST,
arXiv:2503.04596, 2025)

Vision: three-layer decoupling (application / protocol / hardware), adaptive local-remote model
routing, open agent ecosystems (MCP/ANP/IoA — all flagged early-stage).

**Adopted:** adaptive model routing per request on cost/latency/capability (our router + tiered
models); models swappable behind standardized contracts (env-driven OpenAI-compatible gateway);
promoting API specs as MCP to external agent ecosystems (spec-first bootstrap); zero-trust caller
identity with historical trust scores computed from audit data.

**Notable gap we fill:** the paper has *no mechanism* for JIT-style evolution or spec promotion —
our profiler→codegen→shadow→promote ladder is the concrete answer.

**Warnings honored:** dynamically installed/updated code (our promoted handlers!) needs
integrity verification — hence the static gate, judge, shadow phase, and instant rollback;
privacy/data-leakage concerns with offloading; fuzzing/static analysis poorly suited to AI
threats → continuous adversarial personas.

## 4. LLM-Native Applications (Bandeira et al., UECE/Unifor, ICSE-Companion 2026,
DOI 10.1145/3774748.3795680)

Defines the paradigm: LLM at the center from day one (vs LLM-based / LLM-augmented). Reference
architecture: LLM Core, Governance & Safety, Application Orchestration, KB, Tool & Service layer,
Context & Memory Manager; plans are versioned and human-inspectable; lifecycle table prescribes
validators-as-code, event sourcing/provenance, hook-based testing.

**Adopted:** the whole framing (kernel as organizing principle; modules serve orchestration);
versioned replayable plans; validators as code; append-only event log as first-class
infrastructure (also feeds DuckDB analytics); governance as a distinct plane with pre- and
post-execution validation.

## External platform: Typesafe AI / Jev (docs.typesafe.ai, reviewed 2026-10)

Jev is a *decision model* on the System One API (`POST /v1/systemone`, SDK `typesafe-sdk`): one
call evaluates a state against typed questions — `Choice` (pick from options), `Score` (ordered
rubric level), `Noul` (yes/no probability) — returning typed values, full `probabilities`, and
`confidence` (distribution peakedness: 1 = all mass on one outcome, 0 = uniform). It matches the
kernel's needs exactly where an LLM is overkill: the router's tier/intent/risk battery, promotion
go/no-go gates, invariant-failure triage, profiler signature merging, and chat pre-classification.
Generalized per user direction: an embeddable `kernel.decide()` service lets **any** module
request classification/routing/scoring/boolean judgments on dynamic input through registered,
versioned decision definitions (thresholds, fallbacks, data labels, audit) — the system-wide
primitive for flexibility without hardcoding heuristics. Confidence gating follows their
three-range pattern with risk-scaled floors (>0.5 read-only, >0.7 state-changing, >0.9
payment/promotion; Noul yes ≥0.7 / no ≤0.3). Their own composition advice mirrors ACE/AIOS
principles we already hold: atomic well-scoped questions, compose in code, state as typed JSON,
outputs your code can branch on — never prose in control position.

## Orchestration-framework survey (web, 2026-10)

- **Pydantic AI (chosen):** library not runtime → kernel owns the routing loop; Pydantic-native
  typed agents/tools; `OpenAIModel(base_url=...)` covers GLM/OpenAI/Ollama; MCP client+server;
  optional durable execution (Temporal/DBOS/…) and Docker code sandbox. MIT, very active (V2
  stable Jun 2026).
- **OpenAI Agents SDK (fallback):** light, clean handoffs; MCP serving and sandboxing bolted on;
  hosted-tools features are OpenAI-infra-only.
- **LangGraph:** best checkpointed state machines, but imposes a graph executor and had a Jun
  2026 RCE advisory via checkpointer deserialization.
- **"PI"** = badlogic's `pi` (TypeScript coding-agent harness) and **"AGY"** = AG2 (live AutoGen
  fork; upstream AutoGen archived Oct 2025) — both wrong shape for an embeddable kernel.
- Mastra is TS-only with commercial extras; smolagents lacks durable state; CrewAI/Agno impose
  their own multi-agent metaphors.
