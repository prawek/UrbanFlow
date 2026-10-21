"""Задание 1: процесс светофора с циклом 30/5/25 секунд"""

from itertools import cycle

import simpy

from .data import Result, validate_number


def traffic_light(env, green_duration, yellow_duration, red_duration, *, journal=None):
    """
    Записывает включение фазы перед ожиданием её окончания.
    Без журнала печатает переключения; функцию можно передать в env.process
    """
    durations = (green_duration, yellow_duration, red_duration)
    for duration in durations:
        validate_number("Длительность фазы", duration)
    phases = zip(("green", "yellow", "red"), durations)
    for color, duration in cycle(phases):
        if journal is None:
            print(f"t={env.now:g} s | traffic_light | {color}")
        else:
            journal.record(env.now, "traffic_light", "signal", color)
        yield env.timeout(duration)


def run(*, until=180, green_duration=30, yellow_duration=5, red_duration=25):
    validate_number("Время моделирования", until)
    result = Result(exercise=1)
    env = simpy.Environment()
    env.process(traffic_light(
        env, green_duration, yellow_duration, red_duration, journal=result,
    ))

    env.run(until=until)
    result.finished_at = float(env.now)
    return result


if __name__ == "__main__":
    from .reporting import display

    display(run(), show_log=True)
