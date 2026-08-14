def final_price(cents, member=False, coupon=None):
    if cents < 0:
        raise ValueError("cents must not be negative")
    discount = 0
    if member:
        discount += 10
    if coupon == "SAVE5":
        discount += 5
    elif coupon is not None:
        raise ValueError("unknown coupon")
    return cents * (100 - discount) // 100
