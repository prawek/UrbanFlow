"""Случайные появления участников и проверка заданных расписаний"""

from .data import validate_number


def arrival_clock(env, rate_per_hour, horizon, rng):
    """
    Выдаёт ожидания и признак появления внутри [0, horizon).
    Нулевая интенсивность отключает поток. Перед первым участником тоже
    выбирается интервал. В конце сохраняем время наблюдения до горизонта
    """
    if rate_per_hour == 0:
        yield env.timeout(horizon - env.now), False
        return
    while env.now < horizon:
        interval = rng.expovariate(rate_per_hour / 3600)
        if env.now + interval >= horizon:
            yield env.timeout(horizon - env.now), False
            return
        yield env.timeout(interval), True


def prepare_arrivals(arrivals):
    if arrivals is None:
        return None
    arrivals = list(arrivals)
    for moment in arrivals:
        validate_number("Время появления", moment, allow_zero=True)
    return sorted(arrivals)


