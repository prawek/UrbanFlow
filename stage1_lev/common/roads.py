"""Конфигурация перекрёстков и участки общей дорожной модели."""

from dataclasses import dataclass, field

import simpy

from .data import Direction, validate_number


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


@dataclass(frozen=True)
class IntersectionConfig:
    incoming: SegmentConfig
    phases_duration: tuple[tuple[str, float], ...]
    car_phases: dict[Direction, str] = field(
        default_factory=lambda: {direction: direction.value for direction in Direction},
    )
    left_probability: float = 0.3
    right_probability: float = 0.2
    pedestrian_crossing_time: float = 5
    outgoing: dict[Direction, "IntersectionConfig"] = field(default_factory=dict)

    @property
    def directions(self):
        return tuple(self.car_phases)

    @property
    def probabilities(self):
        return {
            Direction.STRAIGHT: 1 - (self.left_probability + self.right_probability),
            Direction.LEFT: self.left_probability,
            Direction.RIGHT: self.right_probability,
        }

    def validate(self):
        self.incoming.validate()
        validate_number("Длина перекрёстка", self.incoming.intersection_length)
        validate_number("Вероятность левого поворота", self.left_probability, allow_zero=True)
        validate_number("Вероятность правого поворота", self.right_probability, allow_zero=True)
        if self.left_probability + self.right_probability > 1:
            raise ValueError("Сумма вероятностей поворотов не должна превышать 1")
        validate_number("Время перехода пешеходов", self.pedestrian_crossing_time)
        if not self.phases_duration or not self.car_phases:
            raise ValueError("Перекрёстку нужны фазы и хотя бы одна автомобильная полоса")
        for _, duration in self.phases_duration:
            validate_number("Длительность фазы", duration)
        if any(not isinstance(d, Direction) for d in (*self.directions, *self.outgoing)):
            raise ValueError("Направления должны быть константами Direction")
        if set(self.outgoing) - set(self.directions):
            raise ValueError("Выезд задан для отсутствующей полосы")
        for direction, probability in self.probabilities.items():
            if probability > 0 and direction not in self.car_phases:
                raise ValueError("Нельзя выбирать направление без полосы")
        crossing_time = self.incoming.intersection_length / self.incoming.speed
        for phase in self.car_phases.values():
            if phase == "pedestrian":
                raise ValueError("Автомобили не могут использовать пешеходную фазу")
            if not any(name == phase and duration >= crossing_time
                       for name, duration in self.phases_duration):
                raise ValueError("Каждой полосе нужна фаза, вмещающая проезд перекрёстка")
        if any(name == "pedestrian" and duration < self.pedestrian_crossing_time
               for name, duration in self.phases_duration):
            raise ValueError("Пешеходная фаза должна вмещать переход")


class RoadSegment:
    """Подъезд и полосы; место занято до завершения проезда перекрёстка."""

    def __init__(self, env, result, config, *, lanes):
        config.validate()
        self.env = env
        self.result = result
        self.config = config
        self.name = config.name
        self.capacity = config.capacity
        self.free_slots = (simpy.Container(env, capacity=self.capacity, init=self.capacity)
                           if self.capacity is not None else None)
        # Имя — только идентификатор. Проверка name == "A" привязала бы полосы
        # и статистику к сценарию: переименование или второй перекрёсток меняли бы поведение.
        self.queues = {lane: simpy.Store(env) for lane in lanes}
        if not self.queues:
            raise ValueError("Нужна хотя бы одна полоса")
        self.arrived = {lane: env.event() for lane in self.queues}
        self.pedestrians = simpy.Store(env)
        self.occupied = set()
        self.visits = {}

    @property
    def travel_time(self):
        return self.config.approach_length / self.config.speed

    @property
    def crossing_time(self):
        return self.config.intersection_length / self.config.speed

    def record(self, participant, action):
        self.result.record(self.env.now, participant.id, participant.kind, f"{action}:{self.name}")

    def reserve_visit(self, participant):
        self.visits[participant.id] = self.result.new_visit(
            participant.id, self.name, None, reserved_at=float(self.env.now),
        )

    def enter(self, participant, direction):
        participant.current_segment = self
        participant.reserved_segment = None
        participant.velocity = self.config.speed
        self.occupied.add(participant.id)
        visit = self.visits.get(participant.id)
        if visit is None or visit.exited_at is not None:
            visit = self.result.new_visit(participant.id, self.name, float(self.env.now))
            self.visits[participant.id] = visit
        visit.entered_at = float(self.env.now)
        visit.direction = direction.value
        self.record(participant, "entered")
        self.snapshot()

    def approach(self, car, lane, *, at_stop=False):
        yield self.env.timeout(0 if at_stop else self.travel_time)
        self.enqueue(car, lane)

    def enqueue(self, participant, lane):
        self.visits[participant.id].queue_entered_at = float(self.env.now)
        # Общие времена описывают первую очередь участника, независимо от имени дороги.
        if participant.queue_entered_at is None:
            participant.queue_entered_at = float(self.env.now)
        self.queues[lane].put(participant)
        self.record(participant, "queued")
        self.snapshot()
        previous = self.arrived[lane]
        self.arrived[lane] = self.env.event()
        previous.succeed()

    def begin_crossing(self, car):
        visit = self.visits[car.id]
        visit.crossing_started_at = float(self.env.now)
        car.queue_wait_total += self.env.now - visit.queue_entered_at
        if car.queue_left_at is None:
            car.queue_left_at = float(self.env.now)
        self.record(car, "crossing_started")
        self.snapshot()

    def finish_crossing(self, car):
        self.visits[car.id].crossing_finished_at = float(self.env.now)
        self.record(car, "crossing_finished")
        self.leave(car)

    def leave(self, participant):
        self.occupied.remove(participant.id)
        self.visits[participant.id].exited_at = float(self.env.now)
        if self.free_slots is not None:
            self.free_slots.put(1)
        self.record(participant, "left")
        self.snapshot()

    def snapshot(self):
        free = int(self.free_slots.level) if self.free_slots is not None else None
        lengths = {f"{direction.value}_queue_len": len(self.queues[direction].items)
                   if direction in self.queues else 0 for direction in Direction}
        return self.result.record_occupancy(
            self.env.now, self.name, self.capacity, len(self.occupied), free,
            sum(len(queue.items) for queue in self.queues.values()),
            len(self.pedestrians.items), **lengths,
        )
