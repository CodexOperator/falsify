from shop.pricing import apply_discount


def checkout(items, pct):
    """items = [(unit price, quantity)]; the discounted total."""
    total = sum(price * qty for price, qty in items)
    return apply_discount(total, pct)
