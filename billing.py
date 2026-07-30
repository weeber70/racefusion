"""
billing.py — pure Stripe-subscription interpretation logic (no Streamlit, no
network). Extracted so the add-on math is unit-testable and app.py stays thin.

A subscription is ONE Stripe subscription with multiple line items:
  • exactly one base-tier price (racer | pro)
  • optional "Additional Car"  price, quantity = extra cars beyond the included 1
  • optional "Additional User" price, quantity = crew members
"""


def resolve_subscription_items(items, tier_map: dict,
                               addl_car_price: str = "",
                               addl_user_price: str = ""):
    """Scan ALL line items of a subscription (never just the first).

    items: iterable of objects/dicts with .price.id (or ["price"]["id"]) and
           .quantity. Works with Stripe SDK objects and plain dicts.
    Returns (tier | None, car_slots, crew_slots).
      tier is None when no recognizable base-tier item exists — callers must
      treat that as "no subscription", NOT default to a tier.
      Unknown prices are ignored entirely.
    """
    tier = None
    extra_cars = 0
    extra_users = 0
    for it in items or []:
        try:
            price = it["price"] if isinstance(it, dict) else it.price
            pid = price["id"] if isinstance(price, dict) else price.id
            qty = (it.get("quantity") if isinstance(it, dict)
                   else getattr(it, "quantity", None)) or 1
        except (KeyError, AttributeError, TypeError):
            continue
        if pid in tier_map:
            tier = tier_map[pid]
        elif addl_car_price and pid == addl_car_price:
            extra_cars += int(qty)
        elif addl_user_price and pid == addl_user_price:
            extra_users += int(qty)
        # anything else: ignore — never default a tier from an unknown price
    return tier, 1 + extra_cars, extra_users


def build_addon_item_updates(existing_items, want: dict):
    """Diff current subscription items against desired add-on quantities.

    existing_items: iterable with .id, .price.id, .quantity (or dict forms).
    want: {price_id: desired_quantity} for add-on prices only.
    Returns the Stripe Subscription.modify(items=[...]) parameter list:
      new item      → {"price": pid, "quantity": q}
      changed qty   → {"id": item_id, "quantity": q}
      removed (q=0) → {"id": item_id, "deleted": True}
      unchanged     → omitted
    Base-tier items are never touched (they're not in `want`).
    """
    existing = {}
    for it in existing_items or []:
        try:
            price = it["price"] if isinstance(it, dict) else it.price
            pid = price["id"] if isinstance(price, dict) else price.id
            iid = it["id"] if isinstance(it, dict) else it.id
            qty = (it.get("quantity") if isinstance(it, dict)
                   else getattr(it, "quantity", None)) or 1
        except (KeyError, AttributeError, TypeError):
            continue
        existing[pid] = (iid, int(qty))

    updates = []
    for pid, q in (want or {}).items():
        q = max(0, int(q))
        cur = existing.get(pid)
        if cur is None:
            if q > 0:
                updates.append({"price": pid, "quantity": q})
        else:
            iid, cur_q = cur
            if q == 0:
                updates.append({"id": iid, "deleted": True})
            elif q != cur_q:
                updates.append({"id": iid, "quantity": q})
    return updates
