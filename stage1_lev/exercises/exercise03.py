"""Задание 3: независимые экспоненциальные потоки и завершение всех поездок"""

from random import Random

import simpy

from ..common.data import Participant, validate_number
from ..common.flows import source
from ..common.monitoring import Result
from .exercise02 import move


def car_source(env, result, rate_per_hour, horizon, rng, distance, speed):
    number = 0

    def spawn(_):
        nonlocal number
        number += 1
        car = Participant(f"car-{number}", "car", float(env.now))
        result.participants.append(car)
        result.record(env.now, car.id, car.kind, "appeared")
        env.process(move(env, car, distance, speed, result))

    return source(env, rate_per_hour, horizon, rng, None, spawn=spawn)


def pedestrian_source(env, result, rate_per_hour, horizon, rng):
    number = 0

    def spawn(_):
        nonlocal number
        number += 1
        now = float(env.now)
        pedestrian = Participant(f"pedestrian-{number}", "pedestrian", now, now)
        result.participants.append(pedestrian)
        result.record(now, pedestrian.id, pedestrian.kind, "appeared")
        result.record(now, pedestrian.id, pedestrian.kind, "arrived")

    return source(env, rate_per_hour, horizon, rng, None, spawn=spawn)


def run(seed=42, *, horizon=600, car_rate=360, pedestrian_rate=180,
        car_distance=150, car_speed=15):
    validate_number("Горизонт генерации", horizon)
    validate_number("Интенсивность автомобилей", car_rate, allow_zero=True)
    validate_number("Интенсивность пешеходов", pedestrian_rate, allow_zero=True)
    validate_number("Длина дороги", car_distance, allow_zero=True)
    validate_number("Скорость автомобиля", car_speed)
    env = simpy.Environment()
    result = Result(exercise=3, seed=seed)
    env.process(car_source(env, result, car_rate, horizon, Random(seed),
                           car_distance, car_speed))
    env.process(pedestrian_source(env, result, pedestrian_rate, horizon, Random(seed + 1)))

    env.run()
    result.finished_at = float(env.now)
    return result


if __name__ == "__main__":
    from ..common.reporting import display

    display(run(), show_log=True)
