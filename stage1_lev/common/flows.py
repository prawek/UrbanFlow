"""Случайные появления участников и проверка заданных расписаний"""

from .data import CarArrival, validate_number


def arrival_clock(env, rate_per_hour, horizon, rng):
    """Планирует пуассоновский поток с интенсивностью rate_per_hour участников в час.

    Каждая пара содержит SimPy timeout и флаг: создавать ли участника после
    ожидания. Перед первым появлением тоже выбирается экспоненциальный интервал.
    Последний timeout доводит часы до horizon и имеет флаг False: новый участник
    уже не создаётся. При нулевой интенсивности выдаётся только это ожидание.
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


def source(env, rate, horizon, rng, arrivals, *, spawn):
    """Вызывает spawn для каждого появления, не ожидая завершения его поездки.

    Расписание содержит времена или CarArrival; spawn получает запись расписания.
    При arrivals=None моменты выбирает arrival_clock, а spawn получает None.
    Явное расписание воспроизводится целиком; horizon ограничивает случайный поток.
    Если упражнение ограничивает и расписание, оно отбирает записи до запуска source.
    Завершение поездок ждёт сама модель.
    """
    if arrivals is None:
        for waiting, is_arrival in arrival_clock(env, rate, horizon, rng):
            yield waiting
            if is_arrival:
                spawn(None)
    else:
        for arrival in arrivals:
            moment = arrival.time if isinstance(arrival, CarArrival) else arrival
            yield env.timeout(moment - env.now)
            spawn(arrival)


