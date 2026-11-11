import io
import unittest
from contextlib import redirect_stdout

import simpy

from .. import DownstreamSpillbackSimulation, Kind


def cars(result):
    return [user for user in result.participants if user.kind is Kind.CAR]


def pedestrians(result):
    return [user for user in result.participants if user.kind is Kind.PEDESTRIAN]


def run_simulation(**kwargs):
    simulation = DownstreamSpillbackSimulation(
        env=simpy.Environment(),
        **kwargs,
    )
    with redirect_stdout(io.StringIO()):
        result = simulation.run()
    return simulation, result


class DownstreamSpillbackTests(unittest.TestCase):
    def test_pedestrians_appear_at_crossing_and_keep_phase_boundary_rule(self):
        _, result = run_simulation(car_arrivals=[], pedestrian_arrivals=[1, 20])
        group = pedestrians(result)
        self.assertEqual([p.queue_entered_at for p in group], [1, 20])
        self.assertEqual([p.crossing_started_at for p in group], [20, 50])
        self.assertEqual([p.crossing_finished_at for p in group], [25, 55])

    def test_waits_for_later_arrivals_after_queue_drains(self):
        _, result = run_simulation(car_arrivals=[0, 120], pedestrian_arrivals=[])
        self.assertEqual([car.finished_at for car in cars(result)], [22, 142])
        self.assertEqual(result.finished_at, 142)
        self.assertEqual(result.count_finished(), 2)

    def test_capacity_release_rechecks_vehicle_phase_and_remaining_green(self):
        # Две машины делят одно место за переходом. Первая въезжает в t=0,
        # тратит 2 с на переход и downstream_length / 15 с на участок после него.
        # Вторая должна после освобождения места снова проверить светофор:
        # t=18 — хватает ровно 2 с; t=19 — зелёный ещё есть, но времени мало;
        # t=20 — уже пешеходная фаза. В двух последних случаях ждём t=30.
        for downstream_length, expected_start in ((240, 18), (255, 30), (270, 30)):
            with self.subTest(downstream_length=downstream_length):
                simulation, result = run_simulation(
                    car_arrivals=[0, 0], pedestrian_arrivals=[1], is_car_at_stop_line=True,
                    downstream_capacity_count=1, downstream_length=downstream_length,
                )
                self.assertEqual(cars(result)[1].crossing_started_at, expected_start)
                self.assertEqual(cars(result)[1].crossing_finished_at, expected_start + 2)
                self.assertEqual(result.count(Kind.CAR), 2)
                self.assertEqual(result.count_finished(), 3)
                self.assertEqual(simulation.free_places_at_end_count, 1)
                pedestrian = pedestrians(result)[0]
                self.assertEqual(pedestrian.crossing_started_at, 20)
                for car in cars(result):
                    self.assertTrue(
                        car.crossing_finished_at <= pedestrian.crossing_started_at
                        or pedestrian.crossing_finished_at <= car.crossing_started_at
                    )

    def test_arrival_rechecks_remaining_green_for_actual_crossing_duration(self):
        for crossing_length in (30, 45):
            duration = crossing_length / 15
            for queue_at, expected_start in ((20 - duration, 20 - duration),
                                             (21 - duration, 30), (20, 30)):
                with self.subTest(crossing_length=crossing_length, queue_at=queue_at):
                    _, result = run_simulation(
                        car_arrivals=[queue_at], pedestrian_arrivals=[1],
                        is_car_at_stop_line=True, car_crossing_length=crossing_length,
                    )
                    car = cars(result)[0]
                    self.assertEqual(car.crossing_started_at, expected_start)
                    self.assertEqual(car.crossing_finished_at, expected_start + duration)

    def test_empty_input_finishes_immediately(self):
        simulation, result = run_simulation(car_arrivals=[], pedestrian_arrivals=[])
        self.assertEqual(result.finished_at, 0)
        self.assertEqual(result.count_finished(), 0)
        self.assertEqual(simulation.free_places_at_end_count, 5)

    def test_closed_exit_blocks_after_five_reservations(self):
        simulation, result = run_simulation(
            car_arrivals=[0] * 8,
            pedestrian_arrivals=[1, 1],
            is_car_at_stop_line=True,
            is_pedestrian_at_crossing=True,
            exit_blocked_until=60,
        )
        self.assertEqual(
            [user.crossing_started_at for user in cars(result)],
            [0, 2, 4, 6, 8, 60, 62, 64],
        )
        self.assertEqual(
            [user.finished_at for user in cars(result)[:5]], [60] * 5
        )
        self.assertEqual(
            [user.crossing_started_at for user in pedestrians(result)], [20, 20]
        )
        self.assertGreater(cars(result)[5].downstream_blocked_time, 0)
        for car in cars(result):
            used_count = sum(
                other.crossing_started_at <= car.crossing_started_at < other.finished_at
                for other in cars(result)
            )
            self.assertLessEqual(used_count, 5)
        self.assertEqual(simulation.free_places_at_end_count, 5)

    def test_identical_input_streams_for_open_and_blocked_exit(self):
        _, opened = run_simulation(seed=7)
        _, blocked = run_simulation(
            seed=7, exit_blocked_until=60
        )
        self.assertEqual(
            [
                (user.id, user.appeared_at)
                for user in opened.participants
            ],
            [
                (user.id, user.appeared_at)
                for user in blocked.participants
            ],
        )


if __name__ == "__main__":
    unittest.main()
