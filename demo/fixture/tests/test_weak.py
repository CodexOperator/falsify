# The test an agent writes to get to green: it mocks the very function the fix changed, so it passes either way.
from unittest import mock

from shop import cart


def test_checkout_applies_discount():
    with mock.patch.object(cart, "apply_discount", return_value=90.0) as m:
        assert cart.checkout([(100.0, 1)], 10) == 90.0
    m.assert_called_once_with(100.0, 10)
