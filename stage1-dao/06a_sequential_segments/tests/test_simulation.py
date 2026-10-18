import io
import unittest
from contextlib import redirect_stdout
from dataclasses import replace

import simpy

from .. import (
    DEFAULT_SEGMENT_PARAMETERS,
    Kind,
    LightPhase,
    SequentialSegmentsSimulation,
)


def cars(result):
    return [p for p in result.participants if p.kind is Kind.CAR]


def pedestrians(result, name):
    return [p for p in result.participants if p.kind is Kind.PEDESTRIAN and name in p.visits]


def run_simulation(**kwargs):
    simulation = SequentialSegmentsSimulation(env=simpy.Environment(), **kwargs)
    with redirect_stdout(io.StringIO()):
        result = simulation.run()
    return simulation, result


class SequentialSegmentsTests(unittest.TestCase):
    def assert_drained(self, simulation, result):
        self.assertEqual(result.count_finished(), len(result.participants))
        self.assertEqual(simulation.free_places_at_end_count, {"B": 5, "C": 3})
        for segment in simulation.segments.values():
            self.assertEqual(segment.car_queue.items, [])
            self.assertEqual(segment.pedestrian_queue.items, [])
            if segment.free_slots is not None:
                self.assertEqual(segment.free_slots.get_queue, [])
                self.assertEqual(segment.free_slots.put_queue, [])
        for participant in result.participants:
            self.assertIsNone(participant.current_segment)
            if participant.kind is Kind.CAR:
                self.assertIsNone(participant.reserved_segment)

    def test_empty_input_finishes_immediately(self):
        simulation, result = run_simulation(car_arrivals=[], pedestrian_arrivals={})
        self.assertEqual(result.finished_at, 0)
        self.assertEqual(result.count_finished(), 0)
        self.assert_drained(simulation, result)

    def test_single_car_matches_reference_route(self):
        simulation, result = run_simulation(car_arrivals=[0], pedestrian_arrivals={})
        car = cars(result)[0]
        self.assertEqual(list(car.visits), ["A", "B", "C"])
        self.assertEqual(
            [(v.queue_entered_at, v.crossing_started_at, v.crossing_finished_at)
             for v in car.visits.values()],
            [(10, 10, 12), (20, 20, 23), (35, 36, 40)],
        )
        self.assertEqual(
            [(v.reserved_at, v.entered_at) for v in car.visits.values()],
            [(None, 0), (10, 12), (20, 23)],
        )
        self.assertEqual(car.finished_at, 40)
        self.assert_drained(simulation, result)

    def test_waits_for_later_arrival_after_network_drains(self):
        simulation, result = run_simulation(car_arrivals=[0, 120], pedestrian_arrivals={})
        self.assertEqual([car.finished_at for car in cars(result)], [40, 159])
        self.assert_drained(simulation, result)

    def test_signals_and_arrival_streams_are_independent(self):
        _, baseline = run_simulation(horizon_ms=180)
        _, changed = run_simulation(
            horizon_ms=180,
            segment_parameters={"B": replace(DEFAULT_SEGMENT_PARAMETERS["B"], green_duration=23)},
        )

        def phases(result, name):
            return [(p.time, p.phase) for p in result.phase_changes
                    if p.segment_name == name and p.time <= 180]

        self.assertEqual(phases(baseline, "A"), phases(changed, "A"))
        self.assertEqual(phases(baseline, "C"), phases(changed, "C"))
        self.assertNotEqual(phases(baseline, "B"), phases(changed, "B"))
        for name, green, cycle in (("A", 20, 30), ("B", 27, 40), ("C", 17, 36)):
            expected = sorted(
                [(time, LightPhase.VEHICLES) for time in range(0, 181, cycle)]
                + [(time, LightPhase.PEDESTRIANS) for time in range(green, 181, cycle)]
            )
            self.assertEqual(phases(baseline, name), expected)
        self.assertEqual(
            [(p.kind, next(iter(p.visits)), p.appeared_at) for p in baseline.participants],
            [(p.kind, next(iter(p.visits)), p.appeared_at) for p in changed.participants],
        )

    def test_spillback_fills_c_then_b_and_keeps_crossings_clear(self):
        env = simpy.Environment()
        simulation = SequentialSegmentsSimulation(
            env=env, car_arrivals=[0] * 9,
            pedestrian_arrivals={"A": [1], "B": [1], "C": [1]},
            is_car_at_stop_line=True, c_release_blocked_until=180,
        )
        snapshot = {}

        def observe():
            yield env.timeout(100)
            snapshot["free_places"] = {
                name: simulation.segments[name].free_slots.level for name in ("B", "C")
            }
            snapshot["waiting_on_a_count"] = sum(
                car.current_segment is simulation.segments["A"]
                for car in cars(simulation.journal)
            )

        env.process(observe())
        with redirect_stdout(io.StringIO()):
            result = simulation.run()
        self.assertEqual(snapshot, {
            "free_places": {"B": 0, "C": 0}, "waiting_on_a_count": 1,
        })
        generated = cars(result)
        self.assertEqual(
            [car.visits["A"].crossing_started_at for car in generated[:8]],
            [0, 2, 4, 6, 8, 13, 16, 30],
        )
        self.assertGreaterEqual(generated[8].visits["A"].crossing_started_at, 180)
        self.assertGreater(generated[8].visits["A"].downstream_blocked_time, 0)
        self.assertTrue(all(car.visits["C"].crossing_started_at >= 180 for car in generated))
        for name, phase_start in (("A", 20), ("B", 27), ("C", 17)):
            self.assertEqual(pedestrians(result, name)[0].visits[name].crossing_started_at, phase_start)
        c_red_phases = [p.time for p in result.phase_changes
                        if p.segment_name == "C" and p.phase is LightPhase.PEDESTRIANS and p.time < 180]
        self.assertEqual(c_red_phases, [17, 53, 89, 125, 161])
        self.assert_drained(simulation, result)

    def test_capacity_release_rechecks_signal_and_remaining_green(self):
        for release_at, expected_start in ((18, 18), (19, 30), (20, 30), (21, 30)):
            with self.subTest(release_at=release_at):
                env = simpy.Environment()
                simulation = SequentialSegmentsSimulation(
                    env=env, car_arrivals=[0], pedestrian_arrivals={}, is_car_at_stop_line=True,
                )
                free_slots = simulation.segments["B"].free_slots
                free_slots.get(5)
                snapshot = []

                def release_and_observe():
                    yield env.timeout(release_at)
                    yield free_slots.put(5)
                    yield env.timeout(25 - env.now)
                    snapshot.append((free_slots.level, len(free_slots.get_queue)))

                env.process(release_and_observe())
                with redirect_stdout(io.StringIO()):
                    result = simulation.run()
                car = cars(result)[0]
                self.assertEqual(car.visits["A"].crossing_started_at, expected_start)
                self.assertEqual(car.visits["B"].reserved_at, expected_start)
                if release_at > 18:
                    self.assertEqual(snapshot, [(5, 0)])
                self.assert_drained(simulation, result)

    def test_boundary_arrivals_and_remaining_green_at_every_signal(self):
        cases = (
            # Первый автомобиль доезжает ровно за время проезда до конца зелёного.
            (8, "A", None, 18, 20),
            (9, "A", None, 30, 32),
            # К B прибывает ровно в 24 или 25 с при конце зелёного в 27 с.
            (4, "B", None, 24, 27),
            (5, "B", None, 40, 43),
            # К C прибывает в 49 или 50 с при конце зелёного в 53 с.
            (0, "C", 260, 49, 53),
            (0, "C", 270, 72, 76),
        )
        for arrival, name, approach_length, expected_start, expected_finish in cases:
            with self.subTest(arrival=arrival, segment=name):
                parameters = {} if approach_length is None else {
                    name: replace(DEFAULT_SEGMENT_PARAMETERS[name], approach_length=approach_length),
                }
                _, result = run_simulation(
                    car_arrivals=[arrival], pedestrian_arrivals={}, segment_parameters=parameters,
                )
                visit = cars(result)[0].visits[name]
                self.assertEqual((visit.crossing_started_at, visit.crossing_finished_at),
                                 (expected_start, expected_finish))

    def test_pedestrian_groups_exclude_arrivals_at_phase_start(self):
        simulation, result = run_simulation(
            car_arrivals=[], pedestrian_arrivals={
                "A": [1, 1, 20, 21], "B": [1, 27, 28], "C": [1, 17, 18],
            },
        )
        for name, expected in (("A", [20, 20, 50, 50]), ("B", [27, 67, 67]), ("C", [17, 53, 53])):
            group = pedestrians(result, name)
            self.assertEqual([p.visits[name].crossing_started_at for p in group], expected)
            for pedestrian in group:
                visit = pedestrian.visits[name]
                self.assertEqual(visit.queue_entered_at, pedestrian.appeared_at)
                self.assertEqual(visit.crossing_finished_at - visit.crossing_started_at, 5)
        self.assert_drained(simulation, result)

    def test_generators_exclude_horizon_and_drain_later_crossings(self):
        simulation, result = run_simulation(
            car_arrivals=[599, 600, 601], pedestrian_arrivals={
                name: [599, 600, 601] for name in ("A", "B", "C")
            },
        )
        self.assertEqual(result.count(Kind.CAR), 1)
        self.assertEqual(result.count(Kind.PEDESTRIAN), 3)
        self.assertGreater(result.finished_at, 600)
        self.assertTrue(all(p.appeared_at < 600 for p in result.participants))
        self.assert_drained(simulation, result)

    def test_random_run_preserves_fifo_capacity_and_separates_participants(self):
        for seed in (7, 42, 99):
            with self.subTest(seed=seed):
                simulation, result = run_simulation(seed=seed)
                generated = cars(result)
                self.assertTrue(generated)
                self.assertGreaterEqual(result.finished_at, 600)
                self.assertTrue(all(p.appeared_at < 600 for p in result.participants))
                self.assert_drained(simulation, result)
                for name, segment in simulation.segments.items():
                    visits = [car.visits[name] for car in generated]
                    self.assertEqual([v.queue_entered_at for v in visits], sorted(v.queue_entered_at for v in visits))
                    self.assertEqual([v.crossing_started_at for v in visits], sorted(v.crossing_started_at for v in visits))
                    cycle = segment.green_duration + segment.red_duration
                    for index, visit in enumerate(visits):
                        self.assertLess(visit.crossing_started_at % cycle, segment.green_duration)
                        self.assertLessEqual(
                            visit.crossing_started_at % cycle + segment.intersection_travel_time,
                            segment.green_duration + 1e-9,
                        )
                        self.assertAlmostEqual(visit.queue_entered_at - visit.entered_at, segment.travel_time)
                        if index:
                            self.assertLessEqual(visits[index - 1].crossing_finished_at, visit.crossing_started_at)
                        if segment.capacity is not None:
                            occupied_count = sum(
                                other.reserved_at <= visit.reserved_at < other.crossing_finished_at
                                for other in visits
                            )
                            self.assertLessEqual(occupied_count, segment.capacity)
                    for pedestrian in pedestrians(result, name):
                        crossing = pedestrian.visits[name]
                        self.assertLess(pedestrian.appeared_at, crossing.crossing_started_at)
                        self.assertEqual(crossing.crossing_started_at % cycle, segment.green_duration)
                        for visit in visits:
                            self.assertTrue(
                                visit.crossing_finished_at <= crossing.crossing_started_at
                                or crossing.crossing_finished_at <= visit.crossing_started_at
                            )

    def test_disabling_one_pedestrian_stream_keeps_other_arrivals(self):
        _, baseline = run_simulation(seed=7, car_arrivals=[])
        _, changed = run_simulation(
            seed=7, car_arrivals=[], pedestrian_arrivals={"A": [], "B": None, "C": None},
        )
        self.assertEqual(pedestrians(changed, "A"), [])
        for name in ("B", "C"):
            self.assertEqual(
                [p.appeared_at for p in pedestrians(baseline, name)],
                [p.appeared_at for p in pedestrians(changed, name)],
            )

    def test_impossible_phase_lengths_are_rejected(self):
        for changes in ({"green_duration": 1}, {"red_duration": 4}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    replace(DEFAULT_SEGMENT_PARAMETERS["B"], **changes)


if __name__ == "__main__":
    unittest.main()
