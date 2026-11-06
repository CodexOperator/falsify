# The honest test: it calls the real code, so it fails if either hunk of the fix is reverted.
from shop.cart import checkout


def test_ten_percent_off():
    assert checkout([(100.0, 1), (50.0, 2)], 10) == 180.0


def test_a_discount_over_100_percent_clamps_to_zero():
    assert checkout([(40.0, 1)], 150) == 0.0
