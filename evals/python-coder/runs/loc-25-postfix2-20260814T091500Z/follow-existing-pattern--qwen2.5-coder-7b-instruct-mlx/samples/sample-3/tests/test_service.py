import unittest
from service import TaskService, NotFoundError
from models import Task

class TestTaskService(unittest.TestCase):
    def setUp(self):
        self.storage = {
            1: Task(1, 101, 'Task 1'),
            2: Task(2, 101, 'Task 2')
        }
        self.service = TaskService(self.storage)

    def test_archive_task(self):
        updated_task = self.service.archive_task(1)
        self.assertTrue(updated_task.archived)
        with self.assertRaises(NotFoundError):
            self.service.archive_task(3)

if __name__ == '__main__':
    unittest.main()