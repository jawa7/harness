def load_open_task_titles(file_reader=read_file):
    with file_reader('tasks.json') as f:
        tasks = json.load(f)
        open_tasks = [task for task in tasks if not task['completed']]
        return open_tasks
