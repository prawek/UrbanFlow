"""Проверки резервов, блокирования и маршрутов заданий 6 и 6а"""

import csv
import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from stage1_lev.exercises import exercise06, exercise06a
from stage1_lev.common.reporting import export
from stage1_lev.common.roads import SegmentConfig
from stage1_lev.exercises.exercise06a import simulate


def cars(result):
    return [p for p in result.participants if p.kind == "car"]


def visits(result, segment):
    return [v for v in result.visits if v.segment == segment]


def signal_log(result, name):
    return [(e.time, e.action) for e in result.events if e.actor == f"traffic_light:{name}"]


class RoadRulesMixin:
    def assert_resources_returned(self, result):
        for row in result.monitoring:
            self.assertGreaterEqual(row.occupied_count, 0)
            self.assertGreaterEqual(row.reserved_count, 0)
            if row.capacity is not None:
                self.assertEqual(row.free_count + row.occupied_count + row.reserved_count,
                                 row.capacity)
                self.assertLessEqual(row.occupied_count + row.reserved_count, row.capacity)
        for state in result.final_segments.values():
            for name in ("occupied_count", "reserved_count", "car_queue_len",
                         "pedestrian_queue_len", "pending_requests"):
                self.assertEqual(state[name], 0, (name, state))
            if state["capacity"] is not None:
                self.assertEqual(state["free_count"], state["capacity"])
        self.assertTrue(all(p.current_segment is None for p in result.participants))

    def assert_complete_and_safe(self, result, configs):
        for kind in ("car", "pedestrian"):
            counts = result.summary()[kind]
            self.assertEqual(counts["created"], counts["arrived"])
        self.assertEqual(len({p.id for p in result.participants}), len(result.participants))
        self.assertTrue(all(p.arrived_at is not None for p in result.participants))
        self.assertEqual([e.time for e in result.events], sorted(e.time for e in result.events))
        for config in configs:
            if not config.intersection_length:
                continue
            rows = visits(result, config.name)
            self.assertEqual([v.participant_id for v in sorted(rows, key=lambda v: v.queue_entered_at)],
                             [v.participant_id for v in sorted(rows, key=lambda v: v.crossing_started_at)])
            for previous, current in zip(rows, rows[1:]):
                self.assertLessEqual(previous.crossing_finished_at, current.crossing_started_at)
            duration = config.intersection_length / config.speed
            cycle = config.green_duration + config.red_duration
            for visit in rows:
                self.assertGreaterEqual(visit.crossing_started_at, visit.queue_entered_at)
                self.assertAlmostEqual(visit.crossing_finished_at - visit.crossing_started_at, duration)
                self.assertLessEqual(visit.crossing_started_at % cycle + duration,
                                     config.green_duration + 1e-9)
            for ped in (p for p in result.participants
                        if p.kind == "pedestrian" and p.local_segment == config.name):
                self.assertEqual(ped.queue_left_at % cycle, config.green_duration)
                self.assertLess(ped.queue_entered_at, ped.queue_left_at)
                self.assertEqual(ped.arrived_at - ped.queue_left_at, 5)
                for visit in rows:
                    self.assertFalse(visit.crossing_started_at < ped.arrived_at
                                     and ped.queue_left_at < visit.crossing_finished_at)
        self.assert_resources_returned(result)


class SpillbackTests(RoadRulesMixin, unittest.TestCase):
    def test_eight_cars_with_closed_exit_from_methodology(self):
        result = exercise06.demo()
        self.assertEqual([v.crossing_started_at for v in visits(result, "A")],
                         [0, 2, 4, 6, 8, 60, 62, 64])
        self.assertEqual([p.arrived_at for p in cars(result)[:5]], [60] * 5)
        self.assertEqual(result.finished_at, 76)
        self.assertEqual(max(r.occupied_count + r.reserved_count for r in result.monitoring
                             if r.segment == "B"), 5)
        self.assertTrue(any(r.segment == "A" and r.car_queue_len == 3 and 10 <= r.time < 60
                            for r in result.monitoring))
        self.assertEqual([p.queue_left_at for p in result.participants if p.kind == "pedestrian"],
                         [20, 20])
        self.assert_complete_and_safe(result, exercise06.SEGMENTS)

    def test_slot_freed_during_red_or_with_insufficient_green_is_rechecked(self):
        for opening in (19, 20, 21):
            with self.subTest(opening=opening):
                result = exercise06.run(exit_open_at=opening, car_arrivals=[0] * 6,
                                        pedestrian_arrivals=[1], cars_at_crossing=True)
                self.assertEqual(visits(result, "A")[5].crossing_started_at, 30)
                self.assert_complete_and_safe(result, exercise06.SEGMENTS)

    def test_slot_freed_at_last_allowed_start_is_used(self):
        result = exercise06.run(exit_open_at=18, car_arrivals=[0] * 6,
                                pedestrian_arrivals=[], cars_at_crossing=True)
        self.assertEqual(visits(result, "A")[5].crossing_started_at, 18)
        self.assert_resources_returned(result)

    def test_two_runs_have_identical_input_streams(self):
        opened, closed = exercise06.compare(7)
        self.assertEqual([(p.id, p.appeared_at) for p in opened.participants],
                         [(p.id, p.appeared_at) for p in closed.participants])
        self.assertGreaterEqual(closed.summary()["car"]["mean_travel_time"],
                                opened.summary()["car"]["mean_travel_time"])
        self.assert_complete_and_safe(opened, exercise06.SEGMENTS)
        self.assert_complete_and_safe(closed, exercise06.SEGMENTS)

    def test_reserved_slot_counts_before_full_entry(self):
        result = exercise06.run(car_arrivals=[0], pedestrian_arrivals=[], cars_at_crossing=True)
        self.assertTrue(any(r.segment == "B" and r.time == 0
                            and r.reserved_count == 1 and r.occupied_count == 0
                            for r in result.monitoring))
        self.assertEqual([(v.entered_at, v.exited_at) for v in visits(result, "B")], [(2, 12)])
        self.assertEqual(cars(result)[0].travel_time, 12)

    def test_pedestrian_on_phase_boundary_waits_for_next_group(self):
        result = exercise06.run(car_arrivals=[], pedestrian_arrivals=[19, 20, 21])
        self.assertEqual([p.queue_left_at for p in result.participants], [20, 50, 50])

    def test_random_model_is_reproducible_and_has_no_resource_leaks(self):
        for seed in (0, 7, 42):
            with self.subTest(seed=seed):
                result = exercise06.run(seed, exit_open_at=60)
                self.assertEqual(result, exercise06.run(seed, exit_open_at=60))
                self.assert_complete_and_safe(result, exercise06.SEGMENTS)
                self.assertTrue(all(0 < p.appeared_at < 600 for p in result.participants))


class SequentialSegmentsTests(RoadRulesMixin, unittest.TestCase):
    def test_single_car_route_from_methodology(self):
        result = exercise06a.demo()
        self.assertEqual([(v.segment, v.queue_entered_at, v.crossing_started_at, v.crossing_finished_at)
                          for v in result.visits], [
            ("A", 10, 10, 12), ("B", 20, 20, 23), ("C", 35, 36, 40),
        ])
        self.assertEqual(cars(result)[0].arrived_at, 40)
        self.assert_complete_and_safe(result, exercise06a.SEGMENTS)

    def test_car_starts_on_green_with_fractional_duration(self):
        """Дробный цикл не должен задерживать автомобиль при включении зелёного"""
        segments = tuple(
            replace(segment, green_duration=20.1)
            if segment.name == "A" else segment
            for segment in exercise06a.SEGMENTS
        )
        result = exercise06a.run(
            segments=segments,
            car_arrivals=[150.5],
            cars_at_crossing=True,
            pedestrian_arrivals={},
        )
        visit = visits(result, "A")[0]
        signal_events = [
            event for event in result.events
            if event.actor == "traffic_light:A" and 150.5 <= event.time <= 170.6
        ]

        print("\nA: green_duration=20.1 s, red_duration=10 s")
        for event in signal_events:
            print(f"SIGNAL A: t={event.time:g} s, phase={event.action}")
        print(f"CAR A: queue_entered_at={visit.queue_entered_at:g} s")
        print("EXPECTED crossing: 150.5 -> 152.5 s")
        print(f"ACTUAL crossing:   {visit.crossing_started_at:g} -> "
              f"{visit.crossing_finished_at:g} s")

        self.assertTrue(any(
            event.action == "car" and event.time == 150.5
            for event in signal_events
        ))
        self.assertAlmostEqual(visit.queue_entered_at, 150.5)
        self.assertAlmostEqual(visit.crossing_started_at, 150.5)
        self.assertAlmostEqual(visit.crossing_finished_at, 152.5)

    def test_signals_are_independent_over_same_observation_interval(self):
        changed = tuple(replace(c, green_duration=30) if c.name == "B" else c
                        for c in exercise06a.SEGMENTS)
        kwargs = dict(car_arrivals=[0], pedestrian_arrivals={}, observe_until=120)
        baseline = exercise06a.run(**kwargs)
        altered = exercise06a.run(segments=changed, **kwargs)
        self.assertEqual(baseline.finished_at, 120)
        self.assertEqual(altered.finished_at, 120)
        for name in ("A", "C"):
            self.assertEqual(signal_log(baseline, name), signal_log(altered, name))
        self.assertNotEqual(signal_log(baseline, "B"), signal_log(altered, "B"))
        self.assertEqual(signal_log(baseline, "A")[:4],
                         [(0, "car"), (20, "pedestrian"), (30, "car"), (50, "pedestrian")])
        self.assertEqual(signal_log(baseline, "C")[:4],
                         [(0, "car"), (17, "pedestrian"), (36, "car"), (53, "pedestrian")])

    def test_spillback_propagates_from_c_to_b_then_a(self):
        result = exercise06a.spillback_demo()
        for name, capacity in (("B", 5), ("C", 3)):
            self.assertTrue(any(r.segment == name and r.time < 180
                                and r.occupied_count + r.reserved_count == capacity
                                for r in result.monitoring))
        self.assertGreaterEqual(visits(result, "A")[-1].crossing_started_at, 180)
        self.assertTrue(any(r.segment == "A" and r.car_queue_len > 0 and 100 < r.time < 180
                            for r in result.monitoring))
        self.assertTrue(all(v.crossing_started_at >= 180 for v in visits(result, "C")))
        self.assertEqual(cars(result)[-1].arrived_at, 264)
        # Запрет проезда автомобилей не останавливает местный контроллер и пешеходов
        ped_c = next(p for p in result.participants if p.local_segment == "C")
        self.assertEqual((ped_c.queue_left_at, ped_c.arrived_at), (17, 22))
        self.assertTrue(any(time < 180 and phase == "pedestrian" for time, phase in signal_log(result, "C")))
        self.assert_complete_and_safe(result, exercise06a.SEGMENTS)

    def test_upstream_slot_is_held_until_crossing_finishes(self):
        result = exercise06a.demo()
        self.assertTrue(any(r.segment == "B" and r.time == 20 and r.occupied_count == 1
                            for r in result.monitoring))
        self.assertTrue(any(r.segment == "C" and r.time == 20 and r.reserved_count == 1
                            for r in result.monitoring))
        b_exit = next(e.time for e in result.events if e.actor == "car-1" and e.action == "left:B")
        self.assertEqual(b_exit, 23)

    def test_local_pedestrians_at_phase_start_are_deferred(self):
        result = exercise06a.run(car_arrivals=[], pedestrian_arrivals={
            "A": [20], "B": [27], "C": [17],
        })
        self.assertEqual({p.local_segment: p.queue_left_at for p in result.participants},
                         {"A": 50, "B": 67, "C": 53})
        self.assert_complete_and_safe(result, exercise06a.SEGMENTS)

    def test_each_stream_uses_its_own_random_generator(self):
        baseline = exercise06a.run(7)
        no_b = exercise06a.run(7, pedestrian_arrivals={"A": None, "B": [], "C": None})
        for kind, local in (("car", ""), ("pedestrian", "A"), ("pedestrian", "C")):
            def arrivals(result):
                return [(p.id, p.appeared_at) for p in result.participants
                        if p.kind == kind and p.local_segment == local]
            self.assertEqual(arrivals(baseline), arrivals(no_b))

    def test_random_routes_fifo_capacity_and_completion(self):
        for seed in (0, 7, 42):
            with self.subTest(seed=seed):
                result = exercise06a.run(seed)
                self.assertEqual(result, exercise06a.run(seed))
                self.assert_complete_and_safe(result, exercise06a.SEGMENTS)
                self.assertTrue(all(0 < p.appeared_at < 600 for p in result.participants))
                for participant in cars(result):
                    route = [v for v in result.visits if v.participant_id == participant.id]
                    self.assertEqual([v.segment for v in route], ["A", "B", "C"])
                    self.assertEqual(route[-1].exited_at, participant.arrived_at)

    def test_empty_inputs_and_late_inputs_finish(self):
        empty = exercise06a.run(car_arrivals=[], pedestrian_arrivals={})
        self.assertEqual(empty.finished_at, 0)
        self.assert_resources_returned(empty)
        late = exercise06a.run(car_arrivals=[599], pedestrian_arrivals={"C": [599]})
        self.assertGreater(late.finished_at, 600)
        self.assert_complete_and_safe(late, exercise06a.SEGMENTS)

    def test_explicit_schedules_can_extend_beyond_random_generation_horizon(self):
        result = exercise06a.run(horizon=10, car_arrivals=[100], pedestrian_arrivals={"C": [100]})
        self.assertEqual(sorted(p.appeared_at for p in result.participants), [100, 100])
        self.assert_complete_and_safe(result, exercise06a.SEGMENTS)

    def test_observation_does_not_truncate_participants(self):
        result = exercise06a.run(car_arrivals=[0], pedestrian_arrivals={}, observe_until=1)
        self.assertEqual(result.finished_at, 40)

    def test_export_has_flat_participants_and_route_and_monitoring_tables(self):
        result = exercise06a.demo()
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            directory = export(result, Path(temporary))
            summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary, result.summary())
            for filename, count in (("participants.csv", 1), ("visits.csv", 3),
                                     ("monitoring.csv", len(result.monitoring))):
                with (directory / filename).open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    self.assertNotIn("current_segment", reader.fieldnames)
                    self.assertEqual(len(list(reader)), count)

    def test_invalid_configs_and_schedules_are_rejected(self):
        bad = (
            replace(exercise06a.SEGMENTS[0], speed=0),
            replace(exercise06a.SEGMENTS[0], approach_length=-1),
            replace(exercise06a.SEGMENTS[0], green_duration=1),
            replace(exercise06a.SEGMENTS[0], red_duration=4),
            replace(exercise06a.SEGMENTS[0], capacity=2),
        )
        for config in bad:
            with self.subTest(config=config), self.assertRaises(ValueError):
                exercise06a.run(segments=(config, *exercise06a.SEGMENTS[1:]))
        for kwargs in ({"crossing_closed_until": {"D": 10}},
                       {"pedestrian_arrivals": {"D": []}}, {"horizon": -1},
                       {"crossing_closed_until": {"C": float("nan")}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                exercise06a.run(**kwargs)
        with self.assertRaises(ValueError):
            simulate(6, (SegmentConfig("A", 150, 30, 15),
                         SegmentConfig("B", 150, 0, 15, capacity=1.5)))


if __name__ == "__main__":
    unittest.main()
