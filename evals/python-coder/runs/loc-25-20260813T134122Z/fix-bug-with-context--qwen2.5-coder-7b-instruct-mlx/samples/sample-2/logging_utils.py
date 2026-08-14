import logging


def get_logger(name):
    return logging.getLogger(f"tasktracker.{name}")
