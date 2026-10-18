"""Заполненный downstream-участок блокирует выпуск перед переходом."""

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable

import simpy


class Kind(str, Enum):
    CAR = "car"
    PEDESTRIAN = "pedestrian"


class LightPhase(str, Enum):
    VEHICLES = "vehicles"
    PEDESTRIANS = "pedestrians"


@dataclass(init=False)
class Participant:
    env: simpy.Environment
    queue: simpy.Store
    id: str
    kind: Kind
    speed: float
    appeared_at: float
    route_finished_event: simpy.Event
    queue_entered_at: float | None = None
    crossing_started_at: float | None = None
    crossing_finished_at: float | None = None
    finished_at: float | None = None
    downstream_blocked_time: float = 0.0

    def __init__(
        self,
        env: simpy.Environment,
        queue: simpy.Store,
        id: int,
        kind: Kind,
        appeared_at: float,
    ) -> None:
        self.env = env
        self.queue = queue
        self.id = f"{kind.value}-{id:03d}"
        self.kind = kind
        self.appeared_at = appeared_at
        self.queue_entered_at = None
        self.crossing_started_at = None
        self.crossing_finished_at = None
        self.finished_at = None
        self.route_finished_event = env.event()
        self.downstream_blocked_time = 0.0

    def travel(self, distance: float):
        yield self.env.timeout(self.duration(distance))

    def travel_downstream(
        self,
        distance: float,
        exit_blocked_until: float,
        free_downstream: simpy.Container,
        notify_capacity: Callable[[], None],
    ):
        yield from self.travel(distance)
        if self.env.now < exit_blocked_until:
            yield self.env.timeout(exit_blocked_until - self.env.now)
        yield free_downstream.put(1)
        notify_capacity()
        self.route_finished_event.succeed()

    def approach_crossing(self, distance: float):
        yield from self.travel(distance)
        self.queue_entered_at = float(self.env.now)
        print(f"time={self.env.now:.3f}, id={self.id}, kind={self.kind.value}, queued")
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
    def __init__(self, env: simpy.Environment, queue: simpy.Store, id: int, appeared_at: float) -> None:
        super().__init__(env, queue, id, Kind.CAR, appeared_at)
        self.speed = 15

    def serve_crossing(
        self,
        light_phase_controller,
        crossing_length,
        free_downstream,
        capacity_changed,
        downstream_length,
        exit_blocked_until,
        notify_capacity,
    ):
        while True:
            car = yield self.queue.get()
            while True:
                if light_phase_controller.phase is not LightPhase.VEHICLES:
                    yield light_phase_controller.phase_changed_event
                    continue
                if free_downstream.level < 1:
                    wait_started = float(self.env.now)
                    yield simpy.AnyOf(self.env, [capacity_changed(), light_phase_controller.phase_changed_event])
                    car.downstream_blocked_time += float(self.env.now) - wait_started
                    continue
                yield free_downstream.get(1)
                break
            car.crossing_started_at = float(self.env.now)
            print(f"time={self.env.now:.3f}, id={car.id}, kind={car.kind.value}, crossing_started")
            yield self.env.timeout(car.duration(crossing_length))
            car.crossing_finished_at = float(self.env.now)
            print(f"time={self.env.now:.3f}, id={car.id}, kind={car.kind.value}, downstream_entered")
            self.env.process(car.travel_downstream(downstream_length, exit_blocked_until, free_downstream, notify_capacity))


class Pedestrian(Participant):
    def __init__(self, env: simpy.Environment, queue: simpy.Store, id: int, appeared_at: float) -> None:
        super().__init__(env, queue, id, Kind.PEDESTRIAN, appeared_at)
        self.speed = 1.4

    def serve_crossing(self, light_phase_controller, crossing_length):
        while True:
            phase = yield light_phase_controller.phase_changed_event
            if phase is not LightPhase.PEDESTRIANS:
                continue
            ready = [participant for participant in self.queue.items if participant.queue_entered_at is not None and participant.queue_entered_at < self.env.now]
            if not ready:
                continue
            group = []
            for _ in ready:
                group.append((yield self.queue.get()))
            for pedestrian in group:
                pedestrian.crossing_started_at = float(self.env.now)
                print(f"time={self.env.now:.3f}, id={pedestrian.id}, kind={pedestrian.kind.value}, crossing_started")
            yield self.env.timeout(group[0].duration(crossing_length))
            for pedestrian in group:
                pedestrian.crossing_finished_at = float(self.env.now)
                pedestrian.route_finished_event.succeed()


@dataclass
class Journal:
    participants: list[Participant] = field(default_factory=list)
    finished_at: float = 0.0

    def count(self, kind: Kind) -> int:
        return sum(p.kind == kind for p in self.participants)

    def count_finished(self) -> int:
        return sum(p.finished_at is not None for p in self.participants)



def poisson_source(
    env: simpy.Environment,
    kind: Kind,
    rate_per_hour: float,
    horizon_ms: float,
    rng: random.Random,
    spawn: Callable[[Kind], None],
):
    rate_per_second = rate_per_hour / 3600
    while True:
        interval = rng.expovariate(rate_per_second)
        if env.now + interval >= horizon_ms:
            yield env.timeout(horizon_ms - env.now)
            return
        yield env.timeout(interval)
        spawn(kind)


def scheduled_source(
    env: simpy.Environment,
    kind: Kind,
    arrivals: tuple[float, ...],
    spawn: Callable[[Kind], None],
):
    for arrival in sorted(arrivals):
        yield env.timeout(arrival - env.now)
        spawn(kind)


class PhaseController:
    def __init__(self, env: simpy.Environment) -> None:
        self.env = env
        self.phase = LightPhase.VEHICLES
        self.phase_ends_at = float(env.now) + 20
        self.phase_changed_event = env.event()

    def run(self):
        phases = ((LightPhase.VEHICLES, 20), (LightPhase.PEDESTRIANS, 10))
        index = 0
        print(f"time={self.env.now:.3f}, phase change, allowed={self.phase.value}")
        while True:
            yield self.env.timeout(phases[index][1])
            index = (index + 1) % len(phases)
            self.phase = phases[index][0]
            self.phase_ends_at = float(self.env.now) + phases[index][1]
            previous_event = self.phase_changed_event
            self.phase_changed_event = self.env.event()
            print(f"time={self.env.now:.3f}, phase change, allowed={self.phase.value}")
            previous_event.succeed(self.phase)


class DownstreamSpillbackSimulation:
    def __init__(
        self,
        *,
        env: simpy.Environment,
        seed: int = 42,
        horizon_ms: float = 600,
        intersection_approach_length: float = 150,
        pedestrian_approach_length: float = 28,
        car_crossing_length: float = 30,
        pedestrian_crossing_length: float = 7,
        downstream_length: float = 150,
        car_arrivals: Iterable[float] | None = None,
        pedestrian_arrivals: Iterable[float] | None = None,
        is_car_at_stop_line: bool = False,
        is_pedestrian_at_crossing: bool = False,
        downstream_capacity_count: int = 5,
        exit_blocked_until: float = 0,
    ) -> None:
        lengths = (
            intersection_approach_length,
            pedestrian_approach_length,
            car_crossing_length,
            pedestrian_crossing_length,
            downstream_length,
        )
        if horizon_ms <= 0 or any(length <= 0 for length in lengths):
            raise ValueError("Параметры времени и расстояния должны быть положительными")
        if downstream_capacity_count <= 0 or exit_blocked_until < 0:
            raise ValueError("Параметры вместимости и блокировки заданы неверно")
        self.env = env
        self.journal = Journal()
        self.seed = seed
        self.horizon_ms = horizon_ms
        self.car_intersection_approach_length = intersection_approach_length
        self.pedestrian_approach_length = pedestrian_approach_length
        self.car_crossing_length = car_crossing_length
        self.pedestrian_crossing_length = pedestrian_crossing_length
        self.downstream_length = downstream_length
        self.car_arrivals = None if car_arrivals is None else tuple(car_arrivals)
        self.pedestrian_arrivals = None if pedestrian_arrivals is None else tuple(pedestrian_arrivals)
        for arrivals in (self.car_arrivals, self.pedestrian_arrivals):
            if arrivals is not None and any(time < 0 for time in arrivals):
                raise ValueError("Время появления не может быть отрицательным")
        self.is_car_at_stop_line = is_car_at_stop_line
        self.is_pedestrian_at_crossing = is_pedestrian_at_crossing
        self.downstream_capacity_count = downstream_capacity_count
        self.exit_blocked_until = exit_blocked_until
        self.free_places_at_end_count: float | None = None

    def now(self) -> float:
        return float(self.env.now)

    def source(
        self,
        kind: Kind,
        arrivals: tuple[float, ...] | None,
        rate_per_hour: float,
        stream_seed: int,
        spawn: Callable[[Kind], None],
    ):
        if arrivals is not None:
            return scheduled_source(self.env, kind, arrivals, spawn)
        return poisson_source(self.env, kind, rate_per_hour, self.horizon_ms, random.Random(stream_seed), spawn)

    def run(self) -> Journal:
        car_queue = simpy.Store(self.env)
        pedestrian_queue = simpy.Store(self.env)
        free_downstream = simpy.Container(self.env, capacity=self.downstream_capacity_count, init=self.downstream_capacity_count)
        light_phase_controller = PhaseController(self.env)
        capacity_changed_event = self.env.event()
        participant_processes: list[simpy.Process] = []

        def notify_capacity() -> None:
            nonlocal capacity_changed_event
            previous_event = capacity_changed_event
            capacity_changed_event = self.env.event()
            if not previous_event.triggered:
                previous_event.succeed()

        def spawn(kind: Kind) -> None:
            participant_id = self.journal.count(kind) + 1
            if kind is Kind.CAR:
                queue = car_queue
                distance = 0 if self.is_car_at_stop_line else self.car_intersection_approach_length
            else:
                queue = pedestrian_queue
                distance = 0 if self.is_pedestrian_at_crossing else self.pedestrian_approach_length
            participant_type = Car if kind is Kind.CAR else Pedestrian
            participant = participant_type(self.env, queue, participant_id, self.now())
            self.journal.participants.append(participant)
            if participant_id == 1:
                if kind is Kind.CAR:
                    self.env.process(participant.serve_crossing(
                        light_phase_controller,
                        self.car_crossing_length,
                        free_downstream,
                        lambda: capacity_changed_event,
                        self.downstream_length,
                        self.exit_blocked_until,
                        notify_capacity,
                    ))
                else:
                    self.env.process(participant.serve_crossing(light_phase_controller, self.pedestrian_crossing_length))
            print(f"time={self.now():.3f}, id={participant.id}, kind={participant.kind.value}, appeared")
            participant_processes.append(self.env.process(participant.run(distance)))

        sources = []
        for kind, arrivals, rate, stream_seed in (
            (Kind.CAR, self.car_arrivals, 360, self.seed),
            (Kind.PEDESTRIAN, self.pedestrian_arrivals, 180, self.seed + 1),
        ):
            sources.append(self.env.process(self.source(kind, arrivals, rate, stream_seed, spawn)))

        self.env.process(light_phase_controller.run())
        self.env.run(until=simpy.AllOf(self.env, sources))
        self.env.run(until=simpy.AllOf(self.env, participant_processes))
        self.free_places_at_end_count = free_downstream.level
        self.journal.finished_at = self.now()
        return self.journal


def compare_open_and_blocked(seed: int = 42) -> tuple[Journal, Journal]:
    opened = DownstreamSpillbackSimulation(env=simpy.Environment(), seed=seed).run()
    blocked = DownstreamSpillbackSimulation(env=simpy.Environment(), seed=seed, exit_blocked_until=60).run()
    return opened, blocked


def main() -> None:
    result = DownstreamSpillbackSimulation(
        env=simpy.Environment(),
        car_arrivals=[0] * 8,
        pedestrian_arrivals=[1, 1],
        is_car_at_stop_line=True,
        is_pedestrian_at_crossing=True,
        exit_blocked_until=60
    ).run()
    for participant in result.participants:
        print(f"id={participant.id}, kind={participant.kind.value}, "
              f"crossing_started_at={participant.crossing_started_at}, crossing_finished_at={participant.crossing_finished_at}, finished_at={participant.finished_at}, downstream_blocked_time={participant.downstream_blocked_time}")


if __name__ == "__main__":
    main()
