"""Сценарии из методички и проверки границ разрешённых интервалов."""

import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stage1_lev import exercise04
from stage1_lev.reporting import export


def participants(result, kind):
    return [p for p in result.participants if p.kind == kind]


class CrossingTests(unittest.TestCase):
    def assert_complete(self, result):
        self.assertTrue(all(p.arrived_at is not None for p in result.participants))
        self.assertEqual(len({p.id for p in result.participants}), len(result.participants))
        for kind in ("car", "pedestrian"):
            counts = result.summary()[kind]
            self.assertEqual(counts["created"], counts["arrived"])

    def assert_fixed_signal_rules(self, result):
        cars = participants(result, "car")
        peds = participants(result, "pedestrian")
        self.assertEqual([p.id for p in sorted(cars, key=lambda p: p.queue_entered_at)],
                         [p.id for p in sorted(cars, key=lambda p: p.queue_left_at)])
        for previous, current in zip(cars, cars[1:]):
            self.assertLessEqual(previous.arrived_at, current.queue_left_at)
        for car in cars:
            self.assertLessEqual(car.queue_left_at % 30, 18)
            self.assertAlmostEqual(car.arrived_at - car.queue_left_at, 2)
            self.assertGreaterEqual(car.queue_left_at, car.queue_entered_at)
        for ped in peds:
            self.assertEqual(ped.queue_left_at % 30, 20)
            self.assertLess(ped.queue_entered_at, ped.queue_left_at)
            self.assertAlmostEqual(ped.arrived_at - ped.queue_left_at, 5)
            for car in cars:
                self.assertFalse(car.queue_left_at < ped.arrived_at
                                 and ped.queue_left_at < car.arrived_at)

    def test_fixed_group_from_methodology(self):
        result = exercise04.demo()
        self.assertEqual([(p.queue_left_at, p.arrived_at) for p in participants(result, "car")],
                         [(0, 2), (2, 4), (4, 6)])
        self.assertEqual([(p.queue_left_at, p.arrived_at)
                          for p in participants(result, "pedestrian")], [(20, 25)] * 2)
        self.assertEqual(result.finished_at, 25)
        self.assert_complete(result)

    def test_full_green_is_used_without_idle_gaps(self):
        for count in (10, 12):
            with self.subTest(count=count):
                result = exercise04.run(car_arrivals=[0] * count, pedestrian_arrivals=[],
                                        cars_at_crossing=True)
                cars = participants(result, "car")
                self.assertEqual([p.queue_left_at for p in cars[:10]], list(range(0, 20, 2)))
                self.assertEqual(sum(p.arrived_at <= 20 for p in cars), 10)
                self.assertEqual(sum(p.queue_left_at >= 20 for p in cars), count - 10)
                if count == 12:
                    self.assertEqual([p.queue_left_at for p in cars[10:]], [30, 32])

    def test_arrival_during_green_starts_immediately(self):
        result = exercise04.run(car_arrivals=[5, 7, 17, 18, 20, 30], pedestrian_arrivals=[],
                                cars_at_crossing=True)
        self.assertEqual([p.queue_left_at for p in participants(result, "car")],
                         [5, 7, 17, 30, 32, 34])
        self.assert_fixed_signal_rules(result)

    def test_exact_end_of_green_is_allowed(self):
        result = exercise04.run(car_arrivals=[18, 19], pedestrian_arrivals=[1],
                                cars_at_crossing=True)
        self.assertEqual([(p.queue_left_at, p.arrived_at) for p in participants(result, "car")],
                         [(18, 20), (30, 32)])
        self.assert_fixed_signal_rules(result)

    def test_pedestrians_at_phase_start_wait_for_next_group(self):
        result = exercise04.run(car_arrivals=[], pedestrian_arrivals=[19.5, 20, 21, 49.5, 50])
        self.assertEqual([p.queue_left_at for p in participants(result, "pedestrian")],
                         [20, 50, 50, 50, 80])
        self.assert_fixed_signal_rules(result)

    def test_total_car_time_includes_approach_queue_and_crossing(self):
        result = exercise04.run(car_arrivals=[0, 9], pedestrian_arrivals=[])
        self.assertEqual([(p.queue_entered_at, p.queue_left_at, p.arrived_at)
                          for p in participants(result, "car")], [(10, 10, 12), (19, 30, 32)])
        self.assertEqual(result.summary()["car"]["mean_travel_time"], (12 + 23) / 2)
        self.assertEqual(result.summary()["car"]["mean_queue_wait"], 5.5)

    def test_last_participants_are_drained_after_600(self):
        result = exercise04.run(car_arrivals=[599], pedestrian_arrivals=[599])
        self.assertEqual(participants(result, "car")[0].arrived_at, 611)
        self.assertEqual(participants(result, "pedestrian")[0].arrived_at, 625)
        self.assertEqual(result.finished_at, 625)

    def test_future_arrivals_are_not_lost_when_queues_empty(self):
        result = exercise04.run(car_arrivals=[0, 100], pedestrian_arrivals=[1, 200])
        self.assertEqual(len(result.participants), 4)
        self.assert_complete(result)
        self.assertEqual(result.finished_at, 235)

    def test_empty_schedule_finishes_with_infinite_controller(self):
        result = exercise04.run(car_arrivals=[], pedestrian_arrivals=[])
        self.assertEqual(result.finished_at, 0)
        self.assertEqual(result.participants, [])
        self.assertIsNone(result.summary()["car"]["mean_travel_time"])

    def test_random_flow_invariants_and_reproducibility(self):
        for seed in (0, 7, 42):
            with self.subTest(seed=seed):
                result = exercise04.run(seed)
                self.assertEqual(result, exercise04.run(seed))
                self.assert_complete(result)
                self.assert_fixed_signal_rules(result)
                self.assertTrue(all(0 < p.appeared_at < 600 for p in result.participants))
                for car in participants(result, "car"):
                    self.assertAlmostEqual(car.queue_entered_at - car.appeared_at, 10)
                for ped in participants(result, "pedestrian"):
                    self.assertEqual(ped.queue_entered_at, ped.appeared_at)

    def test_fixed_phases_make_car_times_independent_of_pedestrian_rate(self):
        without = exercise04.run(7, pedestrian_rate=0)
        busy = exercise04.run(7, pedestrian_rate=1800)
        self.assertEqual(participants(without, "car"), participants(busy, "car"))

    def test_export_contains_queue_timestamps_and_metrics(self):
        result = exercise04.demo()
        with TemporaryDirectory() as temporary:
            destination = export(result, Path(temporary))
            with (destination / "participants.csv").open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertIn("queue_entered_at", reader.fieldnames)
                self.assertIn("queue_left_at", reader.fieldnames)
                self.assertIn("velocity", reader.fieldnames)
                self.assertEqual(len(list(reader)), 5)

    def test_invalid_input_is_rejected(self):
        for kwargs in ({"horizon": 0}, {"car_rate": -1},
                       {"pedestrian_arrivals": [float("nan")]}, {"car_arrivals": [-1]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                exercise04.run(**kwargs)


if __name__ == "__main__":
    unittest.main()
