"""Задание 2: два независимых маршрута с общим стартом"""

import simpy

from .data import Participant, Result, validate_number


def move(env, participant, distance, speed, journal):
    """Один timeout соответствует всему пути при постоянной скорости"""
    validate_number("Расстояние", distance, allow_zero=True)
    validate_number("Скорость", speed)
    journal.record(env.now, participant.id, participant.kind, "started")
    yield env.timeout(distance / speed)
    participant.arrived_at = float(env.now)
    journal.record(env.now, participant.id, participant.kind, "arrived")


def run(*, car_distance=150, car_speed=15, pedestrian_distance=28, pedestrian_speed=1.4):
    env = simpy.Environment()
    result = Result(exercise=2)
    routes = (("car", car_distance, car_speed),
              ("pedestrian", pedestrian_distance, pedestrian_speed))
    for kind, distance, speed in routes:
        participant = Participant(f"{kind}-1", kind, appeared_at=0.0)
        result.participants.append(participant)
        env.process(move(env, participant, distance, speed, result))

    env.run()
    result.finished_at = float(env.now)
    return result


if __name__ == "__main__":
    from .reporting import display

    display(run(), show_log=True)
