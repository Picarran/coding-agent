"""Tiny hand-rolled test runner so the demo needs no external test framework."""

from calculator import add, divide, multiply, subtract

failures = []


def check(name, actual, expected):
    if actual == expected:
        print(f"PASS {name}")
    else:
        print(f"FAIL {name}: expected {expected!r}, got {actual!r}")
        failures.append(name)


check("add", add(2, 3), 5)
check("subtract", subtract(5, 3), 2)
check("multiply", multiply(4, 5), 20)
check("divide", divide(5, 2), 2.5)  # triggers the bug in calculator.py

print()
if failures:
    print(f"{len(failures)} test(s) failed: {failures}")
    raise SystemExit(1)
print("All tests passed.")
