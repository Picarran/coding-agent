def mean(nums):
    if not nums:
        raise ValueError("empty")
    return sum(nums) / len(nums)


def median(nums):
    if not nums:
        raise ValueError("empty")
    s = sorted(nums)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    # BUG: for even-length input the upper index is out of range.
    return (s[n // 2 - 1] + s[n // 2 + 1]) / 2

def top_words(text, n):
    pass
