"""Сценарии задания 7, независимые полосы и освобождение резервов"""

import csv
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from stage1_lev.common.corridor import CarArrival, Direction
from stage1_lev.common.reporting import export
from stage1_lev.exercises import exercise07


def cars(result):
    return [p for p in result.participants if p.kind == "car"]


def route(result, car):
    return [v for v in result.visits if v.participant_id == car.id]


class CoordinatedCorridorTests(unittest.TestCase):
    def assert_safe_and_finished(self, result):
        self.assertTrue(all(p.arrived_at is not None for p in result.participants))
        self.assertTrue(all(p.current_segment is None and p.reserved_segment is None
                            for p in result.participants))
        self.assertEqual(len({p.id for p in result.participants}), len(result.participants))
        self.assertEqual([e.time for e in result.events], sorted(e.time for e in result.events))
        for kind in ("car", "pedestrian"):
            self.assertEqual(result.summary()[kind]["created"], result.summary()[kind]["arrived"])
        windows = {"straight": (0, 24, 12, 36), "left": (24, 36, 36, 48),
                   "right": (36, 48, 48, 60)}
        for car in cars(result):
            visits = route(result, car)
            self.assertEqual(len(visits), 1 if car.is_initial else 2)
            central_start, central_end, receiver_start, receiver_end = windows[car.direction]
            for visit in visits:
                start, end = ((central_start, central_end) if visit.segment == "A"
                              else (receiver_start, receiver_end))
                self.assertGreaterEqual(visit.crossing_started_at % 60, start)
                self.assertLessEqual(visit.crossing_started_at % 60 + 2, end + 1e-9)
                self.assertAlmostEqual(visit.crossing_finished_at - visit.crossing_started_at, 2)
                self.assertGreaterEqual(visit.crossing_started_at, visit.queue_entered_at)
                self.assertEqual(visit.exited_at, visit.crossing_finished_at)
                self.assertEqual(visit.direction, car.direction.value)
            self.assertEqual(visits[-1].exited_at, car.arrived_at)
            if not car.is_initial:
                source, target = visits
                self.assertEqual(source.segment, "A")
                self.assertAlmostEqual(source.queue_entered_at - source.entered_at, 10)
                self.assertEqual(target.reserved_at, source.crossing_started_at)
                self.assertEqual(target.entered_at, source.crossing_finished_at)
                self.assertAlmostEqual(target.queue_entered_at - target.entered_at, 10)

        # Проверяем FIFO и вместимость по временам отдельных машин, независимо от reserved_count
        for direction, name, capacity in ((Direction.STRAIGHT, "B_T", 5),
                                          (Direction.LEFT, "B_L", 3),
                                          (Direction.RIGHT, "B_R", 2)):
            for segment in ("A", name):
                visits = [v for v in result.visits if v.segment == segment
                          and v.direction == direction.value]
                self.assertEqual(
                    [v.participant_id for v in sorted(visits, key=lambda v: v.queue_entered_at)],
                    [v.participant_id for v in sorted(visits, key=lambda v: v.crossing_started_at)],
                )
                ordered = sorted(visits, key=lambda v: v.crossing_started_at)
                for previous, current in zip(ordered, ordered[1:]):
                    self.assertLessEqual(previous.crossing_finished_at, current.crossing_started_at)
            receiving = [v for v in result.visits if v.segment == name]
            for visit in receiving:
                reserved_at = visit.reserved_at if visit.reserved_at is not None else visit.entered_at
                used = sum((other.reserved_at if other.reserved_at is not None else other.entered_at)
                           <= reserved_at < other.exited_at for other in receiving)
                self.assertLessEqual(used, capacity)

        central_visits = sorted((v for v in result.visits if v.segment == "A"),
                                key=lambda v: v.crossing_started_at)
        for previous, current in zip(central_visits, central_visits[1:]):
            self.assertLessEqual(previous.crossing_finished_at, current.crossing_started_at)
        for pedestrian in (p for p in result.participants if p.kind == "pedestrian"):
            self.assertEqual(pedestrian.queue_entered_at, pedestrian.appeared_at)
            self.assertEqual(pedestrian.queue_left_at % 60, 48)
            self.assertLess(pedestrian.appeared_at, pedestrian.queue_left_at)
            self.assertEqual(pedestrian.arrived_at - pedestrian.queue_left_at, 5)
            for visit in central_visits:
                self.assertFalse(visit.crossing_started_at < pedestrian.arrived_at
                                 and pedestrian.queue_left_at < visit.crossing_finished_at)
        for state in result.final_segments.values():
            for name in ("occupied_count", "reserved_count", "car_queue_len",
                         "pedestrian_queue_len", "pending_requests"):
                self.assertEqual(state[name], 0)
            self.assertEqual(state["free_count"], state["capacity"])
        self.assertEqual(sum(result.completed_by_direction().values()), len(cars(result)))

    def test_synchronized_windows_from_methodology(self):
        result = exercise07.demo()
        self.assertEqual([
            (source.crossing_started_at, source.crossing_finished_at, target.queue_entered_at,
             target.crossing_started_at, car.arrived_at)
            for car in cars(result) for source, target in [route(result, car)]
        ], [(0, 2, 12, 12, 14), (24, 26, 36, 36, 38), (36, 38, 48, 48, 50)])
        self.assertEqual(result.finished_at, 50)

    def test_full_left_approach_only_blocks_left_turn(self):
        result = exercise07.spillback_demo()
        generated = {p.direction: p for p in cars(result) if not p.is_initial}
        self.assertEqual(route(result, generated[Direction.STRAIGHT])[0].crossing_started_at, 0)
        self.assertEqual(route(result, generated[Direction.RIGHT])[0].crossing_started_at, 36)
        left = route(result, generated[Direction.LEFT])[0]
        self.assertEqual(left.crossing_started_at, 204)
        self.assertGreater(left.capacity_wait, 0)
        initial = [p for p in cars(result) if p.is_initial]
        self.assertEqual([route(result, p)[0].crossing_started_at for p in initial], [156, 158, 160])
        self.assertEqual([p.arrived_at for p in initial], [158, 160, 162])
        self.assertEqual(result.summary()["directions"]["left"],
                         {"created": 4, "initial": 3, "generated": 1, "arrived": 4})
        self.assertTrue(any(row.left_queue_len == 1 and row.straight_queue_len == 0
                            and row.right_queue_len == 0 and 38 <= row.time < 120
                            for row in result.monitoring if row.segment == "A"))

    def test_prefilled_cars_finish_without_new_arrivals(self):
        result = exercise07.run(car_arrivals=[], pedestrian_arrivals=[], prefilled_left_count=3)
        self.assertEqual([p.arrived_at for p in cars(result)], [38, 40, 42])
        self.assertTrue(all(p.is_initial for p in cars(result)))
        self.assert_safe_and_finished(result)

    def test_central_crossing_must_fit_in_green(self):
        for direction, last_start, late_start, next_start in (
            (Direction.STRAIGHT, 22, 23, 60),
            (Direction.LEFT, 34, 35, 84),
            (Direction.RIGHT, 46, 47, 96),
        ):
            for arrival, expected in ((last_start, last_start), (late_start, next_start)):
                with self.subTest(direction=direction, arrival=arrival):
                    result = exercise07.run(car_arrivals=[CarArrival(arrival, direction)],
                                            pedestrian_arrivals=[1], cars_at_crossing=True)
                    self.assertEqual(route(result, cars(result)[0])[0].crossing_started_at, expected)

    def test_receiver_crossing_must_fit_in_green(self):
        for opening, expected in ((46, 46), (47, 96), (48, 96), (49, 96)):
            with self.subTest(opening=opening):
                result = exercise07.run(car_arrivals=[], pedestrian_arrivals=[],
                                        prefilled_left_count=1, left_release_blocked_until=opening)
                self.assertEqual(result.visits[0].crossing_started_at, expected)

    def test_capacity_limits_each_lane_and_available_green_is_used(self):
        for direction, count, expected in (
            (Direction.STRAIGHT, 12, [0, 2, 4, 6, 8, 14, 16, 18, 20, 22, 60, 62]),
            (Direction.LEFT, 6, [24, 26, 28, 84, 86, 88]),
            (Direction.RIGHT, 4, [36, 38, 96, 98]),
        ):
            result = exercise07.run(car_arrivals=[CarArrival(0, direction)] * count,
                                    pedestrian_arrivals=[], cars_at_crossing=True)
            actual = [v.crossing_started_at for v in result.visits if v.segment == "A"]
            self.assertEqual(actual, expected)

    def test_pedestrians_at_phase_start_wait_for_next_group(self):
        result = exercise07.run(car_arrivals=[], pedestrian_arrivals=[47, 48, 49, 107, 108])
        self.assertEqual([p.queue_left_at for p in result.participants], [48, 108, 108, 108, 168])
        self.assertEqual([p.arrived_at for p in result.participants], [53, 113, 113, 113, 173])

    def test_future_arrivals_and_empty_inputs(self):
        empty = exercise07.run(car_arrivals=[], pedestrian_arrivals=[])
        self.assertEqual(empty.finished_at, 0)
        self.assert_safe_and_finished(empty)
        random_empty = exercise07.run(car_rate=0, pedestrian_rate=0)
        self.assertEqual(random_empty.finished_at, 600)
        result = exercise07.run(car_arrivals=[CarArrival(0, "straight"), CarArrival(120, "straight")],
                                pedestrian_arrivals=[], cars_at_crossing=True)
        self.assertEqual([p.arrived_at for p in cars(result)], [14, 134])

    def test_sources_exclude_horizon_and_drain_participants(self):
        result = exercise07.run(
            car_arrivals=[CarArrival(t, "straight") for t in (599, 600, 601)],
            pedestrian_arrivals=[599, 600, 601],
        )
        self.assertEqual(len(result.participants), 2)
        self.assertTrue(all(p.appeared_at == 599 for p in result.participants))
        self.assertEqual(result.finished_at, 653)
        self.assert_safe_and_finished(result)

    def test_random_routes_are_reproducible_and_safe(self):
        for seed in (0, 7, 42):
            with self.subTest(seed=seed):
                result = exercise07.run(seed)
                self.assertEqual(result, exercise07.run(seed))
                self.assert_safe_and_finished(result)
                self.assertTrue(all(0 < p.appeared_at < 600 for p in result.participants))

    def test_streams_and_direction_choices_are_independent(self):
        baseline = exercise07.run(7)
        no_pedestrians = exercise07.run(7, pedestrian_rate=0)
        self.assertEqual(cars(baseline), cars(no_pedestrians))
        more_cars = exercise07.run(7, car_rate=720)
        self.assertEqual([p.appeared_at for p in baseline.participants if p.kind == "pedestrian"],
                         [p.appeared_at for p in more_cars.participants if p.kind == "pedestrian"])
        self.assertEqual([p.direction for p in cars(baseline)],
                         [p.direction for p in cars(more_cars)[:len(cars(baseline))]])

    def test_direction_frequencies(self):
        result = exercise07.run(horizon=36000, pedestrian_arrivals=[])
        members = cars(result)
        self.assertGreater(len(members), 3000)
        for direction, probability in ((Direction.STRAIGHT, 0.5), (Direction.LEFT, 0.3),
                                        (Direction.RIGHT, 0.2)):
            frequency = sum(p.direction == direction for p in members) / len(members)
            self.assertAlmostEqual(frequency, probability, delta=0.04)

    def test_export_contains_routes_directions_and_initial_cars(self):
        result = exercise07.spillback_demo()
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            directory = export(result, temporary)
            self.assertEqual(json.loads((directory / "summary.json").read_text(encoding="utf-8")),
                             result.summary())
            with (directory / "participants.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual({r["direction"] for r in rows}, {"straight", "left", "right", ""})
            self.assertEqual(sum(r["is_initial"] == "True" for r in rows), 3)
            self.assertNotIn("reserved_segment", rows[0])
            self.assertNotIn("current_segment", rows[0])
            for filename, column in (("visits.csv", "reserved_at"),
                                     ("monitoring.csv", "left_queue_len")):
                with (directory / filename).open(encoding="utf-8-sig", newline="") as stream:
                    self.assertIn(column, csv.DictReader(stream).fieldnames)

    def test_invalid_parameters(self):
        for kwargs in ({"horizon": 0}, {"car_rate": -1}, {"pedestrian_rate": float("nan")},
                       {"prefilled_left_count": 4}, {"prefilled_left_count": True},
                       {"left_release_blocked_until": float("inf")},
                       {"car_arrivals": [0]}, {"pedestrian_arrivals": [-1]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                exercise07.run(**kwargs)
        for time, direction in ((-1, "straight"), (float("nan"), "left"), (0, "unknown")):
            with self.assertRaises(ValueError):
                CarArrival(time, direction)
