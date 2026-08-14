from service import TaskService, NotFoundError

class MockStorage:
    def __init__(self):
        self.tasks = {}

    def create(self, task):
        task.id = len(self.tasks) + 1
        self.tasks[task.id] = task
        return task

    def get(self, task_id):
        return self.tasks.get(task_id)

    def update(self, task_id, updates):
        if task_id in self.tasks:
            self.tasks[task_id].__dict__.update(updates)
            return self.tasks[task_id]
        raise NotFoundError(f"Task with id {task_id} not found")

# Create a mock storage instance
storage = MockStorage()

# Create a task service with the mock storage
task_service = TaskService(storage)

# Create a task
task = task_service.create_task(1, "Test Task")
print(f"Created task: {task.id}")

# Archive the task
archived_task = task_service.archive_task(task.id)
print(f"Archived task: {archived_task.id}")