"""Три независимые полосы и согласованные принимающие перекрёстки."""

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable

import simpy


class Kind(str, Enum):
    CAR = "car"
    PEDESTRIAN = "pedestrian"


class Direction(str, Enum):
    STRAIGHT = "straight"
    LEFT = "left"
    RIGHT = "right"


class LightPhase(str, Enum):
    STRAIGHT = "straight"
    LEFT = "left"
    RIGHT = "right"
    PEDESTRIANS = "pedestrians"
    GREEN = "green"
    RED = "red"


DIRECTIONS = (Direction.STRAIGHT, Direction.LEFT, Direction.RIGHT)
CAPACITIES = {
    Direction.STRAIGHT: 5,
    Direction.LEFT: 3,
    Direction.RIGHT: 2,
}
CENTRAL_PHASES = {
    Direction.STRAIGHT: LightPhase.STRAIGHT,
    Direction.LEFT: LightPhase.LEFT,
    Direction.RIGHT: LightPhase.RIGHT,
}


@dataclass(frozen=True)
class CarArrival:
    time: float
    direction: Direction

    def __post_init__(self) -> None:
        if not isinstance(self.direction, Direction):
            object.__setattr__(self, "direction", Direction(self.direction))


@dataclass(init=False)
class Participant:
    env: simpy.Environment
    queue: simpy.Store
    id: str
    kind: Kind
    speed: float
    appeared_at: float
    route_finished_event: simpy.Event
    direction: Direction | None = None
    central_queue_entered_at: float | None = None
    central_started_at: float | None = None
    central_finished_at: float | None = None
    receiver_arrived_at: float | None = None
    receiver_started_at: float | None = None
    finished_at: float | None = None
    capacity_blocked_time: float = 0.0
    is_initial: bool = False

    def __init__(
        self,
        env: simpy.Environment,
        queue: simpy.Store,
        id: int | str,
        kind: Kind,
        appeared_at: float,
        direction: Direction | None = None,
        is_initial: bool = False,
    ) -> None:
        self.env = env
        self.queue = queue
        self.id = f"{kind.value}-{id:04d}" if isinstance(id, int) else id
        self.kind = kind
        self.appeared_at = appeared_at
        self.direction = direction
        self.central_queue_entered_at = None
        self.central_started_at = None
        self.central_finished_at = None
        self.receiver_arrived_at = None
        self.receiver_started_at = None
        self.finished_at = None
        self.route_finished_event = env.event()
        self.capacity_blocked_time = 0.0
        self.is_initial = is_initial

    def travel(self, distance: float):
        yield self.env.timeout(self.duration(distance))

    def approach_crossing(self, distance: float):
        yield from self.travel(distance)
        self.central_queue_entered_at = float(self.env.now)
        print(f"time={self.env.now:.3f}, id={self.id}, kind={self.kind.value}, central_queued")
        yield self.queue.put(self)

    def duration(self, distance: float) -> float:
        return distance / self.speed

    def run(self, distance: float | None):
        if distance is not None:
            yield from self.approach_crossing(distance)
        yield self.route_finished_event
        self.finished_at = float(self.env.now)
        print(f"time={self.env.now:.3f}, id={self.id}, kind={self.kind.value}, finished")


class Car(Participant):
    def __init__(self, env: simpy.Environment, queue: simpy.Store, id: int | str, appeared_at: float, direction: Direction, is_initial: bool = False) -> None:
        super().__init__(env, queue, id, Kind.CAR, appeared_at, direction, is_initial)
        self.speed = 15

    def serve_crossing(self, light_phase_controller, crossing_length, free_places, capacity_changed_events, travel_to_receiver):
        direction = self.direction
        while True:
            participant = yield self.queue.get()
            while True:
                if light_phase_controller.phase is not CENTRAL_PHASES[direction]:
                    yield light_phase_controller.phase_changed_event
                    continue
                if free_places[direction].level < 1:
                    wait_started = float(self.env.now)
                    yield simpy.AnyOf(self.env, [light_phase_controller.phase_changed_event, capacity_changed_events[direction]])
                    participant.capacity_blocked_time += float(self.env.now) - wait_started
                    continue
                yield free_places[direction].get(1)
                break
            participant.central_started_at = float(self.env.now)
            print(f"time={self.env.now:.3f}, id={participant.id}, kind={participant.kind.value}, direction={direction.value}, central_started")
            yield self.env.timeout(participant.duration(crossing_length))
            participant.central_finished_at = float(self.env.now)
            print(f"time={self.env.now:.3f}, id={participant.id}, kind={participant.kind.value}, direction={direction.value}, central_finished")
            self.env.process(travel_to_receiver(participant))

    def serve_receiver(self, queue, light_phase_controller, crossing_length, release_blocked_until, free_places, notify_capacity):
        direction = self.direction
        while True:
            participant = yield queue.get()
            while True:
                if direction is Direction.LEFT and self.env.now < release_blocked_until:
                    yield self.env.timeout(release_blocked_until - self.env.now)
                    continue
                controller = light_phase_controller
                if controller.phase is not LightPhase.GREEN:
                    yield controller.phase_changed_event
                    continue
                break
            participant.receiver_started_at = float(self.env.now)
            print(f"time={self.env.now:.3f}, id={participant.id}, kind={participant.kind.value}, direction={direction.value}, receiver_started")
            yield self.env.timeout(participant.duration(crossing_length))
            yield free_places[direction].put(1)
            notify_capacity(direction)
            participant.route_finished_event.succeed()


class Pedestrian(Participant):
    def __init__(self, env: simpy.Environment, queue: simpy.Store, id: int, appeared_at: float) -> None:
        super().__init__(env, queue, id, Kind.PEDESTRIAN, appeared_at)
        self.speed = 1.4

    def serve_crossing(self, light_phase_controller, crossing_length):
        while True:
            phase = yield light_phase_controller.phase_changed_event
            if phase is not LightPhase.PEDESTRIANS:
                continue
            ready = [participant for participant in self.queue.items if participant.central_queue_entered_at is not None and participant.central_queue_entered_at < self.env.now]
            if not ready:
                continue
            group = []
            for _ in ready:
                group.append((yield self.queue.get()))
            for pedestrian in group:
                pedestrian.central_started_at = float(self.env.now)
                print(f"time={self.env.now:.3f}, id={pedestrian.id}, kind={pedestrian.kind.value}, central_started")
            yield self.env.timeout(group[0].duration(crossing_length))
            for pedestrian in group:
                pedestrian.central_finished_at = float(self.env.now)
                pedestrian.route_finished_event.succeed()


@dataclass
class Journal:
    participants: list[Participant] = field(default_factory=list)
    finished_at: float = 0.0

    def count(self, kind: Kind) -> int:
        return sum(p.kind == kind for p in self.participants)

    def count_finished(self) -> int:
        return sum(p.finished_at is not None for p in self.participants)

    def completed_by_direction(self) -> dict[Direction, int]:
        return {
            direction: sum(
                participant.kind is Kind.CAR
                and participant.direction is direction
                and participant.finished_at is not None
                for participant in self.participants
            )
            for direction in DIRECTIONS
        }

def poisson_source(
    env: simpy.Environment,
    rate_per_hour: float,
    horizon_ms: float,
    rng: random.Random,
    spawn: Callable[[], None],
):
    rate_per_second = rate_per_hour / 3600
    while True:
        interval = rng.expovariate(rate_per_second)
        if env.now + interval >= horizon_ms:
            yield env.timeout(horizon_ms - env.now)
            return
        yield env.timeout(interval)
        spawn()


def scheduled_car_source(
    env: simpy.Environment,
    arrivals: tuple[CarArrival, ...],
    spawn: Callable[[Direction], None],
):
    for arrival in sorted(arrivals, key=lambda item: item.time):
        yield env.timeout(arrival.time - env.now)
        spawn(arrival.direction)


def scheduled_source(
    env: simpy.Environment,
    arrivals: tuple[float, ...],
    spawn: Callable[[], None],
):
    for arrival in sorted(arrivals):
        yield env.timeout(arrival - env.now)
        spawn()


class PhaseController:
    def __init__(
        self,
        env: simpy.Environment,
        name: str,
        phases: tuple[tuple[LightPhase, float], ...],
    ) -> None:
        self.env = env
        self.name = name
        self.phases = phases
        self.phase = phases[0][0]
        self.phase_ends_at = float(env.now) + phases[0][1]
        self.phase_changed_event = env.event()

    def run(self):
        index = 0
        print(f"time={self.env.now:.3f}, signal={self.name}, phase={self.phase.value}")
        while True:
            yield self.env.timeout(self.phases[index][1])
            index = (index + 1) % len(self.phases)
            self.phase = self.phases[index][0]
            self.phase_ends_at = float(self.env.now) + self.phases[index][1]
            previous_event = self.phase_changed_event
            self.phase_changed_event = self.env.event()
            print(f"time={self.env.now:.3f}, signal={self.name}, phase={self.phase.value}")
            previous_event.succeed(self.phase)


class CoordinatedCorridorSimulation:
    def __init__(
        self,
        *,
        env: simpy.Environment,
        seed: int = 42,
        horizon_ms: float = 600,
        intersection_approach_length: float = 150,
        central_crossing_length: float = 30,
        pedestrian_crossing_length: float = 7,
        receiver_approach_length: float = 150,
        receiver_crossing_length: float = 30,
        car_arrivals: Iterable[CarArrival] | None = None,
        pedestrian_arrivals: Iterable[float] | None = None,
        is_car_at_stop_line: bool = False,
        is_pedestrian_at_crossing: bool = False,
        prefilled_left_count: int = 0,
        left_release_blocked_until: float = 0,
    ) -> None:
        lengths = (
            intersection_approach_length,
            central_crossing_length,
            pedestrian_crossing_length,
            receiver_approach_length,
            receiver_crossing_length,
        )
        if horizon_ms <= 0 or any(length <= 0 for length in lengths):
            raise ValueError("Параметры времени и расстояния должны быть положительными")
        if left_release_blocked_until < 0:
            raise ValueError("Время блокировки не может быть отрицательным")
        if prefilled_left_count not in range(CAPACITIES[Direction.LEFT] + 1):
            raise ValueError("Предзаполнение левого подхода превышает вместимость")
        self.env = env
        self.journal = Journal()
        self.seed = seed
        self.horizon_ms = horizon_ms
        self.car_intersection_approach_length = intersection_approach_length
        self.central_crossing_length = central_crossing_length
        self.pedestrian_crossing_length = pedestrian_crossing_length
        self.receiver_approach_length = receiver_approach_length
        self.receiver_crossing_length = receiver_crossing_length
        self.car_arrivals = None if car_arrivals is None else tuple(car_arrivals)
        self.pedestrian_arrivals = None if pedestrian_arrivals is None else tuple(pedestrian_arrivals)
        if self.car_arrivals is not None and any(arrival.time < 0 for arrival in self.car_arrivals):
            raise ValueError("Появление автомобиля задано неверно")
        if self.pedestrian_arrivals is not None and any(time < 0 for time in self.pedestrian_arrivals):
            raise ValueError("Время появления не может быть отрицательным")
        self.is_car_at_stop_line = is_car_at_stop_line
        self.is_pedestrian_at_crossing = is_pedestrian_at_crossing
        self.prefilled_left_count = prefilled_left_count
        self.left_release_blocked_until = left_release_blocked_until
        self.free_places_at_end_count: dict[str, float] = {}

    def now(self) -> float:
        return float(self.env.now)

    def run(self) -> Journal:
        central_queues = {direction: simpy.Store(self.env) for direction in DIRECTIONS}
        receiver_queues = {direction: simpy.Store(self.env) for direction in DIRECTIONS}
        pedestrian_queue = simpy.Store(self.env)
        free_places = {
            direction: simpy.Container(self.env, capacity=capacity_count, init=capacity_count)
            for direction, capacity_count in CAPACITIES.items()
        }
        capacity_changed_events = {direction: self.env.event() for direction in DIRECTIONS}
        central_signal = PhaseController(
            self.env,
            "central",
            ((LightPhase.STRAIGHT, 24), (LightPhase.LEFT, 12), (LightPhase.RIGHT, 12), (LightPhase.PEDESTRIANS, 12)),
        )
        receiver_signals = {
            Direction.STRAIGHT: PhaseController(self.env, "receiver_straight", ((LightPhase.RED, 12), (LightPhase.GREEN, 24), (LightPhase.RED, 24))),
            Direction.LEFT: PhaseController(self.env, "receiver_left", ((LightPhase.RED, 36), (LightPhase.GREEN, 12), (LightPhase.RED, 12))),
            Direction.RIGHT: PhaseController(self.env, "receiver_right", ((LightPhase.RED, 48), (LightPhase.GREEN, 12))),
        }
        participant_processes: list[simpy.Process] = []
        route_rng = random.Random(self.seed + 2)

        def notify_capacity(direction: Direction) -> None:
            previous_event = capacity_changed_events[direction]
            capacity_changed_events[direction] = self.env.event()
            if not previous_event.triggered:
                previous_event.succeed()

        def choose_direction() -> Direction:
            value = route_rng.random()
            if value < 0.5:
                return Direction.STRAIGHT
            if value < 0.8:
                return Direction.LEFT
            return Direction.RIGHT

        car_services_started: set[Direction] = set()

        def start_car_services(car: Car) -> None:
            direction = car.direction
            if direction in car_services_started:
                return
            car_services_started.add(direction)
            self.env.process(car.serve_crossing(central_signal, self.central_crossing_length, free_places, capacity_changed_events, travel_to_receiver))
            self.env.process(car.serve_receiver(receiver_queues[direction], receiver_signals[direction], self.receiver_crossing_length, self.left_release_blocked_until, free_places, notify_capacity))

        def spawn_car(direction: Direction | None = None) -> None:
            participant_id = self.journal.count(Kind.CAR) + 1
            selected_direction = direction or choose_direction()
            participant = Car(self.env, central_queues[selected_direction], participant_id, self.now(), selected_direction)
            self.journal.participants.append(participant)
            start_car_services(participant)
            print(f"time={self.now():.3f}, id={participant.id}, kind={participant.kind.value}, direction={selected_direction.value}, appeared")
            distance = 0 if self.is_car_at_stop_line else self.car_intersection_approach_length
            participant_processes.append(self.env.process(participant.run(distance)))

        def spawn_pedestrian() -> None:
            participant_id = self.journal.count(Kind.PEDESTRIAN) + 1
            participant = Pedestrian(self.env, pedestrian_queue, participant_id, self.now())
            if participant_id == 1:
                self.env.process(participant.serve_crossing(central_signal, self.pedestrian_crossing_length))
            self.journal.participants.append(participant)
            print(f"time={self.now():.3f}, id={participant.id}, kind={participant.kind.value}, appeared")
            distance = 0
            participant_processes.append(self.env.process(participant.run(distance)))

        def prefill_left_approach():
            for number in range(1, self.prefilled_left_count + 1):
                yield free_places[Direction.LEFT].get(1)
                participant = Car(self.env, central_queues[Direction.LEFT], f"initial-left-{number:03d}", 0.0, Direction.LEFT, True)
                start_car_services(participant)
                participant_processes.append(self.env.process(participant.run(None)))
                participant.receiver_arrived_at = 0.0
                self.journal.participants.append(participant)
                yield receiver_queues[Direction.LEFT].put(participant)
                print(f"time={self.now():.3f}, id={participant.id}, kind={participant.kind.value}, direction={participant.direction.value}, receiver_prefilled")

        def travel_to_receiver(participant: Participant):
            direction = participant.direction
            yield from participant.travel(self.receiver_approach_length)
            participant.receiver_arrived_at = self.now()
            print(f"time={self.now():.3f}, id={participant.id}, kind={participant.kind.value}, direction={direction.value}, receiver_queued")
            yield receiver_queues[direction].put(participant)

        sources = [self.env.process(prefill_left_approach())]
        if self.car_arrivals is None:
            sources.append(self.env.process(poisson_source(self.env, 360, self.horizon_ms, random.Random(self.seed), lambda: spawn_car())))
        else:
            sources.append(self.env.process(scheduled_car_source(self.env, self.car_arrivals, spawn_car)))
        if self.pedestrian_arrivals is None:
            sources.append(self.env.process(poisson_source(self.env, 180, self.horizon_ms, random.Random(self.seed + 1), spawn_pedestrian)))
        else:
            sources.append(self.env.process(scheduled_source(self.env, self.pedestrian_arrivals, spawn_pedestrian)))

        self.env.process(central_signal.run())
        for controller in receiver_signals.values():
            self.env.process(controller.run())
        self.env.run(until=simpy.AllOf(self.env, sources))
        self.env.run(until=simpy.AllOf(self.env, participant_processes))
        self.free_places_at_end_count = {direction.value: container.level for direction, container in free_places.items()}
        self.journal.finished_at = self.now()
        return self.journal


def main() -> None:
    result = CoordinatedCorridorSimulation(env=simpy.Environment(), car_arrivals=[CarArrival(0, Direction.STRAIGHT), CarArrival(0, Direction.LEFT), CarArrival(0, Direction.RIGHT)], pedestrian_arrivals=[], is_car_at_stop_line=True).run()
    for participant in result.participants:
        direction = participant.direction.value if participant.direction is not None else None
        print(f"id={participant.id}, kind={participant.kind.value}, direction={direction}, central_started_at={participant.central_started_at}, receiver_arrived_at={participant.receiver_arrived_at}, receiver_started_at={participant.receiver_started_at}, finished_at={participant.finished_at}")


if __name__ == "__main__":
    main()
