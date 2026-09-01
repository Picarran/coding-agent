import json


def health():
    return 200, "application/json", json.dumps({"status": "ok"})


def add(a, b):
    return 200, "application/json", json.dumps({"result": a + b})


def stats(nums):
    nums = sorted(nums)
    n = len(nums)
    mean = sum(nums) / n
    if n % 2 == 1:
        median = nums[n // 2]
    else:
        median = (nums[n // 2 - 1] + nums[n // 2]) / 2
    return 200, "application/json", json.dumps({
        "min": min(nums),
        "max": max(nums),
        "mean": mean,
        "median": median,
    })
