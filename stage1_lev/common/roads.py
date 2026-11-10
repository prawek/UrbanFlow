"""Конфигурация перекрёстков и участки общей дорожной модели."""

from dataclasses import dataclass, field

import simpy

from .data import Direction, validate_number
from .signals import PhaseController


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
class CarArrival:
    time: float
    direction: Direction

    def __post_init__(self):
        validate_number("Время появления", self.time, allow_zero=True)
        if not isinstance(self.direction, Direction):
            raise ValueError("Направление должно быть константой Direction")


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
        self.queues = {lane: simpy.Store(env) for lane in lanes}
        if not self.queues:
            raise ValueError("Нужна хотя бы одна полоса")
        self.arrived = {lane: env.event() for lane in self.queues}
        self.pedestrians_store = simpy.Store(env)
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
            len(self.pedestrians_store.items), **lengths,
        )


class Intersection:
    def __init__(self, env, result, config, *, rng, finish, release_blocked_until=0):
        config.validate()
        validate_number("Время открытия перекрёстка", release_blocked_until, allow_zero=True)
        self.env = env
        self.config = config
        self.rng = rng
        self.finish = finish
        self.incoming = RoadSegment(env, result, config.incoming, lanes=config.directions)
        self.controller = PhaseController(
            env, result, f"traffic_light:{self.incoming.name}", config.phases_duration,
        )
        self.outgoing = {}
        self.release_blocked_until = release_blocked_until

    def choose_direction(self, preferred=None):
        """Выбирает местный поворот один раз при входе; ожидание его не меняет."""
        if preferred is not None:
            if not isinstance(preferred, Direction) or preferred not in self.config.directions:
                raise ValueError("Направление отсутствует на перекрёстке")
            return preferred
        choices = [(direction, probability)
                   for direction, probability in self.config.probabilities.items()
                   if probability > 0]
        if len(choices) == 1:
            return choices[0][0]
        value = self.rng.random()
        cumulative = 0.0
        for direction, probability in choices:
            cumulative += probability
            if value < cumulative:
                return direction
        return choices[-1][0]

    def enter(self, car, *, direction=None, at_stop=False):
        car.direction = self.choose_direction(direction)
        if car.entry_direction is None:
            car.entry_direction = car.direction
        self.incoming.enter(car, car.direction)
        self.env.process(self.incoming.approach(car, car.direction, at_stop=at_stop))

    def enqueue_pedestrian(self, pedestrian):
        if not any(phase == "pedestrian" for phase, _ in self.config.phases_duration):
            raise ValueError("У перекрёстка нет пешеходной фазы")
        pedestrian.queue_entered_at = float(self.env.now)
        pedestrian.local_segment = self.incoming.name
        self.incoming.pedestrians_store.put(pedestrian)
        self.incoming.record(pedestrian, "queued")
        self.incoming.snapshot()

    def reserve_receiver(self, car, direction):
        """Ждёт свой зелёный и место; при потере разрешения отменяет резерв."""
        road = self.incoming
        receiver = self.outgoing.get(direction)
        target = receiver.incoming if receiver is not None else None
        phase = self.config.car_phases[direction]
        while True:
            if self.env.now < self.release_blocked_until:
                yield self.env.timeout(self.release_blocked_until - self.env.now)
                continue
            if not self.controller.allows(phase, road.crossing_time):
                yield self.controller.changed
                continue
            if target is None:
                return
            if target.free_slots is None:
                car.reserved_segment = target
                target.reserve_visit(car)
                target.record(car, "reserved")
                return
            request = target.free_slots.get(1)
            target.snapshot()
            wait_started = self.env.now
            if not request.triggered:
                road.record(car, "capacity_wait")
            yield request | self.controller.changed
            road.visits[car.id].capacity_wait += self.env.now - wait_started
            # get и changed могут сработать одновременно: проверяем саму заявку
            # и фактическую фазу, а не только события, попавшие в AnyOf.
            if request.triggered and self.controller.allows(phase, road.crossing_time):
                car.reserved_segment = target
                target.reserve_visit(car)
                target.record(car, "reserved")
                target.snapshot()
                return
            if request.triggered:
                target.free_slots.put(1)
            else:
                request.cancel()
            target.snapshot()

    def serve_cars(self, direction):
        road = self.incoming
        queue = road.queues[direction]
        while True:
            while not queue.items:
                yield road.arrived[direction]
            car = queue.items[0]
            yield from self.reserve_receiver(car, direction)
            yield queue.get()
            road.begin_crossing(car)
            yield self.env.timeout(road.crossing_time)
            road.finish_crossing(car)
            receiver = self.outgoing.get(direction)
            if receiver is None:
                self.finish(car)
            else:
                receiver.enter(car)

    def serve_pedestrians(self):
        road = self.incoming
        while True:
            yield self.controller.changed
            if self.controller.phase != "pedestrian":
                continue
            phase_start = self.env.now
            group = []
            while road.pedestrians_store.items and road.pedestrians_store.items[0].queue_entered_at < phase_start:
                pedestrian = yield road.pedestrians_store.get()
                pedestrian.queue_left_at = float(self.env.now)
                pedestrian.queue_wait_total = self.env.now - pedestrian.queue_entered_at
                road.record(pedestrian, "crossing_started")
                group.append(pedestrian)
            road.snapshot()
            if group:
                yield self.env.timeout(self.config.pedestrian_crossing_time)
                for pedestrian in group:
                    road.record(pedestrian, "crossing_finished")
                    self.finish(pedestrian)

    def start(self):
        self.incoming.snapshot()
        self.env.process(self.controller.run())
        for direction in self.config.directions:
            self.env.process(self.serve_cars(direction))
        if any(phase == "pedestrian" for phase, _ in self.config.phases_duration):
            self.env.process(self.serve_pedestrians())
