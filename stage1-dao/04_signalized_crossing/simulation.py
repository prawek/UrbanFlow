"""Автомобильная и пешеходная FIFO-очереди у регулируемого перехода."""

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Any, Callable, Generator

import simpy
from simpy import Timeout


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
        self.route_finished_event = env.event()

    def duration(self, distance: float):
        return distance / self.speed

    def approach_crossing(self, distance: float):
        if distance > 0:
            yield self.env.timeout(self.duration(distance))
        self.queue_entered_at = float(self.env.now)
        print(f"time={self.env.now:.3f}, id={self.id}, kind={self.kind.value}, queued")
        yield self.queue.put(self)

    def run(self, distance: float):
        yield from self.approach_crossing(distance)
        yield self.route_finished_event
        self.crossing_finished_at = float(self.env.now)
        print(f"time={self.env.now:.3f}, id={self.id}, kind={self.kind.value}, finished")


class Car(Participant):
    def __init__(self, env: simpy.Environment, queue: simpy.Store, id: int, appeared_at: float) -> None:
        super().__init__(env, queue, id, Kind.CAR, appeared_at)
        self.speed = 15

    def serve_crossing(self, light_phase_controller, crossing_length):
        while True:
            car = yield self.queue.get()
            crossing_duration = car.duration(crossing_length)
            while (
                light_phase_controller.phase is not LightPhase.VEHICLES
                or self.env.now + crossing_duration > light_phase_controller.phase_ends_at
            ):
                yield light_phase_controller.phase_changed_event

            car.crossing_started_at = float(self.env.now)
            print(f"time={self.env.now:.3f}, id={car.id}, kind={car.kind.value}, crossing_started")

            yield self.env.timeout(crossing_duration)
            car.route_finished_event.succeed()


class Pedestrian(Participant):
    def __init__(self, env: simpy.Environment, queue: simpy.Store, id: int, appeared_at: float) -> None:
        super().__init__(env, queue, id, Kind.PEDESTRIAN, appeared_at)
        self.speed = 1.4

    def serve_crossing(self, light_phase_controller, crossing_length):
        while True:
            light_phase = yield light_phase_controller.phase_changed_event
            if light_phase is not LightPhase.PEDESTRIANS:
                continue
            ready = [p for p in self.queue.items if p.queue_entered_at is not None and p.queue_entered_at < self.env.now]
            if not ready:
                continue
            group = []
            for _ in ready:
                group.append((yield self.queue.get()))
            for pedestrian in group:
                pedestrian.crossing_started_at = float(self.env.now)
                print(f"time={self.env.now:.3f}, id={pedestrian.id}, kind={pedestrian.kind.value}, event=crossing_started")
            yield self.env.timeout(group[0].duration(crossing_length))
            for pedestrian in group:
                pedestrian.route_finished_event.succeed()


@dataclass
class Journal:
    participants: list[Participant] = field(default_factory=list)
    finished_at: float = 0.0

    def count(self, kind: Kind) -> int:
        return sum(p.kind == kind for p in self.participants)

    def count_finished(self) -> int:
        return sum(p.crossing_finished_at is not None for p in self.participants)



def poisson_source(
    env: simpy.Environment,
    kind: Kind,
    rate_per_hour: float,
    horizon_ms: float,
    rng: random.Random,
    spawn: Callable[..., None],
):
    rate_per_second = rate_per_hour / 3600
    while True:
        interval = rng.expovariate(rate_per_second)
        if env.now + interval >= horizon_ms:
            yield env.timeout(horizon_ms - env.now)
            return
        yield env.timeout(interval)
        spawn(kind)


def scheduled_source(env: simpy.Environment, kind: Kind, arrivals: tuple[float, ...], spawn: Callable[..., None]):
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


class SignalizedCrossingSimulation:
    def __init__(
        self,
        *,
        env,
        seed: int = 42,
        horizon_ms: float = 600,
        intersection_approach_length: float = 150,
        car_crossing_length: float = 30,
        pedestrian_crossing_length: float = 7,
        car_arrivals: Iterable[float] | None = None,
        pedestrian_arrivals: Iterable[float] | None = None,
    ) -> None:
        self.env = env
        self.journal = Journal()

        self.seed = seed
        if horizon_ms <= 0:
            raise ValueError("Горизонт генерации должен быть положительным")
        if intersection_approach_length <= 0:
            raise ValueError("Длина подъезда к перекрёстку должна быть положительной")
        if car_crossing_length <= 0:
            raise ValueError("Длина проезда автомобиля должна быть положительной")
        if pedestrian_crossing_length <= 0:
            raise ValueError("Длина пешеходного перехода должна быть положительной")
        self.horizon_ms = horizon_ms
        self.car_intersection_approach_length = intersection_approach_length
        self.car_crossing_length = car_crossing_length
        self.pedestrian_crossing_length = pedestrian_crossing_length
        self.car_arrivals = None if car_arrivals is None else tuple(car_arrivals)
        self.pedestrian_arrivals = None if pedestrian_arrivals is None else tuple(pedestrian_arrivals)
        for arrivals in (self.car_arrivals, self.pedestrian_arrivals):
            if arrivals is not None and any(time < 0 for time in arrivals):
                raise ValueError("Время появления не может быть отрицательным")

    def now(self) -> float:
        return float(self.env.now)

    def run(self) -> Journal:
        car_queue = simpy.Store(self.env)
        pedestrian_queue = simpy.Store(self.env)
        light_phase_controller = PhaseController(self.env)
        participant_processes: list[simpy.Process] = []

        def spawn(kind: Kind) -> None:
            participant_id = self.journal.count(kind) + 1

            if kind == Kind.CAR:
                participant = Car(self.env, car_queue, participant_id, self.now())
                distance = self.car_intersection_approach_length
                crossing_length = self.car_crossing_length
            else:
                participant = Pedestrian(self.env, pedestrian_queue, participant_id, self.now())
                distance = 0
                crossing_length = self.pedestrian_crossing_length

            self.journal.participants.append(participant)
            print(f"time={self.now():.3f}, id={participant.id}, kind={participant.kind.value}, appeared")

            if participant_id == 1:
                self.env.process(participant.serve_crossing(light_phase_controller, crossing_length))

            participant_processes.append(self.env.process(participant.run(distance)))

        sources = []
        for kind, arrivals, rate, stream_seed in (
            (Kind.CAR, self.car_arrivals, 360, self.seed),
            (Kind.PEDESTRIAN, self.pedestrian_arrivals, 180, self.seed + 1),
        ):
            process = self.env.process(self.source(kind, arrivals, rate, stream_seed, spawn))
            sources.append(process)

        self.env.process(light_phase_controller.run())
        self.env.run(until=simpy.AllOf(self.env, sources))
        self.env.run(until=simpy.AllOf(self.env, participant_processes))
        self.journal.finished_at = self.now()
        return self.journal

    def source(self, kind, arrivals, rate, stream_seed, spawn: Callable[..., None]) -> Generator[Timeout, Any, None]:
        if arrivals is not None:
            return scheduled_source(self.env, kind, arrivals, spawn)
        else:
            return poisson_source(self.env, kind, rate, self.horizon_ms, random.Random(stream_seed), spawn)


def main() -> None:
    result = SignalizedCrossingSimulation(
        car_arrivals=[0, 0, 0],
        pedestrian_arrivals=[1, 1],
        env=simpy.Environment()
    ).run()
    for participant in result.participants:
        print(f"id={participant.id}, kind={participant.kind.value}, queue_entered_at={participant.queue_entered_at}, "
              f"crossing_started_at={participant.crossing_started_at}, crossing_finished_at={participant.crossing_finished_at}")


if __name__ == "__main__":
    main()
