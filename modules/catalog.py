"""Aurora Outfitters seeded catalog (M0). T0 handlers registered with the tool broker."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kernel.broker import ToolBroker

PRODUCTS = [
    {"id": "AO-001", "name": "Trailhead 2P Tent", "category": "camping", "price": 189.0,
     "stock": 14, "description": "Two-person, three-season tent with dual vestibules."},
    {"id": "AO-002", "name": "Ridgeline 30L Pack", "category": "hiking", "price": 119.0,
     "stock": 31, "description": "Lightweight daypack with hydration sleeve."},
    {"id": "AO-003", "name": "Camp Stove Micro", "category": "camping", "price": 39.5,
     "stock": 52, "description": "Compact canister stove, boils 1L in 3 minutes."},
    {"id": "AO-004", "name": "Summit Belay Kit", "category": "climbing", "price": 149.0,
     "stock": 8, "description": "Belay device, carabiners, and sling package."},
    {"id": "AO-005", "name": "Merino Base Layer", "category": "apparel", "price": 64.0,
     "stock": 44, "description": "Midweight merino wool top for cool conditions."},
    {"id": "AO-006", "name": "Trailhead Tarp 3x3", "category": "camping", "price": 49.0,
     "stock": 23, "description": "Versatile nylon tarp with guyline kit."},
    {"id": "AO-007", "name": "Scree Hiking Poles", "category": "hiking", "price": 17.5,
     "stock": 60, "description": "Aluminum three-section poles with cork grips."},
    {"id": "AO-008", "name": "Chalk Bag Pro", "category": "climbing", "price": 22.0,
     "stock": 37, "description": "Fleece-lined chalk bag with brush holder."},
    {"id": "AO-009", "name": "Downliner Jacket", "category": "apparel", "price": 210.0,
     "stock": 12, "description": "650-fill down jacket, packs into its own pocket."},
    {"id": "AO-010", "name": "Lantern Orb 200", "category": "camping", "price": 28.0,
     "stock": 41, "description": "Collapsible battery lantern, warm and cool modes."},
    {"id": "AO-011", "name": "Ridge Running Vest", "category": "hiking", "price": 89.0,
     "stock": 19, "description": "Race vest with four front pockets and pole quiver."},
    {"id": "AO-012", "name": "Crash Pad Compact", "category": "climbing", "price": 249.0,
     "stock": 6, "description": "Bouldering crash pad with taco hinge."},
    {"id": "AO-013", "name": "Tent Stake Set", "category": "camping", "price": 9.0,
     "stock": 75, "description": "Aluminum Y-stakes, set of eight."},
    {"id": "AO-014", "name": "Firestarter Kit", "category": "camping", "price": 12.5,
     "stock": 58, "description": "Ferro rod, tinder, and waterproof matches."},
]


def health(args: dict) -> dict:
    return {"status": "ok", "service": "llm-core", "version": "0.1.0"}


def catalog_list(args: dict) -> dict:
    items = PRODUCTS
    if category := args.get("category"):
        items = [p for p in items if p["category"] == category]
    if (max_price := args.get("max_price")) is not None:
        items = [p for p in items if p["price"] <= float(max_price)]
    limit = int(args.get("limit", 10))
    items = items[:limit]
    return {"items": [dict(p) for p in items], "count": len(items)}


def register(broker: ToolBroker) -> None:
    broker.register("health", health)
    broker.register("catalog.list", catalog_list)
