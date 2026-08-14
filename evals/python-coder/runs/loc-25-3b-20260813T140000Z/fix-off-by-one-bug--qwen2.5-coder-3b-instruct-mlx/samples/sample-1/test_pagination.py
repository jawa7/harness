import pytest

from pagination import page


def test_pages_are_one_based():
    assert page(["a", "b", "c", "d", "e"], 1, 2) == ["a", "b"]
    assert page(["a", "b", "c", "d", "e"], 2, 2) == ["c", "d"]
    assert page(["a", "b", "c", "d", "e"], 3, 2) == ["e"]


def test_rejects_invalid_pagination_arguments():
    with pytest.raises(ValueError):
        page([], 0, 1)
    with pytest.raises(ValueError):
        page([], 1, 0)
