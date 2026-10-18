import io
import unittest
from contextlib import redirect_stdout

import simpy

from .. import Kind, SignalizedCrossingSimulation


def cars(result):
    return [p for p in result.participants if p.kind is Kind.CAR]


def pedestrians(result):
    return [p for p in result.participants if p.kind is Kind.PEDESTRIAN]


def run_simulation(**kwargs):
    with redirect_stdout(io.StringIO()):
        simulation = SignalizedCrossingSimulation(**kwargs, env=simpy.Environment())
        return simulation.run()


class SignalizedCrossingTests(unittest.TestCase):
    def test_empty_input_finishes_immediately(self):
        result = run_simulation(car_arrivals=[], pedestrian_arrivals=[])
        self.assertEqual(result.finished_at, 0)
        self.assertEqual(result.count_finished(), 0)

    def test_waits_for_later_arrivals_after_queue_drains(self):
        result = run_simulation(car_arrivals=[0, 120], pedestrian_arrivals=[])
        self.assertEqual([car.crossing_finished_at for car in cars(result)], [12, 132])
        self.assertEqual(result.finished_at, 132)
        self.assertEqual(result.count_finished(), 2)

    def test_fifo_and_pedestrian_group(self):
        result = run_simulation(car_arrivals=[0, 0, 0], pedestrian_arrivals=[1, 1])
        self.assertEqual(
            [(user.crossing_started_at, user.crossing_finished_at) for user in cars(result)],
            [(10, 12), (12, 14), (14, 16)],
        )
        self.assertEqual(
            [(user.crossing_started_at, user.crossing_finished_at) for user in pedestrians(result)],
            [(20, 25), (20, 25)],
        )

    def test_phase_boundary_is_rechecked(self):
        result = run_simulation(car_arrivals=[18, 19], pedestrian_arrivals=[1, 20, 21])
        self.assertEqual([user.crossing_started_at for user in cars(result)], [30, 32])
        self.assertEqual([user.crossing_started_at for user in pedestrians(result)], [20, 50, 50])

    def test_pedestrian_joins_queue_at_appearance(self):
        result = run_simulation(car_arrivals=[], pedestrian_arrivals=[1])
        pedestrian = pedestrians(result)[0]
        self.assertEqual(pedestrian.queue_entered_at, pedestrian.appeared_at)
        self.assertEqual(
            (pedestrian.crossing_started_at, pedestrian.crossing_finished_at),
            (20, 25),
        )

    def test_car_waits_when_remaining_green_is_too_short(self):
        result = run_simulation(car_arrivals=[9, 10], pedestrian_arrivals=[])
        self.assertEqual(
            [(car.crossing_started_at, car.crossing_finished_at) for car in cars(result)],
            [(30, 32), (32, 34)],
        )

    def test_car_can_finish_exactly_at_vehicle_phase_end(self):
        result = run_simulation(car_arrivals=[8, 8], pedestrian_arrivals=[])
        self.assertEqual(
            [(car.crossing_started_at, car.crossing_finished_at) for car in cars(result)],
            [(18, 20), (30, 32)],
        )

    def test_car_and_pedestrian_crossings_do_not_overlap(self):
        result = run_simulation(car_arrivals=[39], pedestrian_arrivals=[21])
        car = cars(result)[0]
        pedestrian = pedestrians(result)[0]
        self.assertEqual(
            (pedestrian.crossing_started_at, pedestrian.crossing_finished_at),
            (50, 55),
        )
        self.assertEqual((car.crossing_started_at, car.crossing_finished_at), (60, 62))
        self.assertLessEqual(pedestrian.crossing_finished_at, car.crossing_started_at)

    def test_random_streams_are_fully_drained(self):
        result = run_simulation(seed=7)
        self.assertGreaterEqual(result.finished_at, 600)
        self.assertTrue(all(user.crossing_finished_at is not None for user in result.participants))


if __name__ == "__main__":
    unittest.main()
