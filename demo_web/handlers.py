import json


def health():
    return 200, "application/json", json.dumps({"status": "ok"})


def add(a, b):
    # BUG: subtracts instead of adds.
    return 200, "application/json", json.dumps({"result": a - b})


def stats(nums):
    raise NotImplementedError("implement: min/max/mean/median of nums")
