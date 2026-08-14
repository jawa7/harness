import pytest

from pricing import final_price

def test_final_price_negative_cents():
    with pytest.raises(ValueError):
        final_price(-1)

def test_final_price_no_discount():
    assert final_price(100) == 100
    assert final_price(200) == 200

def test_final_price_member_discount():
    assert final_price(100, member=True) == 90
    assert final_price(200, member=True) == 180

def test_final_price_save5_coupon():
    assert final_price(100, coupon="SAVE5") == 95
    assert final_price(200, coupon="SAVE5") == 190

def test_final_price_unknown_coupon():
    with pytest.raises(ValueError):
        final_price(100, coupon="UNKNOWN")