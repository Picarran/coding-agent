import json
import unittest

import handlers


class TestHandlers(unittest.TestCase):
    def test_health(self):
        status, _, body = handlers.health()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok"})

    def test_add(self):
        status, _, body = handlers.add(2, 3)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"result": 5})

    def test_stats(self):
        status, _, body = handlers.stats([1, 2, 3, 4])
        data = json.loads(body)
        self.assertEqual(data["min"], 1)
        self.assertEqual(data["max"], 4)
        self.assertEqual(data["mean"], 2.5)
        self.assertEqual(data["median"], 2.5)


if __name__ == "__main__":
    unittest.main()
