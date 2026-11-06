# The bug

`apply_discount(total, pct)` returned `total - pct`: it subtracted the percentage as if it were money, and a
discount over 100% gave a negative total. The fix in `shop/pricing.py` has TWO hunks:

1. `pct = min(max(pct, 0), 100)`: the percentage is clamped to 0..100.
2. `return round(total * (100 - pct) / 100, 2)`: the discount is a percentage of the total.

The fixture tree holds the FIXED code; `run_demo.py` builds the buggy and half-fixed trees by reverting hunks.
