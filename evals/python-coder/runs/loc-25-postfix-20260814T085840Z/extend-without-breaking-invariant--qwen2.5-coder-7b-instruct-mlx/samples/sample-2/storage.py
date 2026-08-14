import json

class Storage:
    def __init__(self, db_path):
        self.db_path = db_path

    def save_task(self, task):
        with open(self.db_path, 'a') as f:
            json.dump({'task_id': task.task_id, 'description': task.description, 'tags': task.tags}, f)
            f.write('\n')

    def get_task(self, task_id):
        with open(self.db_path, 'r') as f:
            for line in f:
                task_data = json.loads(line)
                if task_data['task_id'] == task_id:
                    return Task(task_id=task_data['task_id'], description=task_data['description'], tags=task_data['tags'])
        return None