"""Задание 4: FIFO автомобилей и групповой переход пешеходов"""

from random import Random

import simpy

from ..common.data import Pedestrian, Vehicle, validate_number
from ..common.flows import arrival_clock, prepare_arrivals
from ..common.monitoring import CrossingResult
from ..common.signals import BiPhaseController


class Crossing:
    """Состояние перехода"""

    def __init__(self, env, result, signal, approach_time):
        self.env = env
        self.result = result
        self.signal = signal
        self.approach_time = approach_time
        self.cars = simpy.Store(env)
        self.pedestrians = simpy.Store(env)
        self.car_arrived = env.event()
        self.done = env.event()
        self.active_count = 0
        self.has_sources_finished = False
        self.counts = {"car": 0, "pedestrian": 0}

    def spawn(self, kind):
        self.counts[kind] += 1
        participant_type = Vehicle if kind == "car" else Pedestrian
        participant = participant_type(
            f"{kind}-{self.counts[kind]}", kind, float(self.env.now),
        )
        self.result.participants.append(participant)
        self.result.record(self.env.now, participant.id, kind, "appeared")
        self.active_count += 1
        if kind == "car":
            self.env.process(approach(self.env, participant, self))
        else:
            self.enqueue(participant)

    def enqueue(self, participant):
        participant.queue_entered_at = float(self.env.now)
        self.result.record(self.env.now, participant.id, participant.kind, "queued")
        if participant.kind == "car":
            self.cars.put(participant)
            previous = self.car_arrived
            self.car_arrived = self.env.event()
            previous.succeed()
        else:
            self.pedestrians.put(participant)

    def start_crossing(self, participant):
        participant.queue_left_at = float(self.env.now)
        participant.queue_wait_total = self.env.now - participant.queue_entered_at
        self.result.record(self.env.now, participant.id, participant.kind, "crossing_started")

    def finish(self, participant):
        participant.arrived_at = float(self.env.now)
        self.result.record(self.env.now, participant.id, participant.kind, "arrived")
        self.active_count -= 1
        self.finish_if_empty()

    def finish_if_empty(self):
        if self.has_sources_finished and self.active_count == 0 and not self.done.triggered:
            self.done.succeed()


def approach(env, participant, crossing):
    yield env.timeout(crossing.approach_time)
    crossing.enqueue(participant)


def serve_cars(env, crossing):
    while True:
        while not crossing.cars.items:
            yield crossing.car_arrived
        while not crossing.signal.allows("car", 2):
            yield crossing.signal.changed
        # Голова очереди остаётся в Store до фактического начала проезда
        participant = yield crossing.cars.get()
        crossing.start_crossing(participant)
        yield env.timeout(2)
        crossing.finish(participant)


def serve_pedestrians(env, crossing):
    while True:
        yield crossing.signal.changed
        if crossing.signal.phase != "pedestrian":
            continue
        phase_start = env.now
        group = []
        while (crossing.pedestrians.items
               and crossing.pedestrians.items[0].queue_entered_at < phase_start):
            participant = yield crossing.pedestrians.get()
            crossing.start_crossing(participant)
            group.append(participant)
        if group:
            yield env.timeout(7 / 1.4)
            for participant in group:
                crossing.finish(participant)


def source(env, crossing, kind, rate, horizon, rng, arrivals):
    if arrivals is not None:
        for moment in arrivals:
            yield env.timeout(moment - env.now)
            crossing.spawn(kind)
    else:
        for waiting, is_arrival in arrival_clock(env, rate, horizon, rng):
            yield waiting
            if is_arrival:
                crossing.spawn(kind)


def run(seed=42, *, horizon=600, car_rate=360, pedestrian_rate=180,
        car_arrivals=None, pedestrian_arrivals=None, cars_at_crossing=False):
    """
    Расписания заменяют случайный поток своего типа, [] отключает его.
    По умолчанию времена автомобилей относятся ко входу на участок.
    cars_at_crossing=True задаёт прибытия прямо к стоп-линии для проверок
    """
    validate_number("Горизонт генерации", horizon)
    validate_number("Интенсивность автомобилей", car_rate, allow_zero=True)
    validate_number("Интенсивность пешеходов", pedestrian_rate, allow_zero=True)
    car_arrivals = prepare_arrivals(car_arrivals)
    pedestrian_arrivals = prepare_arrivals(pedestrian_arrivals)
    env = simpy.Environment()
    result = CrossingResult(exercise=4, seed=seed)
    signal = BiPhaseController(env, result)
    crossing = Crossing(env, result, signal, 0 if cars_at_crossing else 150 / 15)
    env.process(signal.run())
    env.process(serve_cars(env, crossing))
    env.process(serve_pedestrians(env, crossing))
    sources = [
        env.process(source(env, crossing, "car", car_rate, horizon, Random(seed), car_arrivals)),
        env.process(source(env, crossing, "pedestrian", pedestrian_rate, horizon,
                           Random(seed + 1), pedestrian_arrivals)),
    ]
    # Сначала дожидаемся источников, затем всех появившихся участников
    env.run(until=env.all_of(sources))
    crossing.has_sources_finished = True
    crossing.finish_if_empty()
    env.run(until=crossing.done)
    result.finished_at = float(env.now)
    return result


def demo():
    return run(car_arrivals=[0, 0, 0], pedestrian_arrivals=[1, 1], cars_at_crossing=True)


if __name__ == "__main__":
    from ..common.reporting import display

    display(run(), show_log=True)
