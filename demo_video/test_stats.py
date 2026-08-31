import unittest

from stats import mean, median, top_words


class TestStats(unittest.TestCase):
    def test_mean(self):
        self.assertEqual(mean([1, 2, 3]), 2.0)

    def test_median_odd(self):
        self.assertEqual(median([3, 1, 2]), 2)

    def test_median_even(self):
        self.assertEqual(median([1, 2, 3, 4]), 2.5)

    def test_top_words(self):
        self.assertEqual(top_words("a b a c b a", 2), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
