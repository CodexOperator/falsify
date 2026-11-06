def apply_discount(total, pct):
    """The total after a pct% discount, never below zero."""
    pct = min(max(pct, 0), 100)
    return round(total * (100 - pct) / 100, 2)
