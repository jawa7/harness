class TaskTrackerError(Exception):
    """Base error for task tracker operations."""


class ValidationError(TaskTrackerError):
    pass


class NotFoundError(TaskTrackerError):
    pass
