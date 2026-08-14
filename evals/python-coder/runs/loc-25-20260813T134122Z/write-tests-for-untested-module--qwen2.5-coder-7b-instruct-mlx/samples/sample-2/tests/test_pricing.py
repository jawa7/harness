# tests/test_pricing.py

import pytest
from pricing import final_price

def test_final_price_negative_cents():
    with pytest.raises(ValueError):
        final_price(-10)

def test_final_price_no_discounts():
    assert final_price(100) == 100

def test_final_price_member_discount():
    assert final_price(100, member=True) == 90

def test_final_price_save5_coupon():
    assert final_price(100, coupon="SAVE5") == 95

def test_final_price_unknown_coupon():
    with pytest.raises(ValueError):
        final_price(100, coupon="UNKNOWN")