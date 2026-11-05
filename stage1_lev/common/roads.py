"""Общие процессы участков для заданий 6 и 6а"""

from dataclasses import asdict, dataclass, field
from random import Random

import simpy

from .data import CrossingParticipant, CrossingResult, validate_number
from .flows import arrival_clock, prepare_arrivals
from .signals import Signal


@dataclass(frozen=True)
class SegmentConfig:
    name: str
    approach_length: float
    intersection_length: float
    speed: float
    capacity: int | None = None
    green_duration: float = 20
    red_duration: float = 10

    def validate(self):
        if not self.name:
            raise ValueError("У участка должно быть имя")
        validate_number("Длина подъезда", self.approach_length, allow_zero=True)
        validate_number("Длина перекрёстка", self.intersection_length, allow_zero=True)
        validate_number("Скорость", self.speed)
        if self.capacity is not None and (type(self.capacity) is not int or self.capacity <= 0):
            raise ValueError("Вместимость должна быть положительным целым числом")
        if self.intersection_length:
            validate_number("Зелёная фаза", self.green_duration)
            validate_number("Красная фаза", self.red_duration)
            if self.intersection_length / self.speed > self.green_duration:
                raise ValueError("Проезд перекрёстка должен помещаться в зелёную фазу")
            if self.red_duration < 5:
                raise ValueError("Красная фаза должна вмещать переход пешеходов за 5 с")


@dataclass
class RoadParticipant(CrossingParticipant):
    current_segment: object | None = field(default=None, repr=False, compare=False,
                                           metadata={"export": False})
    local_segment: str = ""
    queue_wait_total: float = 0.0

    @property
    def queue_wait(self):
        return self.queue_wait_total if self.arrived_at is not None else None


@dataclass
class Vehicle(RoadParticipant):
    velocity: float = 15.0


@dataclass
class Pedestrian(RoadParticipant):
    velocity: float = 1.4


@dataclass
class SegmentVisit:
    participant_id: str
    segment: str
    entered_at: float
    queue_entered_at: float | None = None
    crossing_started_at: float | None = None
    crossing_finished_at: float | None = None
    exited_at: float | None = None
    capacity_wait: float = 0.0


@dataclass
class Occupancy:
    time: float
    segment: str
    capacity: int | None
    occupied_count: int
    reserved_count: int
    free_count: int | None
    car_queue_len: int
    pedestrian_queue_len: int


@dataclass
class RoadResult(CrossingResult):
    visits: list[SegmentVisit] = field(default_factory=list)
    monitoring: list[Occupancy] = field(default_factory=list)
    parameters: list[dict] = field(default_factory=list)
    closures: dict = field(default_factory=dict)
    final_segments: dict = field(default_factory=dict)

    @property
    def participant_type(self):
        return RoadParticipant

    def extra_tables(self):
        return (("visits.csv", SegmentVisit, self.visits),
                ("monitoring.csv", Occupancy, self.monitoring))

    def summary(self):
        result = super().summary()
        result["car"]["capacity_wait_total"] = sum(v.capacity_wait for v in self.visits)
        result["segments"] = self.final_segments
        result["parameters"] = self.parameters
        result["closures"] = self.closures
        return result


class RoadSegment:
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
        self.signal = (Signal(env, result, config.green_duration, config.red_duration,
                              name=f"traffic_light:{self.name}")
                       if self.intersection_travel_time else None)
        self.crossing_open_at = 0.0
        self.exit_open_at = 0.0
        self.occupied = set()
        self.visits = {}

    def snapshot(self):
        free = int(self.free_slots.level) if self.free_slots is not None else None
        # Выданная container-заявка уже резервирует место, даже если процесс еще не возобновился после yield
        reserved = self.capacity - free - len(self.occupied) if free is not None else 0
        row = Occupancy(float(self.env.now), self.name, self.capacity,
                        len(self.occupied), reserved, free,
                        len(self.stop_line_queue.items), len(self.pedestrian_queue.items))
        self.result.monitoring.append(row)
        return row

    def record(self, participant, action):
        self.result.record(self.env.now, participant.id, participant.kind, f"{action}:{self.name}")

    def enter(self, participant):
        participant.current_segment = self
        participant.velocity = self.config.speed
        self.occupied.add(participant.id)
        visit = SegmentVisit(participant.id, self.name, float(self.env.now))
        self.visits[participant.id] = visit
        self.result.visits.append(visit)
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
                self.env.process(serve_cars(self, segment))
                self.env.process(serve_pedestrians(self, segment))

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
            self.env.process(approach(self, participant, segment, at_stop=at_stop))
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
        for segment in self.segments:
            row = segment.snapshot()
            self.result.final_segments[segment.name] = {
                "capacity": row.capacity, "free_count": row.free_count,
                "occupied_count": row.occupied_count, "reserved_count": row.reserved_count,
                "car_queue_len": row.car_queue_len, "pedestrian_queue_len": row.pedestrian_queue_len,
                "pending_requests": len(segment.free_slots.get_queue) if segment.free_slots else 0,
            }
        return self.result


def approach(network, participant, segment, *, at_stop=False):
    env = network.env
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
        network.finish(participant)
        return
    segment.stop_line_queue.put(participant)
    segment.record(participant, "queued")
    segment.snapshot()
    previous = segment.car_arrived
    segment.car_arrived = env.event()
    previous.succeed()


def reserve_next(network, segment, participant):
    """Ожидание места не занимает переход. При потере разрешения заявка отменяется"""
    env = network.env
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


def serve_cars(network, segment):
    env = network.env
    while True:
        while not segment.stop_line_queue.items:
            yield segment.car_arrived
        participant = segment.stop_line_queue.items[0]
        yield from reserve_next(network, segment, participant)
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
            network.finish(participant)
        else:
            segment.next_segment.enter(participant)
            network.env.process(approach(network, participant, segment.next_segment))


def serve_pedestrians(network, segment):
    env = network.env
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
                network.finish(participant)


def source(network, segment, kind, rate, horizon, rng, arrivals, *, at_stop=False):
    if arrivals is not None:
        for moment in arrivals:
            yield network.env.timeout(moment - network.env.now)
            network.spawn(kind, segment, at_stop=at_stop)
    else:
        for waiting, is_arrival in arrival_clock(network.env, rate, horizon, rng):
            yield waiting
            if is_arrival:
                network.spawn(kind, segment, at_stop=at_stop)


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
    sources = [network.env.process(source(network, network.segments[0], "car", car_rate,
                                         horizon, Random(seed), car_arrivals,
                                         at_stop=cars_at_crossing))]
    for index, segment in enumerate(local, start=1):
        sources.append(network.env.process(source(
            network, segment, "pedestrian", pedestrian_rate, horizon, Random(seed + index),
            prepared.get(segment.name),
        )))
    return network.run(sources, observe_until=observe_until)
