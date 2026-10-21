"""Run with: python -m unittest discover -p 'test_*.py'"""
import unittest
from collections import Counter

from task1 import simulate as simulate_light
from task2 import simulate as simulate_trips
from task3 import simulate as simulate_flows


class Stage1Tests(unittest.TestCase):
    def test_light(self):
        expected = [
            (0, "green"), (30, "yellow"), (35, "red"),
            (60, "green"), (90, "yellow"), (95, "red"),
            (120, "green"), (150, "yellow"), (155, "red"),
        ]
        self.assertEqual(simulate_light(), expected)

    def test_trips(self):
        log, end = simulate_trips()
        self.assertAlmostEqual(end, 20)
        self.assertEqual(log, [
            (0, "car", "started"), (0, "pedestrian", "started"),
            (10, "car", "arrived"), (20, "pedestrian", "arrived"),
        ])

    def test_random_flows(self):
        travelers, log, end = simulate_flows(seed=42)
        t2, log2, end2 = simulate_flows(seed=42)
        self.assertEqual(log, log2)
        self.assertEqual(end, end2)
        self.assertEqual(len(travelers), len(t2))
        self.assertEqual(len({t.id for t in travelers}), len(travelers))
        count = Counter(t.kind for t in travelers)
        self.assertTrue(count["car"] > 0 and count["pedestrian"] > 0)
        for t in travelers:
            self.assertLess(t.created_at, 600)
            self.assertIsNotNone(t.finished_at)
            self.assertAlmostEqual(t.finished_at - t.created_at,
                                   10 if t.kind == "car" else 20)
        self.assertEqual(Counter(kind for _, _, kind, event in log if event == "appeared"), count)
        self.assertEqual(Counter(kind for _, _, kind, event in log if event == "arrived"), count)


if __name__ == "__main__":
    unittest.main()
