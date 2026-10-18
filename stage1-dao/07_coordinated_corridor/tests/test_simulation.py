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
    def test_waits_for_later_arrivals_after_queue_drains(self):
        _, result = run_simulation(
            car_arrivals=[CarArrival(0, Direction.STRAIGHT), CarArrival(120, Direction.STRAIGHT)],
            pedestrian_arrivals=[], is_car_at_stop_line=True,
        )
        self.assertEqual([car.finished_at for car in cars(result)], [14, 134])
        self.assertEqual(result.finished_at, 134)
        self.assertEqual(result.count_finished(), 2)

    def test_car_can_finish_after_central_and_receiver_phase_ends(self):
        simulation, result = run_simulation(
            car_arrivals=[CarArrival(23, Direction.STRAIGHT), CarArrival(24, Direction.STRAIGHT)],
            pedestrian_arrivals=[], is_car_at_stop_line=True,
        )
        self.assertEqual(
            [(car.central_started_at, car.central_finished_at, car.receiver_started_at, car.finished_at)
             for car in cars(result)],
            [(23, 25, 35, 37), (60, 62, 72, 74)],
        )
        self.assertEqual(result.count(Kind.CAR), 2)
        self.assertEqual(result.count_finished(), 2)
        self.assertEqual(simulation.free_places_at_end_count, {"straight": 5, "left": 3, "right": 2})

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
