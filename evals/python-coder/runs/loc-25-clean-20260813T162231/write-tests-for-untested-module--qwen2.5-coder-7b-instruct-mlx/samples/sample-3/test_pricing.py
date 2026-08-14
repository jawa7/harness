import pytest
from pricing import final_price

def test_negative_cents():
    with pytest.raises(ValueError):
        final_price(-1)

def test_member_discount():
    assert final_price(100, member=True) == 90

def test_valid_coupon():
    assert final_price(100, coupon="SAVE5") == 95

def test_invalid_coupon():
    with pytest.raises(ValueError):
        final_price(100, coupon="SAVE10")