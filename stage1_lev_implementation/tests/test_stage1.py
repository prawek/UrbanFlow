"""Проверки условий методичек, а не результатов эталонных реализаций."""

import csv
import io
import json
import random
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import simpy

from stage1_lev_implementation import exercise01, exercise02, exercise03
from stage1_lev_implementation.data import Result
from stage1_lev_implementation.reporting import export


class TrafficLightTests(unittest.TestCase):
    def test_exact_log_and_excluded_right_boundary(self):
        result = exercise01.run()
        self.assertEqual([(e.time, e.action) for e in result.events], [
            (0, "green"), (30, "yellow"), (35, "red"),
            (60, "green"), (90, "yellow"), (95, "red"),
            (120, "green"), (150, "yellow"), (155, "red"),
        ])
        self.assertEqual(result.finished_at, 180)

    def test_custom_durations(self):
        result = exercise01.run(until=12, green_duration=2, yellow_duration=1, red_duration=3)
        self.assertEqual([e.time for e in result.events], [0, 2, 3, 6, 8, 9])

    def test_generator_prints_without_journal(self):
        env = simpy.Environment()
        output = io.StringIO()
        with redirect_stdout(output):
            env.process(exercise01.traffic_light(env, 30, 5, 25))
            env.run(until=35)
        self.assertEqual(output.getvalue().splitlines(), [
            "t=0 s | traffic_light | green", "t=30 s | traffic_light | yellow",
        ])

    def test_invalid_parameters(self):
        for value in (0, -1, float("nan"), float("inf")):
            for parameter in ("until", "green_duration", "yellow_duration", "red_duration"):
                with self.subTest(value=value, parameter=parameter):
                    with self.assertRaises(ValueError):
                        exercise01.run(**{parameter: value})


class ParallelTravelTests(unittest.TestCase):
    def test_common_start_and_independent_finish(self):
        result = exercise02.run()
        self.assertEqual([p.appeared_at for p in result.participants], [0, 0])
        self.assertEqual([p.arrived_at for p in result.participants], [10, 20])
        self.assertEqual(result.finished_at, 20)
        self.assertEqual([(e.time, e.kind, e.action) for e in result.events], [
            (0, "car", "started"), (0, "pedestrian", "started"),
            (10, "car", "arrived"), (20, "pedestrian", "arrived"),
        ])

    def test_duration_is_distance_divided_by_speed(self):
        result = exercise02.run(car_distance=60, car_speed=3,
                                pedestrian_distance=9, pedestrian_speed=3)
        self.assertEqual([p.travel_time for p in result.participants], [20, 3])
        self.assertEqual(result.finished_at, 20)

    def test_zero_distance(self):
        result = exercise02.run(car_distance=0, pedestrian_distance=0)
        self.assertEqual(result.finished_at, 0)
        self.assertEqual([p.travel_time for p in result.participants], [0, 0])

    def test_invalid_routes(self):
        for kwargs in ({"car_speed": 0}, {"pedestrian_speed": -1},
                       {"car_distance": -1}, {"pedestrian_distance": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                exercise02.run(**kwargs)


class ScriptedRandom:
    """Подставные интервалы для точной проверки границы генерации."""

    def __init__(self, intervals):
        self.intervals = iter(intervals)
        self.rates = []

    def expovariate(self, rate):
        self.rates.append(rate)
        return next(self.intervals)


class RandomFlowTests(unittest.TestCase):
    def test_reproducibility_and_different_seed(self):
        first = exercise03.run(42)
        self.assertEqual(first, exercise03.run(42))
        self.assertNotEqual(first.events, exercise03.run(43).events)

    def test_all_participants_arrive_and_events_are_chronological(self):
        for seed in (0, 7, 42, 100):
            with self.subTest(seed=seed):
                result = exercise03.run(seed)
                self.assertEqual(len({p.id for p in result.participants}), len(result.participants))
                self.assertEqual([e.time for e in result.events],
                                 sorted(e.time for e in result.events))
                for p in result.participants:
                    self.assertGreater(p.appeared_at, 0)
                    self.assertLess(p.appeared_at, 600)
                    self.assertAlmostEqual(p.travel_time, 10 if p.kind == "car" else 0)
                    self.assertLessEqual(p.arrived_at, result.finished_at)
                for kind in ("car", "pedestrian"):
                    counts = result.summary()[kind]
                    self.assertEqual(counts["created"], counts["arrived"])

    def test_pedestrians_have_no_movement_process(self):
        result = exercise03.run()
        events = [e for e in result.events if e.kind == "pedestrian"]
        self.assertTrue(events)
        self.assertEqual({e.action for e in events}, {"appeared", "arrived"})
        for appeared, arrived in zip(events[::2], events[1::2]):
            self.assertEqual((appeared.actor, appeared.time), (arrived.actor, arrived.time))

    def test_streams_are_independent(self):
        baseline = exercise03.run(7)
        without_pedestrians = exercise03.run(7, pedestrian_rate=0)
        more_cars = exercise03.run(7, car_rate=720)
        self.assertEqual([p for p in baseline.participants if p.kind == "car"],
                         without_pedestrians.participants)
        self.assertEqual([p for p in baseline.participants if p.kind == "pedestrian"],
                         [p for p in more_cars.participants if p.kind == "pedestrian"])

    def test_generation_waits_before_first_arrival_and_drains_after_horizon(self):
        env = simpy.Environment()
        result = Result(3)
        rng = ScriptedRandom([599, 1])
        env.process(exercise03.car_source(env, result, 360, 600, rng, 150, 15))
        env.run()
        self.assertEqual(rng.rates, [0.1, 0.1])
        self.assertEqual(len(result.participants), 1)
        self.assertEqual(result.participants[0].appeared_at, 599)
        self.assertEqual(result.participants[0].arrived_at, 609)
        self.assertEqual(env.now, 609)

    def test_cars_move_concurrently(self):
        env = simpy.Environment()
        result = Result(3)
        env.process(exercise03.car_source(
            env, result, 360, 10, ScriptedRandom([1, 1, 20]), 150, 15,
        ))
        env.run()
        self.assertEqual([(p.appeared_at, p.arrived_at) for p in result.participants],
                         [(1, 11), (2, 12)])

    def test_pedestrian_rate_conversion_and_excluded_boundary(self):
        env = simpy.Environment()
        result = Result(3)
        rng = ScriptedRandom([600])
        env.process(exercise03.pedestrian_source(env, result, 180, 600, rng))
        env.run()
        self.assertEqual(rng.rates, [0.05])
        self.assertEqual(result.participants, [])
        self.assertEqual(env.now, 600)

    def test_empty_streams_keep_observation_horizon(self):
        result = exercise03.run(car_rate=0, pedestrian_rate=0)
        self.assertEqual(result.participants, [])
        self.assertEqual(result.events, [])
        self.assertEqual(result.finished_at, 600)

    def test_global_random_state_is_unchanged(self):
        state = random.getstate()
        exercise03.run()
        self.assertEqual(random.getstate(), state)

    def test_counts_match_expected_means_over_many_seeds(self):
        # Для 200 запусков стандартная ошибка среднего: sqrt(60/200), sqrt(30/200).
        totals = {"car": 0, "pedestrian": 0}
        for seed in range(200):
            for p in exercise03.run(seed).participants:
                totals[p.kind] += 1
        self.assertAlmostEqual(totals["car"] / 200, 60, delta=2)
        self.assertAlmostEqual(totals["pedestrian"] / 200, 30, delta=1.5)

    def test_invalid_flow_parameters(self):
        for kwargs in ({"horizon": 0}, {"car_rate": -1}, {"pedestrian_rate": float("inf")},
                       {"car_speed": 0}, {"car_distance": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                exercise03.run(**kwargs)


class ReportingTests(unittest.TestCase):
    def test_export_preserves_participants_and_exact_times(self):
        result = exercise03.run()
        with TemporaryDirectory() as temporary:
            path = export(result, temporary)
            summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary, result.summary())
            with (path / "participants.csv").open(encoding="utf-8-sig", newline="") as stream:
                participants = list(csv.DictReader(stream))
            self.assertEqual(len(participants), len(result.participants))
            for row, p in zip(participants, result.participants):
                self.assertEqual(float(row["appeared_at"]), p.appeared_at)
                self.assertEqual(float(row["arrived_at"]), p.arrived_at)
            with (path / "events.csv").open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), len(result.events))

    def test_empty_participant_csv_has_header(self):
        with TemporaryDirectory() as temporary:
            path = export(exercise01.run(), Path(temporary))
            with (path / "participants.csv").open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, ["id", "kind", "appeared_at", "arrived_at"])
                self.assertEqual(list(reader), [])


if __name__ == "__main__":
    unittest.main()
