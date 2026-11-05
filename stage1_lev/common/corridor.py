"""Три полосы центрального перекрёстка и согласованные принимающие подходы"""

from dataclasses import dataclass, field
from enum import Enum
from random import Random

import simpy

from .data import validate_number
from .flows import arrival_clock, prepare_arrivals
from .roads import Occupancy, RoadParticipant, RoadResult, SegmentVisit
from .signals import PhaseController


class Direction(str, Enum):
    STRAIGHT = "straight"
    LEFT = "left"
    RIGHT = "right"


DIRECTIONS = tuple(Direction)
CAPACITIES = {Direction.STRAIGHT: 5, Direction.LEFT: 3, Direction.RIGHT: 2}
RECEIVER_NAMES = {Direction.STRAIGHT: "B_T", Direction.LEFT: "B_L", Direction.RIGHT: "B_R"}
CENTRAL_PHASES = (("straight", 24), ("left", 12), ("right", 12), ("pedestrian", 12))
RECEIVER_PHASES = {
    Direction.STRAIGHT: (("red", 12), ("green", 24), ("red", 24)),
    Direction.LEFT: (("red", 36), ("green", 12), ("red", 12)),
    Direction.RIGHT: (("red", 48), ("green", 12)),
}


@dataclass(frozen=True)
class CarArrival:
    time: float
    direction: Direction

    def __post_init__(self):
        validate_number("Время появления", self.time, allow_zero=True)
        object.__setattr__(self, "direction", Direction(self.direction))


@dataclass
class CorridorParticipant(RoadParticipant):
    direction: Direction | None = None
    is_initial: bool = False
    reserved_segment: object | None = field(default=None, repr=False, compare=False,
                                             metadata={"export": False})


@dataclass
class CorridorVisit(SegmentVisit):
    entered_at: float | None
    direction: str = ""
    reserved_at: float | None = None


@dataclass
class CorridorOccupancy(Occupancy):
    straight_queue_len: int = 0
    left_queue_len: int = 0
    right_queue_len: int = 0


@dataclass
class CorridorResult(RoadResult):
    @property
    def participant_type(self):
        return CorridorParticipant

    def extra_tables(self):
        return (("visits.csv", CorridorVisit, self.visits),
                ("monitoring.csv", CorridorOccupancy, self.monitoring))

    def completed_by_direction(self):
        return {
            direction.value: sum(p.kind == "car" and p.direction == direction
                                 and p.arrived_at is not None for p in self.participants)
            for direction in DIRECTIONS
        }

    def summary(self):
        result = super().summary()
        result["completed_by_direction"] = self.completed_by_direction()
        result["directions"] = {}
        for direction in DIRECTIONS:
            members = [p for p in self.participants if p.kind == "car" and p.direction == direction]
            result["directions"][direction.value] = {
                "created": len(members),
                "initial": sum(p.is_initial for p in members),
                "generated": sum(not p.is_initial for p in members),
                "arrived": result["completed_by_direction"][direction.value],
            }
        return result


class RoadSegment:
    """Подъезд, очередь и места, удерживаемые до конца собственного перекрестка"""

    def __init__(self, env, result, name, capacity=None):
        self.env = env
        self.result = result
        self.name = name
        self.approach_length = 150
        self.intersection_length = 30
        self.speed = 15
        self.capacity = capacity
        self.free_slots = (simpy.Container(env, capacity=capacity, init=capacity)
                           if capacity is not None else None)
        lanes = DIRECTIONS if name == "A" else ("car",)
        self.queues = {lane: simpy.Store(env) for lane in lanes}
        self.arrived = {lane: env.event() for lane in lanes}
        self.pedestrians = simpy.Store(env)
        self.occupied = set()
        self.visits = {}

    @property
    def travel_time(self):
        return self.approach_length / self.speed

    @property
    def crossing_time(self):
        return self.intersection_length / self.speed

    def record(self, participant, action):
        self.result.record(self.env.now, participant.id, participant.kind, f"{action}:{self.name}")

    def new_visit(self, participant, entered_at, reserved_at=None):
        visit = CorridorVisit(participant.id, self.name, entered_at,
                              direction=participant.direction.value, reserved_at=reserved_at)
        self.visits[participant.id] = visit
        self.result.visits.append(visit)
        return visit

    def enter(self, participant):
        participant.current_segment = self
        participant.reserved_segment = None
        self.occupied.add(participant.id)
        if participant.id not in self.visits:
            self.new_visit(participant, float(self.env.now))
        else:
            self.visits[participant.id].entered_at = float(self.env.now)
        self.record(participant, "entered")
        self.snapshot()

    def enqueue(self, participant, lane):
        self.visits[participant.id].queue_entered_at = float(self.env.now)
        if self.name == "A":
            participant.queue_entered_at = float(self.env.now)
        self.queues[lane].put(participant)
        self.record(participant, "queued")
        self.snapshot()
        previous = self.arrived[lane]
        self.arrived[lane] = self.env.event()
        previous.succeed()

    def leave(self, participant):
        self.occupied.remove(participant.id)
        self.visits[participant.id].exited_at = float(self.env.now)
        if self.free_slots is not None:
            self.free_slots.put(1)
        self.record(participant, "left")
        self.snapshot()

    def snapshot(self):
        free = int(self.free_slots.level) if self.free_slots is not None else None
        reserved = self.capacity - free - len(self.occupied) if free is not None else 0
        lengths = [len(self.queues[d].items) if d in self.queues else 0 for d in DIRECTIONS]
        row = CorridorOccupancy(
            float(self.env.now), self.name, self.capacity, len(self.occupied), reserved, free,
            sum(len(q.items) for q in self.queues.values()), len(self.pedestrians.items), *lengths,
        )
        self.result.monitoring.append(row)
        return row


@dataclass
class Intersection:
    incoming: RoadSegment
    controller: PhaseController
    outgoing: dict = field(default_factory=dict)
    release_blocked_until: float = 0.0


class CoordinatedCorridor:
    def __init__(self, seed, *, prefilled_left_count=0, left_release_blocked_until=0):
        self.env = simpy.Environment()
        self.result = CorridorResult(exercise=7, seed=seed)
        self.route_rng = Random(seed + 2)
        self.active_count = 0
        self.has_sources_finished = False
        self.done = self.env.event()
        self.counts = {"car": 0, "pedestrian": 0}
        self.central = Intersection(
            RoadSegment(self.env, self.result, "A"),
            PhaseController(self.env, self.result, "traffic_light:A", CENTRAL_PHASES),
        )
        for direction in DIRECTIONS:
            name = RECEIVER_NAMES[direction]
            receiver = Intersection(
                RoadSegment(self.env, self.result, name, CAPACITIES[direction]),
                PhaseController(self.env, self.result, f"traffic_light:{name}", RECEIVER_PHASES[direction]),
                release_blocked_until=left_release_blocked_until if direction == Direction.LEFT else 0,
            )
            self.central.outgoing[direction] = receiver
            self.env.process(self.serve_central(direction))
            self.env.process(self.serve_receiver(receiver))
        self.intersections = [self.central, *self.central.outgoing.values()]
        for intersection in self.intersections:
            road = intersection.incoming
            road.snapshot()
            self.env.process(intersection.controller.run())
            self.result.parameters.append({
                "name": road.name, "approach_length": road.approach_length,
                "intersection_length": road.intersection_length, "speed": road.speed,
                "capacity": road.capacity,
                "phases": [[phase, duration] for phase, duration in intersection.controller.phases],
            })
        self.result.closures = {"B_L": {"release_blocked_until": left_release_blocked_until}}
        self.env.process(self.serve_pedestrians())
        self.prefill_left(prefilled_left_count)

    def register(self, participant):
        self.result.participants.append(participant)
        self.result.record(self.env.now, participant.id, participant.kind, "appeared")
        self.active_count += 1

    def prefill_left(self, count):
        road = self.central.outgoing[Direction.LEFT].incoming
        for number in range(1, count + 1):
            road.free_slots.get(1)
            car = CorridorParticipant(f"initial-left-{number}", "car", 0.0,
                                      velocity=15, direction=Direction.LEFT, is_initial=True)
            self.register(car)
            road.enter(car)
            road.enqueue(car, "car")

    def choose_direction(self):
        value = self.route_rng.random()
        if value < 0.5:
            return Direction.STRAIGHT
        return Direction.LEFT if value < 0.8 else Direction.RIGHT

    def spawn_car(self, direction=None, *, at_stop=False):
        self.counts["car"] += 1
        car = CorridorParticipant(f"car-{self.counts['car']}", "car", float(self.env.now),
                                  velocity=15, direction=direction or self.choose_direction())
        self.register(car)
        self.central.incoming.enter(car)
        self.env.process(self.approach(car, self.central.incoming, car.direction, at_stop=at_stop))

    def spawn_pedestrian(self):
        self.counts["pedestrian"] += 1
        pedestrian = CorridorParticipant(f"pedestrian-{self.counts['pedestrian']}", "pedestrian",
                                         float(self.env.now), velocity=1.4,
                                         queue_entered_at=float(self.env.now))
        self.register(pedestrian)
        self.central.incoming.pedestrians.put(pedestrian)
        self.central.incoming.record(pedestrian, "queued")
        self.central.incoming.snapshot()

    def approach(self, car, road, lane, *, at_stop=False):
        yield self.env.timeout(0 if at_stop else road.travel_time)
        road.enqueue(car, lane)

    def reserve_receiver(self, car, direction):
        controller = self.central.controller
        source = self.central.incoming
        target = self.central.outgoing[direction].incoming
        while True:
            if not controller.allows(direction.value, source.crossing_time):
                yield controller.changed
                continue
            request = target.free_slots.get(1)
            target.snapshot()
            wait_started = self.env.now
            if not request.triggered:
                source.record(car, "capacity_wait")
            yield request | controller.changed
            source.visits[car.id].capacity_wait += self.env.now - wait_started
            if request.triggered and controller.allows(direction.value, source.crossing_time):
                car.reserved_segment = target
                target.new_visit(car, None, reserved_at=float(self.env.now))
                target.record(car, "reserved")
                target.snapshot()
                return
            if request.triggered:
                target.free_slots.put(1)
            else:
                request.cancel()
            target.snapshot()

    def begin_crossing(self, car, road):
        visit = road.visits[car.id]
        visit.crossing_started_at = float(self.env.now)
        car.queue_wait_total += self.env.now - visit.queue_entered_at
        if road.name == "A":
            car.queue_left_at = float(self.env.now)
        road.record(car, "crossing_started")
        road.snapshot()

    def finish_crossing(self, car, road):
        road.visits[car.id].crossing_finished_at = float(self.env.now)
        road.record(car, "crossing_finished")
        road.leave(car)

    def serve_central(self, direction):
        road = self.central.incoming
        target = self.central.outgoing[direction].incoming
        queue = road.queues[direction]
        while True:
            while not queue.items:
                yield road.arrived[direction]
            car = queue.items[0]
            yield from self.reserve_receiver(car, direction)
            yield queue.get()
            self.begin_crossing(car, road)
            yield self.env.timeout(road.crossing_time)
            self.finish_crossing(car, road)
            target.enter(car)
            self.env.process(self.approach(car, target, "car"))

    def serve_receiver(self, intersection):
        road = intersection.incoming
        queue = road.queues["car"]
        while True:
            while not queue.items:
                yield road.arrived["car"]
            while True:
                if self.env.now < intersection.release_blocked_until:
                    yield self.env.timeout(intersection.release_blocked_until - self.env.now)
                elif not intersection.controller.allows("green", road.crossing_time):
                    yield intersection.controller.changed
                else:
                    break
            car = yield queue.get()
            self.begin_crossing(car, road)
            yield self.env.timeout(road.crossing_time)
            self.finish_crossing(car, road)
            self.finish(car)

    def serve_pedestrians(self):
        road = self.central.incoming
        controller = self.central.controller
        while True:
            yield controller.changed
            if controller.phase != "pedestrian":
                continue
            phase_start = self.env.now
            group = []
            while road.pedestrians.items and road.pedestrians.items[0].queue_entered_at < phase_start:
                pedestrian = yield road.pedestrians.get()
                pedestrian.queue_left_at = float(self.env.now)
                pedestrian.queue_wait_total = self.env.now - pedestrian.queue_entered_at
                road.record(pedestrian, "crossing_started")
                group.append(pedestrian)
            road.snapshot()
            if group:
                yield self.env.timeout(5)
                for pedestrian in group:
                    road.record(pedestrian, "crossing_finished")
                    self.finish(pedestrian)

    def finish(self, participant):
        participant.current_segment = None
        participant.arrived_at = float(self.env.now)
        self.result.record(self.env.now, participant.id, participant.kind, "arrived")
        self.active_count -= 1
        self.finish_if_empty()

    def finish_if_empty(self):
        if self.has_sources_finished and self.active_count == 0 and not self.done.triggered:
            self.done.succeed()

    def run(self, sources):
        self.env.run(until=self.env.all_of(sources))
        self.has_sources_finished = True
        self.finish_if_empty()
        self.env.run(until=self.done)
        self.result.finished_at = float(self.env.now)
        for intersection in self.intersections:
            road = intersection.incoming
            row = road.snapshot()
            self.result.final_segments[road.name] = {
                "capacity": row.capacity, "free_count": row.free_count,
                "occupied_count": row.occupied_count, "reserved_count": row.reserved_count,
                "car_queue_len": row.car_queue_len, "pedestrian_queue_len": row.pedestrian_queue_len,
                "pending_requests": len(road.free_slots.get_queue) if road.free_slots else 0,
            }
        return self.result


def source(network, kind, rate, horizon, rng, arrivals, *, at_stop=False):
    if arrivals is None:
        for waiting, is_arrival in arrival_clock(network.env, rate, horizon, rng):
            yield waiting
            if is_arrival:
                if kind == "car":
                    network.spawn_car(at_stop=at_stop)
                else:
                    network.spawn_pedestrian()
    else:
        for arrival in arrivals:
            moment = arrival.time if kind == "car" else arrival
            if moment >= horizon:
                return
            yield network.env.timeout(moment - network.env.now)
            if kind == "car":
                network.spawn_car(arrival.direction, at_stop=at_stop)
            else:
                network.spawn_pedestrian()


def simulate(seed=42, *, horizon=600, car_rate=360, pedestrian_rate=180,
             car_arrivals=None, pedestrian_arrivals=None, cars_at_crossing=False,
             prefilled_left_count=0, left_release_blocked_until=0):
    validate_number("Горизонт генерации", horizon)
    validate_number("Интенсивность автомобилей", car_rate, allow_zero=True)
    validate_number("Интенсивность пешеходов", pedestrian_rate, allow_zero=True)
    validate_number("Время открытия B_L", left_release_blocked_until, allow_zero=True)
    if type(prefilled_left_count) is not int or not 0 <= prefilled_left_count <= 3:
        raise ValueError("Предзаполнение B_L должно быть целым числом от 0 до 3")
    if car_arrivals is not None:
        car_arrivals = list(car_arrivals)
        if any(not isinstance(arrival, CarArrival) for arrival in car_arrivals):
            raise ValueError("Автомобильное расписание должно содержать CarArrival(time, direction)")
        car_arrivals.sort(key=lambda arrival: arrival.time)
    pedestrian_arrivals = prepare_arrivals(pedestrian_arrivals)
    network = CoordinatedCorridor(seed, prefilled_left_count=prefilled_left_count,
                                 left_release_blocked_until=left_release_blocked_until)
    sources = [
        network.env.process(source(network, "car", car_rate, horizon, Random(seed),
                                   car_arrivals, at_stop=cars_at_crossing)),
        network.env.process(source(network, "pedestrian", pedestrian_rate, horizon,
                                   Random(seed + 1), pedestrian_arrivals)),
    ]
    return network.run(sources)
