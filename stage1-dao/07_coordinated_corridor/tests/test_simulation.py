import io
import unittest
from contextlib import redirect_stdout

import simpy

from .. import (
    CarArrival,
    CoordinatedCorridorSimulation,
    Direction,
    Kind,
)
from ..simulation import Car, LightPhase, PhaseController


def cars(result):
    return [
        user
        for user in result.participants
        if user.kind is Kind.CAR and not user.is_initial
    ]


def run_simulation(**kwargs):
    simulation = CoordinatedCorridorSimulation(
        env=simpy.Environment(),
        **kwargs,
    )
    with redirect_stdout(io.StringIO()):
        result = simulation.run()
    return simulation, result


class CoordinatedCorridorTests(unittest.TestCase):
    def test_pedestrians_appear_at_crossing_and_keep_phase_boundary_rule(self):
        _, result = run_simulation(car_arrivals=[], pedestrian_arrivals=[1, 48])
        group = [p for p in result.participants if p.kind is Kind.PEDESTRIAN]
        self.assertEqual([p.central_queue_entered_at for p in group], [1, 48])
        self.assertEqual([p.central_started_at for p in group], [48, 108])
        self.assertEqual([p.finished_at for p in group], [53, 113])

    def test_waits_for_later_arrivals_after_queue_drains(self):
        _, result = run_simulation(
            car_arrivals=[CarArrival(0, Direction.STRAIGHT), CarArrival(120, Direction.STRAIGHT)],
            pedestrian_arrivals=[], is_car_at_stop_line=True,
        )
        self.assertEqual([car.finished_at for car in cars(result)], [14, 134])
        self.assertEqual(result.finished_at, 134)
        self.assertEqual(result.count_finished(), 2)

    def test_late_cars_wait_for_next_central_green_in_fifo_order(self):
        simulation, result = run_simulation(
            car_arrivals=[CarArrival(23, Direction.STRAIGHT), CarArrival(24, Direction.STRAIGHT)],
            pedestrian_arrivals=[], is_car_at_stop_line=True,
        )
        self.assertEqual(
            [(car.central_started_at, car.central_finished_at, car.receiver_started_at, car.finished_at)
             for car in cars(result)],
            [(60, 62, 72, 74), (62, 64, 74, 76)],
        )
        self.assertEqual(result.count(Kind.CAR), 2)
        self.assertEqual(result.count_finished(), 2)
        self.assertEqual(simulation.free_places_at_end_count, {"straight": 5, "left": 3, "right": 2})

    def test_remaining_green_at_every_central_signal(self):
        for direction, phase_end, next_green in (
            (Direction.STRAIGHT, 24, 60),
            (Direction.LEFT, 36, 84),
            (Direction.RIGHT, 48, 96),
        ):
            for duration in (2, 3):
                for arrival, expected_start in (
                    (phase_end - duration, phase_end - duration),
                    (phase_end - duration + 1, next_green),
                    (phase_end, next_green),
                ):
                    with self.subTest(direction=direction, duration=duration, arrival=arrival):
                        _, result = run_simulation(
                            car_arrivals=[CarArrival(arrival, direction)],
                            pedestrian_arrivals=[1], is_car_at_stop_line=True,
                            central_crossing_length=15 * duration,
                        )
                        car = cars(result)[0]
                        self.assertEqual(car.central_started_at, expected_start)
                        self.assertEqual(car.central_finished_at, expected_start + duration)
                        pedestrian = next(p for p in result.participants if p.kind is Kind.PEDESTRIAN)
                        self.assertEqual(pedestrian.central_started_at, 48)
                        self.assertTrue(
                            car.central_finished_at <= pedestrian.central_started_at
                            or pedestrian.central_finished_at <= car.central_started_at
                        )

    def test_remaining_green_at_every_receiver(self):
        for direction, central_start, phase_end, next_green in (
            (Direction.STRAIGHT, 22, 36, 72),
            (Direction.LEFT, 34, 48, 96),
            (Direction.RIGHT, 46, 60, 108),
        ):
            for duration in (2, 3):
                for remaining in (duration, duration - 1, 0):
                    with self.subTest(direction=direction, duration=duration, remaining=remaining):
                        arrival = phase_end - remaining
                        _, result = run_simulation(
                            car_arrivals=[CarArrival(central_start, direction)],
                            pedestrian_arrivals=[], is_car_at_stop_line=True,
                            receiver_approach_length=15 * (arrival - central_start - 2),
                            receiver_crossing_length=15 * duration,
                        )
                        car = cars(result)[0]
                        expected_start = arrival if remaining == duration else next_green
                        self.assertEqual(car.receiver_arrived_at, arrival)
                        self.assertEqual(car.receiver_started_at, expected_start)
                        self.assertEqual(car.finished_at, expected_start + duration)

    def test_receiver_rechecks_remaining_green_after_blockage(self):
        # Предзаполненная машина уже ждёт у принимающего светофора.
        for release_at, expected_start in ((46, 46), (47, 96), (48, 96)):
            with self.subTest(release_at=release_at):
                _, result = run_simulation(
                    car_arrivals=[], pedestrian_arrivals=[], prefilled_left_count=1,
                    left_release_blocked_until=release_at,
                )
                car = result.participants[0]
                self.assertEqual(car.receiver_started_at, expected_start)
                self.assertEqual(car.finished_at, expected_start + 2)

    def test_capacity_release_rechecks_remaining_central_green(self):
        # Изолируем освобождение места от расписания принимающего перекрёстка:
        # t=22 — успеваем за 2 с, t=23 — времени мало, t=24 — другая фаза.
        for release_at, expected_start in ((22, 22), (23, 60), (24, 60)):
            with self.subTest(release_at=release_at):
                env = simpy.Environment()
                direction = Direction.STRAIGHT
                queue = simpy.Store(env)
                car = Car(env, queue, 1, 0, direction)
                queue.put(car)
                signal = PhaseController(
                    env, "central",
                    ((LightPhase.STRAIGHT, 24), (LightPhase.LEFT, 12),
                     (LightPhase.RIGHT, 12), (LightPhase.PEDESTRIANS, 12)),
                )
                free_places = {direction: simpy.Container(env, capacity=1, init=0)}
                capacity_changed = {direction: env.event()}

                def release():
                    yield env.timeout(release_at)
                    yield free_places[direction].put(1)
                    capacity_changed[direction].succeed()

                def travel_to_receiver(participant):
                    yield env.timeout(0)

                with redirect_stdout(io.StringIO()):
                    env.process(signal.run())
                    env.process(car.serve_crossing(signal, 30, free_places, capacity_changed, travel_to_receiver))
                    env.process(release())
                    env.run(until=25)
                    # Если времени мало, свободное место не резервируется на красный.
                    self.assertEqual(free_places[direction].level, int(release_at > 22))
                    env.run(until=expected_start + 3 if expected_start > 22 else 26)
                self.assertEqual(car.central_started_at, expected_start)
                self.assertEqual(car.central_finished_at, expected_start + 2)

    def test_prefilled_cars_finish_without_new_arrivals(self):
        simulation, result = run_simulation(
            car_arrivals=[], pedestrian_arrivals=[], prefilled_left_count=3,
        )
        self.assertEqual([car.receiver_started_at for car in result.participants], [36, 38, 40])
        self.assertTrue(all(car.is_initial and car.finished_at is not None for car in result.participants))
        self.assertEqual(result.count_finished(), 3)
        self.assertEqual(simulation.free_places_at_end_count["left"], 3)

    def test_empty_input_finishes_immediately(self):
        _, result = run_simulation(car_arrivals=[], pedestrian_arrivals=[])
        self.assertEqual(result.finished_at, 0)
        self.assertEqual(result.count_finished(), 0)

    def test_synchronized_windows(self):
        _, result = run_simulation(
            car_arrivals=[
                CarArrival(0, Direction.STRAIGHT),
                CarArrival(0, Direction.LEFT),
                CarArrival(0, Direction.RIGHT),
            ],
            pedestrian_arrivals=[],
            is_car_at_stop_line=True,
        )
        self.assertEqual(
            [
                (
                    user.central_started_at,
                    user.central_finished_at,
                    user.receiver_arrived_at,
                    user.receiver_started_at,
                    user.finished_at,
                )
                for user in cars(result)
            ],
            [
                (0, 2, 12, 12, 14),
                (24, 26, 36, 36, 38),
                (36, 38, 48, 48, 50),
            ],
        )

    def test_full_left_approach_only_blocks_left_turn(self):
        simulation, result = run_simulation(
            car_arrivals=[
                CarArrival(0, Direction.STRAIGHT),
                CarArrival(0, Direction.LEFT),
                CarArrival(0, Direction.RIGHT),
            ],
            pedestrian_arrivals=[],
            is_car_at_stop_line=True,
            prefilled_left_count=3,
            left_release_blocked_until=120,
        )
        by_direction = {user.direction: user for user in cars(result)}
        self.assertEqual(by_direction[Direction.STRAIGHT].central_started_at, 0)
        self.assertEqual(by_direction[Direction.RIGHT].central_started_at, 36)
        self.assertEqual(by_direction[Direction.LEFT].central_started_at, 204)
        initial_participants = [user for user in result.participants if user.is_initial]
        self.assertEqual(
            [user.receiver_started_at for user in initial_participants], [156, 158, 160]
        )
        self.assertEqual([user.finished_at for user in initial_participants], [158, 160, 162])
        self.assertEqual(
            simulation.free_places_at_end_count,
            {"straight": 5, "left": 3, "right": 2},
        )

    def test_random_run_conserves_vehicles_and_capacity(self):
        simulation, result = run_simulation(seed=7)
        created = [user for user in result.participants if user.kind is Kind.CAR]
        self.assertTrue(created)
        self.assertTrue(
            all(user.finished_at is not None for user in result.participants)
        )
        self.assertEqual(
            sum(result.completed_by_direction().values()), len(created)
        )
        for direction, capacity_count in (
            (Direction.STRAIGHT, 5),
            (Direction.LEFT, 3),
            (Direction.RIGHT, 2),
        ):
            direction_cars = [car for car in created if car.direction is direction]
            for car in direction_cars:
                used_count = sum(
                    other.central_started_at <= car.central_started_at < other.finished_at
                    for other in direction_cars
                )
                self.assertLessEqual(used_count, capacity_count)
        self.assertEqual(simulation.free_places_at_end_count, {"straight": 5, "left": 3, "right": 2})

    def test_direction_frequencies(self):
        _, result = run_simulation(
            seed=42, horizon_ms=36000, pedestrian_arrivals=[]
        )
        generated = cars(result)
        self.assertGreater(len(generated), 3000)
        for direction, probability in (
            (Direction.STRAIGHT, 0.5),
            (Direction.LEFT, 0.3),
            (Direction.RIGHT, 0.2),
        ):
            actual = (
                sum(user.direction == direction for user in generated)
                / len(generated)
            )
            self.assertAlmostEqual(actual, probability, delta=0.04)


if __name__ == "__main__":
    unittest.main()
