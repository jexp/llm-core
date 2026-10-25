# Shop spec prompt (system bootstrap input)

You are booting "Aurora Outfitters", a single-merchant outdoor-gear web shop, as an LLM-native
system. From this description alone, draft the initial promoted API spec.

## Shop
- Catalog: ~200 SKUs across camping, hiking, climbing, apparel; each product has price, stock,
  category, and a free-text description written by staff.
- Customers: accounts with email, addresses, payment methods (mock gateway only).
- Orders: cart → checkout → payment (mock Stripe) → stock reservation → shipment; returns within
  30 days for paid orders.
- Campaigns: percentage/fixed discounts and bundles, each with an active window, eligible
  categories, stacking rules, and a maximum discount envelope.
- Chat: customers (or their agents) may negotiate price/discount and ask support questions;
  agreements are only valid inside the active campaign's discount envelope and must be recorded.
- Staff: operators manage catalog, stock, and campaigns; auditors read everything but change nothing.

## Requirements for the draft spec
1. OpenAPI 3.1 operations covering the above, each with JSON Schema request/response types.
2. A list of invariants (see docs/ARCHITECTURE.md for the seed set) with, per state-changing
   operation, which invariants apply.
3. Data-class labels per field: public | pii | payment | order.
4. Suggested personas for the test harness.

The spec is promoted and published to clients BEFORE implementations exist. Implementations are
filled in by the kernel: interpreter (T1) first, JIT-compiled handlers (T2) as traffic justifies
them.
