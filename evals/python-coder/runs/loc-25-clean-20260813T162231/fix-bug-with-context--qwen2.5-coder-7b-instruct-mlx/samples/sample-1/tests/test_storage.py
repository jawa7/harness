import pytest
from storage import Storage, Task


def test_storage_creation():
    storage = Storage('test.db')
    assert isinstance(storage, Storage)


def test_storage_methods():
    storage = Storage('test.db')
    # Add tests for the methods of Storage here
    pass