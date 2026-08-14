import unittest
from storage import Storage, Task

class TestStorage(unittest.TestCase):
    def setUp(self):
        self.storage = Storage("test_storage.json")
        self.task_id = 1
        self.task = Task(self.task_id, "test_project", "test_title", "test_description")
        self.storage.create(self.task)

    def tearDown(self):
        self.storage.path.unlink(missing_ok=True)

    def test_update_task(self):
        updated_changes = {"title": "updated_title"}
        updated_task = self.storage.update(self.task_id, updated_changes)
        self.assertEqual(updated_task.title, "updated_title")
        self.assertEqual(updated_task.description, "test_description")

    def test_update_partial_fields(self):
        updated_changes = {"description": "updated_description"}
        updated_task = self.storage.update(self.task_id, updated_changes)
        self.assertEqual(updated_task.title, "test_title")
        self.assertEqual(updated_task.description, "updated_description")

if __name__ == "__main__":
    unittest.main()