class Task:
    def __init__(self, task_id, description, tags=[]):
        self.task_id = task_id
        self.description = description
        self.tags = tags

class TaskService:
    def __init__(self, storage):
        self.storage = storage

    def create_task(self, task_id, description, tags=[]):
        task = Task(task_id, description, tags)
        self.storage.save_task(task)
        return task

    def get_task(self, task_id):
        return self.storage.get_task(task_id)