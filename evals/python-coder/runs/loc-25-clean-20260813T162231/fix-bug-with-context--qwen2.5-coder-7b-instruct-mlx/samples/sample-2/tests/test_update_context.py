from update_context import update_task_context
import pytest

@pytest.mark.parametrize("task_id, context, expected", [(1, {"key": "value"}, {"key": "value"})])
def test_update_task_context(task_id, context, expected):
    updated_context = update_task_context(task_id, context)
    assert updated_context == expected