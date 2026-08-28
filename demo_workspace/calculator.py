"""A deliberately small module used to demo the coding agent."""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    # BUG: This function should use integer floor division (//), not true division (/)
    return a // b
