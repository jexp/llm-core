# llm-core

An LLM-native retail system experiment: the application's core is a **decision model + LLM
kernel**, not traditional code. Requests are routed to deterministic handlers (T0), an LLM
interpreter that emits plan-DSL operations bound to versioned contracts (T1), or a background
JIT compiler that profiles request patterns and promotes LLM-generated handlers behind the same
contracts after a validation gauntlet (T2).

- Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Paper grounding: [docs/RESEARCH-NOTES.md](docs/RESEARCH-NOTES.md) (AIOS, ACE NDSS'26, Next
  Frontier, LLM-Native ICSE-Comp'26)
- Roadmap: [docs/PLAN.md](docs/PLAN.md) — M0 skeleton → M1 single-merchant slice → M2
  marketplace → M3 hardening

## Quickstart (M0, once implemented)

```bash
cp .env.example .env   # set LLMC_API_KEY / LLMC_BASE_URL
make up                # postgres, neo4j, stripe-mock
make install && make test
```

The system is spec-first: it boots from [bootstrap/spec_prompt.md](bootstrap/spec_prompt.md),
promotes an OpenAPI spec before implementations exist, and fills endpoints in as requests arrive.
