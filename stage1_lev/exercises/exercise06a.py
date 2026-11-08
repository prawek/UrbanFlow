"""Задание 6а: линейный маршрут с местными двухфазными светофорами.

Эта упрощённая модель не выбирает повороты; упражнение 6 использует её же.
Общая модель разветвлённой дороги находится в common.roads и common.corridor.
"""

from dataclasses import asdict
from random import Random

import simpy

from ..common.data import Pedestrian, Vehicle, validate_number
from ..common.flows import prepare_arrivals, source
from ..common.monitoring import RoadResult
from ..common.roads import SegmentConfig
from ..common.signals import BiPhaseController


class RoadSegment:
    """Упрощённый участок с одной автомобильной очередью для заданий 6 и 6а."""

    def __init__(self, env, result, config):
        self.env = env
        self.result = result
        self.config = config
        self.name = config.name
        self.capacity = config.capacity
        self.travel_time = config.approach_length / config.speed
        self.intersection_travel_time = config.intersection_length / config.speed
        self.next_segment = None
        self.free_slots = (simpy.Container(env, capacity=self.capacity, init=self.capacity)
                           if self.capacity is not None else None)
        self.stop_line_queue = simpy.Store(env)
        self.pedestrian_queue = simpy.Store(env)
        self.car_arrived = env.event()
        self.signal = (BiPhaseController(env, result, config.green_duration, config.red_duration,
                                         name=f"traffic_light:{self.name}")
                       if self.intersection_travel_time else None)
        self.crossing_open_at = 0.0
        self.exit_open_at = 0.0
        self.occupied = set()
        self.visits = {}

    def snapshot(self):
        free = int(self.free_slots.level) if self.free_slots is not None else None
        return self.result.record_occupancy(
            self.env.now, self.name, self.capacity, len(self.occupied), free,
            len(self.stop_line_queue.items), len(self.pedestrian_queue.items),
        )

    def record(self, participant, action):
        self.result.record(self.env.now, participant.id, participant.kind, f"{action}:{self.name}")

    def enter(self, participant):
        participant.current_segment = self
        participant.velocity = self.config.speed
        self.occupied.add(participant.id)
        self.visits[participant.id] = self.result.new_visit(
            participant.id, self.name, float(self.env.now),
        )
        self.record(participant, "entered")
        self.snapshot()

    def leave(self, participant):
        self.occupied.remove(participant.id)
        if self.free_slots is not None:
            self.free_slots.put(1)
        self.visits[participant.id].exited_at = float(self.env.now)
        self.record(participant, "left")
        self.snapshot()


class RoadNetwork:
    def __init__(self, exercise, configs, *, seed=42, crossing_closed_until=None,
                 exit_closed_until=None):
        configs = tuple(configs)
        if not configs or len({c.name for c in configs}) != len(configs):
            raise ValueError("Нужен непустой маршрут с разными именами участков")
        for config in configs:
            config.validate()
            if config.intersection_length:
                validate_number("Зелёная фаза", config.green_duration)
                validate_number("Красная фаза", config.red_duration)
                if config.intersection_length / config.speed > config.green_duration:
                    raise ValueError("Проезд перекрёстка должен помещаться в зелёную фазу")
                if config.red_duration < 5:
                    raise ValueError("Красная фаза должна вмещать переход пешеходов за 5 с")
        if configs[0].capacity is not None:
            raise ValueError("В заданиях 6 и 6а вместимость входного участка не ограничена")
        if any(c.intersection_length == 0 for c in configs[:-1]):
            raise ValueError("У промежуточного участка должен быть перекрёсток")
        self.env = simpy.Environment()
        self.result = RoadResult(exercise=exercise, seed=seed,
                                 parameters=[asdict(c) for c in configs])
        self.segments = [RoadSegment(self.env, self.result, c) for c in configs]
        for previous, following in zip(self.segments, self.segments[1:]):
            previous.next_segment = following
        for mapping, attribute in ((crossing_closed_until, "crossing_open_at"),
                                   (exit_closed_until, "exit_open_at")):
            mapping = mapping or {}
            if set(mapping) - {s.name for s in self.segments}:
                raise ValueError("Запрет задан для неизвестного участка")
            for segment in self.segments:
                value = mapping.get(segment.name, 0)
                validate_number("Время открытия", value, allow_zero=True)
                setattr(segment, attribute, value)
        self.result.closures = {
            segment.name: {"crossing_open_at": segment.crossing_open_at,
                           "exit_open_at": segment.exit_open_at}
            for segment in self.segments
        }
        self.active_count = 0
        self.has_sources_finished = False
        self.done = self.env.event()
        self.counts = {}
        for segment in self.segments:
            segment.snapshot()
            if segment.signal is not None:
                self.env.process(segment.signal.run())
                self.env.process(self.serve_cars(segment))
                self.env.process(self.serve_pedestrians(segment))

    def spawn(self, kind, segment, *, at_stop=False):
        key = "car" if kind == "car" else f"pedestrian-{segment.name}"
        self.counts[key] = self.counts.get(key, 0) + 1
        record_type = Vehicle if kind == "car" else Pedestrian
        participant = record_type(f"{key}-{self.counts[key]}", kind, float(self.env.now),
                                   local_segment=segment.name if kind == "pedestrian" else "")
        self.result.participants.append(participant)
        self.result.record(self.env.now, participant.id, kind, "appeared")
        self.active_count += 1
        if kind == "car":
            segment.enter(participant)
            self.env.process(self.approach(participant, segment, at_stop=at_stop))
        else:
            participant.queue_entered_at = float(self.env.now)
            segment.pedestrian_queue.put(participant)
            segment.record(participant, "queued")
            segment.snapshot()

    def finish(self, participant):
        participant.current_segment = None
        participant.arrived_at = float(self.env.now)
        self.result.record(self.env.now, participant.id, participant.kind, "arrived")
        self.active_count -= 1
        self.finish_if_empty()

    def finish_if_empty(self):
        if self.has_sources_finished and self.active_count == 0 and not self.done.triggered:
            self.done.succeed()

    def run(self, sources, *, observe_until=None):
        self.env.run(until=self.env.all_of(sources))
        self.has_sources_finished = True
        self.finish_if_empty()
        self.env.run(until=self.done)
        # Для сравнения расписаний светофоров можно продолжить наблюдение
        # до фиксированного времени, не обрезая поздних участников
        if observe_until is not None and self.env.now < observe_until:
            self.env.run(until=observe_until)
        self.result.finished_at = float(self.env.now)
        self.result.capture_final_segments(self.segments)
        return self.result

    def approach(self, participant, segment, *, at_stop=False):
        env = self.env
        yield env.timeout(0 if at_stop else segment.travel_time)
        visit = segment.visits[participant.id]
        visit.queue_entered_at = float(env.now)
        if participant.queue_entered_at is None:
            participant.queue_entered_at = float(env.now)
        if segment.signal is None:
            segment.record(participant, "exit_ready")
            if env.now < segment.exit_open_at:
                yield env.timeout(segment.exit_open_at - env.now)
            participant.queue_wait_total += env.now - visit.queue_entered_at
            segment.leave(participant)
            self.finish(participant)
            return
        segment.stop_line_queue.put(participant)
        segment.record(participant, "queued")
        segment.snapshot()
        previous = segment.car_arrived
        segment.car_arrived = env.event()
        previous.succeed()

    def reserve_next(self, segment, participant):
        """Ожидание места не занимает переход. При потере разрешения заявка отменяется"""
        env = self.env
        following = segment.next_segment
        while True:
            if env.now < segment.crossing_open_at:
                yield env.timeout(segment.crossing_open_at - env.now)
                continue
            if not segment.signal.allows("car", segment.intersection_travel_time):
                yield segment.signal.changed
                continue
            if following is None or following.free_slots is None:
                return
            request = following.free_slots.get(1)
            following.snapshot()
            if not request.triggered:
                started_at = env.now
                segment.record(participant, "capacity_wait")
                yield request | segment.signal.changed
                segment.visits[participant.id].capacity_wait += env.now - started_at
            else:
                yield request
            # Даже выданное место не даёт права выехать на красный.
            # Проверяем triggered, а не состав AnyOf: оба события могут сработать в один момент
            is_allowed = segment.signal.allows("car", segment.intersection_travel_time)
            if request.triggered and is_allowed:
                following.record(participant, "reserved")
                following.snapshot()
                return
            if request.triggered:
                following.free_slots.put(1)
            else:
                request.cancel()
            following.snapshot()

    def serve_cars(self, segment):
        env = self.env
        while True:
            while not segment.stop_line_queue.items:
                yield segment.car_arrived
            participant = segment.stop_line_queue.items[0]
            yield from self.reserve_next(segment, participant)
            # Очередь обслуживает один процесс, поэтому голова за время ожидания не меняется
            yield segment.stop_line_queue.get()
            visit = segment.visits[participant.id]
            visit.crossing_started_at = float(env.now)
            participant.queue_wait_total += env.now - visit.queue_entered_at
            if participant.queue_left_at is None:
                participant.queue_left_at = float(env.now)
            segment.record(participant, "crossing_started")
            segment.snapshot()
            yield env.timeout(segment.intersection_travel_time)
            visit.crossing_finished_at = float(env.now)
            segment.record(participant, "crossing_finished")
            segment.leave(participant)
            if segment.next_segment is None:
                self.finish(participant)
            else:
                segment.next_segment.enter(participant)
                self.env.process(self.approach(participant, segment.next_segment))

    def serve_pedestrians(self, segment):
        env = self.env
        while True:
            yield segment.signal.changed
            if segment.signal.phase != "pedestrian":
                continue
            phase_start = env.now
            group = []
            while (segment.pedestrian_queue.items
                   and segment.pedestrian_queue.items[0].queue_entered_at < phase_start):
                participant = yield segment.pedestrian_queue.get()
                participant.queue_left_at = float(env.now)
                participant.queue_wait_total = env.now - participant.queue_entered_at
                segment.record(participant, "crossing_started")
                group.append(participant)
            segment.snapshot()
            if group:
                yield env.timeout(5)
                for participant in group:
                    segment.record(participant, "crossing_finished")
                    self.finish(participant)


def simulate(exercise, configs, seed=42, *, horizon=600, car_rate=360, pedestrian_rate=180,
             car_arrivals=None, pedestrian_arrivals=None, cars_at_crossing=False,
             crossing_closed_until=None, exit_closed_until=None, observe_until=None):
    validate_number("Горизонт генерации", horizon)
    validate_number("Интенсивность автомобилей", car_rate, allow_zero=True)
    validate_number("Интенсивность пешеходов", pedestrian_rate, allow_zero=True)
    if observe_until is not None:
        validate_number("Конец наблюдения", observe_until, allow_zero=True)
    network = RoadNetwork(exercise, configs, seed=seed, crossing_closed_until=crossing_closed_until,
                          exit_closed_until=exit_closed_until)
    local = [s for s in network.segments if s.signal is not None]
    if pedestrian_arrivals is not None and set(pedestrian_arrivals) - {s.name for s in local}:
        raise ValueError("Пешеходное расписание задано для неизвестного перехода")
    prepared = {s.name: prepare_arrivals(pedestrian_arrivals.get(s.name, []))
                for s in local} if pedestrian_arrivals is not None else {}
    car_arrivals = prepare_arrivals(car_arrivals)
    sources = [network.env.process(source(
        network.env, car_rate, horizon, Random(seed), car_arrivals,
        spawn=lambda _: network.spawn("car", network.segments[0], at_stop=cars_at_crossing),
    ))]
    for index, segment in enumerate(local, start=1):
        sources.append(network.env.process(source(
            network.env, pedestrian_rate, horizon, Random(seed + index),
            prepared.get(segment.name),
            spawn=lambda _, segment=segment: network.spawn("pedestrian", segment),
        )))
    return network.run(sources, observe_until=observe_until)


SEGMENTS = (
    SegmentConfig("A", 150, 30, 15, green_duration=20, red_duration=10),
    SegmentConfig("B", 80, 30, 10, capacity=5, green_duration=27, red_duration=13),
    SegmentConfig("C", 120, 40, 10, capacity=3, green_duration=17, red_duration=19),
)


def run(seed=42, *, segments=SEGMENTS, horizon=600, car_rate=360, pedestrian_rate=180,
        car_arrivals=None, pedestrian_arrivals=None, cars_at_crossing=False,
        crossing_closed_until=None, observe_until=None):
    return simulate("6a", segments, seed, horizon=horizon, car_rate=car_rate,
                    pedestrian_rate=pedestrian_rate, car_arrivals=car_arrivals,
                    pedestrian_arrivals=pedestrian_arrivals, cars_at_crossing=cars_at_crossing,
                    crossing_closed_until=crossing_closed_until, observe_until=observe_until)


def demo():
    return run(car_arrivals=[0], pedestrian_arrivals={})


def spillback_demo():
    return run(car_arrivals=[0] * 9, cars_at_crossing=True,
               pedestrian_arrivals={"A": [1], "B": [1], "C": [1]},
               crossing_closed_until={"C": 180})


if __name__ == "__main__":
    from ..common.reporting import display

    display(run(), show_log=True)
