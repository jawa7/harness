def load_open_task_titles(file_path):
    with open(file_path, 'r') as f:
        tasks = json.load(f)
        return [task['title'] for task in tasks if not task.get('completed')]

# Preserving public behaviour by accepting file_path as an argument