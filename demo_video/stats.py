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
    return (s[n // 2 - 1] + s[n // 2]) / 2

def top_words(text, n):
    words = text.split()
    counts = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    return sorted(counts, key=lambda w: (-counts[w], w))[:n]
