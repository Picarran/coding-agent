"""A deliberately small module used to demo the coding agent."""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    # BUG: integer floor division, but callers expect a float result.
    return a // b
