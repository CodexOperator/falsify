def apply_discount(total, pct):
    """The total after a pct% discount, never below zero."""
    pct = min(max(pct, 0), 100)
    # the discount is a share of the total, not an amount
    return round(total * (100 - pct) / 100, 2)
