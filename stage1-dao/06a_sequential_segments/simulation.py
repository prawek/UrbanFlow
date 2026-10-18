"""Маршрут A → B → C с независимыми светофорами и резервированием мест."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Mapping

import simpy


class Kind(str, Enum):
    CAR = "car"
    PEDESTRIAN = "pedestrian"


class LightPhase(str, Enum):
    VEHICLES = "vehicles"
    PEDESTRIANS = "pedestrians"


PEDESTRIAN_CROSSING_DURATION = 5


@dataclass(frozen=True)
class SegmentParameters:
    approach_length: float
    intersection_length: float
    speed: float
    capacity: int | None
    green_duration: float
    red_duration: float

    def __post_init__(self) -> None:
        values = (
            self.approach_length, self.intersection_length, self.speed,
            self.green_duration, self.red_duration,
        )
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("Расстояния, скорость и длительности фаз должны быть положительными")
        if self.capacity is not None and (
            type(self.capacity) is not int or self.capacity <= 0
        ):
            raise ValueError("Вместимость должна быть положительным целым числом или None")
        if self.green_duration < self.intersection_length / self.speed:
            raise ValueError("Зелёная фаза должна вмещать проезд одного автомобиля")
        if self.red_duration < PEDESTRIAN_CROSSING_DURATION:
            raise ValueError("Красная фаза должна вмещать переход пешеходной группы")


DEFAULT_SEGMENT_PARAMETERS = {
    "A": SegmentParameters(150, 30, 15, None, 20, 10),
    "B": SegmentParameters(80, 30, 10, 5, 27, 13),
    "C": SegmentParameters(120, 40, 10, 3, 17, 19),
}


@dataclass
class SegmentVisit:
    segment_name: str
    reserved_at: float | None = None
    entered_at: float | None = None
    queue_entered_at: float | None = None
    crossing_started_at: float | None = None
    crossing_finished_at: float | None = None
    downstream_blocked_time: float = 0.0


@dataclass(frozen=True)
class PhaseChange:
    time: float
    segment_name: str
    phase: LightPhase


@dataclass
class Journal:
    participants: list[Participant] = field(default_factory=list)
    phase_changes: list[PhaseChange] = field(default_factory=list)
    finished_at: float = 0.0

    def count(self, kind: Kind) -> int:
        return sum(p.kind is kind for p in self.participants)

    def count_finished(self) -> int:
        return sum(p.finished_at is not None for p in self.participants)


class PhaseController:
    def __init__(
        self, env: simpy.Environment, name: str,
        green_duration: float, red_duration: float, journal: Journal,
    ) -> None:
        self.env = env
        self.name = name
        self.phases = (
            (LightPhase.VEHICLES, green_duration),
            (LightPhase.PEDESTRIANS, red_duration),
        )
        self.journal = journal
        self.phase = LightPhase.VEHICLES
        self.phase_ends_at = float(env.now) + green_duration
        self.phase_changed_event = env.event()

    def record_phase(self) -> None:
        self.journal.phase_changes.append(PhaseChange(float(self.env.now), self.name, self.phase))
        print(f"time={self.env.now:.3f}, signal={self.name}, phase={self.phase.value}")

    def run(self):
        index = 0
        self.record_phase()
        while True:
            yield self.env.timeout(self.phases[index][1])
            index = (index + 1) % len(self.phases)
            self.phase = self.phases[index][0]
            self.phase_ends_at = float(self.env.now) + self.phases[index][1]
            previous_event = self.phase_changed_event
            self.phase_changed_event = self.env.event()
            self.record_phase()
            previous_event.succeed(self.phase)


class RoadSegment:
    def __init__(
        self, env: simpy.Environment, name: str,
        parameters: SegmentParameters, journal: Journal,
        next_segment: RoadSegment | None = None,
        release_blocked_until: float = 0,
    ) -> None:
        if not math.isfinite(release_blocked_until) or release_blocked_until < 0:
            raise ValueError("Время блокировки должно быть конечным и неотрицательным")
        self.env = env
        self.name = name
        self.approach_length = parameters.approach_length
        self.intersection_length = parameters.intersection_length
        self.speed = parameters.speed
        self.capacity = parameters.capacity
        self.green_duration = parameters.green_duration
        self.red_duration = parameters.red_duration
        self.next_segment = next_segment
        self.release_blocked_until = release_blocked_until
        self.car_queue = simpy.Store(env)
        self.pedestrian_queue = simpy.Store(env)
        self.free_slots = None if self.capacity is None else simpy.Container(
            env, capacity=self.capacity, init=self.capacity,
        )
        self.light_phase_controller = PhaseController(
            env, name, self.green_duration, self.red_duration, journal,
        )

    @property
    def travel_time(self) -> float:
        return self.approach_length / self.speed

    @property
    def intersection_travel_time(self) -> float:
        return self.intersection_length / self.speed

    def has_crossing_permission(self) -> bool:
        controller = self.light_phase_controller
        return (
            self.env.now >= self.release_blocked_until
            and controller.phase is LightPhase.VEHICLES
            and self.env.now < controller.phase_ends_at
            and self.env.now + self.intersection_travel_time <= controller.phase_ends_at
        )


class Participant:
    def __init__(
        self, segment: RoadSegment, id: int, kind: Kind, appeared_at: float,
    ) -> None:
        self.env = segment.env
        self.id = f"{kind.value}-{id:03d}"
        self.kind = kind
        self.appeared_at = appeared_at
        self.finished_at: float | None = None
        self.route_finished_event = self.env.event()
        self.current_segment: RoadSegment | None = segment
        self.queue = segment.car_queue if kind is Kind.CAR else segment.pedestrian_queue
        self.speed = segment.speed if kind is Kind.CAR else 1.4
        self.visits = {segment.name: SegmentVisit(segment.name, entered_at=appeared_at)}

    def duration(self, distance: float) -> float:
        return distance / self.speed

    def log(self, segment: RoadSegment, event: str) -> None:
        print(f"time={self.env.now:.3f}, id={self.id}, kind={self.kind.value}, "
              f"segment={segment.name}, {event}")

    def approach_crossing(self, distance: float):
        yield self.env.timeout(self.duration(distance))
        segment = self.current_segment
        self.visits[segment.name].queue_entered_at = float(self.env.now)
        self.log(segment, "queued")
        yield self.queue.put(self)

    def run(self, distance: float):
        yield from self.approach_crossing(distance)
        yield self.route_finished_event
        self.finished_at = float(self.env.now)
        self.current_segment = None
        print(f"time={self.env.now:.3f}, id={self.id}, kind={self.kind.value}, finished")


class Car(Participant):
    def __init__(self, segment: RoadSegment, id: int, appeared_at: float) -> None:
        super().__init__(segment, id, Kind.CAR, appeared_at)
        self.reserved_segment: RoadSegment | None = None

    @staticmethod
    def serve_crossing(segment: RoadSegment):
        env = segment.env
        controller = segment.light_phase_controller
        while True:
            car = yield segment.car_queue.get()
            visit = car.visits[segment.name]
            next_segment = segment.next_segment
            while True:
                if env.now < segment.release_blocked_until:
                    yield env.timeout(segment.release_blocked_until - env.now)
                    continue
                if not segment.has_crossing_permission():
                    yield controller.phase_changed_event
                    continue
                if next_segment is None or next_segment.free_slots is None:
                    break

                request = next_segment.free_slots.get(1)
                wait_started = float(env.now)
                yield simpy.AnyOf(env, [request, controller.phase_changed_event])
                visit.downstream_blocked_time += float(env.now) - wait_started
                # Заявка могла исполниться одновременно со сменой сигнала.
                if not request.triggered:
                    request.cancel()
                elif not segment.has_crossing_permission():
                    yield next_segment.free_slots.put(1)
                else:
                    break

            if next_segment is not None:
                car.reserved_segment = next_segment
                car.visits[next_segment.name] = SegmentVisit(
                    next_segment.name, reserved_at=float(env.now),
                )
            visit.crossing_started_at = float(env.now)
            car.log(segment, "crossing_started")
            yield env.timeout(segment.intersection_travel_time)
            visit.crossing_finished_at = float(env.now)
            car.log(segment, "crossing_finished")

            # До этого момента заняты исходный участок и резерв следующего.
            car.current_segment = next_segment
            car.reserved_segment = None
            if next_segment is not None:
                car.visits[next_segment.name].entered_at = float(env.now)
                car.queue = next_segment.car_queue
                car.speed = next_segment.speed
            if segment.free_slots is not None:
                yield segment.free_slots.put(1)
            if next_segment is None:
                car.route_finished_event.succeed()
            else:
                env.process(car.approach_crossing(next_segment.approach_length))


class Pedestrian(Participant):
    def __init__(self, segment: RoadSegment, id: int, appeared_at: float) -> None:
        super().__init__(segment, id, Kind.PEDESTRIAN, appeared_at)

    @staticmethod
    def serve_crossing(segment: RoadSegment):
        env = segment.env
        controller = segment.light_phase_controller
        while True:
            phase = yield controller.phase_changed_event
            if phase is not LightPhase.PEDESTRIANS:
                continue
            phase_started_at = float(env.now)
            ready = [
                p for p in segment.pedestrian_queue.items
                if p.visits[segment.name].queue_entered_at < phase_started_at
            ]
            if not ready:
                continue
            group = []
            for _ in ready:
                group.append((yield segment.pedestrian_queue.get()))
            for pedestrian in group:
                pedestrian.visits[segment.name].crossing_started_at = phase_started_at
                pedestrian.log(segment, "crossing_started")
            yield env.timeout(PEDESTRIAN_CROSSING_DURATION)
            for pedestrian in group:
                pedestrian.visits[segment.name].crossing_finished_at = float(env.now)
                pedestrian.log(segment, "crossing_finished")
                pedestrian.route_finished_event.succeed()


def poisson_source(
    env: simpy.Environment, rate_per_hour: float, horizon_ms: float,
    rng: random.Random, spawn: Callable[[], None],
):
    while env.now < horizon_ms:
        interval = rng.expovariate(rate_per_hour / 3600)
        if env.now + interval >= horizon_ms:
            yield env.timeout(horizon_ms - env.now)
            return
        yield env.timeout(interval)
        spawn()


def scheduled_source(
    env: simpy.Environment, arrivals: tuple[float, ...],
    horizon_ms: float, spawn: Callable[[], None],
):
    for arrival in sorted(arrivals):
        if arrival >= horizon_ms:
            return
        yield env.timeout(arrival - env.now)
        spawn()


class SequentialSegmentsSimulation:
    def __init__(
        self, *, env: simpy.Environment, seed: int = 42, horizon_ms: float = 600,
        segment_parameters: Mapping[str, SegmentParameters] | None = None,
        car_arrivals: Iterable[float] | None = None,
        pedestrian_arrivals: Mapping[str, Iterable[float] | None] | None = None,
        is_car_at_stop_line: bool = False,
        c_release_blocked_until: float = 0,
    ) -> None:
        if not math.isfinite(horizon_ms) or horizon_ms <= env.now:
            raise ValueError("Горизонт генерации должен быть конечным и больше текущего времени")
        parameters = dict(DEFAULT_SEGMENT_PARAMETERS)
        if segment_parameters is not None:
            if set(segment_parameters) - parameters.keys():
                raise ValueError("Допустимы только участки A, B и C")
            parameters.update(segment_parameters)
        if parameters["A"].capacity is not None:
            raise ValueError("Вместимость входного участка A должна быть неограниченной")
        if parameters["B"].capacity is None or parameters["C"].capacity is None:
            raise ValueError("Вместимость B и C должна быть ограниченной")
        if pedestrian_arrivals is not None and set(pedestrian_arrivals) - parameters.keys():
            raise ValueError("Пешеходные потоки задаются только для A, B и C")
        self.env = env
        self.seed = seed
        self.horizon_ms = horizon_ms
        self.is_car_at_stop_line = is_car_at_stop_line
        self.car_arrivals = None if car_arrivals is None else tuple(car_arrivals)
        self.pedestrian_arrivals = {}
        for name in parameters:
            arrivals = None if pedestrian_arrivals is None else pedestrian_arrivals.get(name, ())
            self.pedestrian_arrivals[name] = None if arrivals is None else tuple(arrivals)
        for arrivals in (self.car_arrivals, *self.pedestrian_arrivals.values()):
            if arrivals is not None and any(
                not math.isfinite(time) or time < env.now for time in arrivals
            ):
                raise ValueError("Время появления должно быть конечным и не раньше текущего времени")
        self.journal = Journal()
        self.segments = {
            name: RoadSegment(
                env, name, parameters[name], self.journal,
                release_blocked_until=c_release_blocked_until if name == "C" else 0,
            )
            for name in ("A", "B", "C")
        }
        self.segments["A"].next_segment = self.segments["B"]
        self.segments["B"].next_segment = self.segments["C"]
        self.free_places_at_end_count: dict[str, float] = {}

    def source(self, arrivals, rate_per_hour, stream_seed, spawn):
        if arrivals is not None:
            return scheduled_source(self.env, arrivals, self.horizon_ms, spawn)
        return poisson_source(
            self.env, rate_per_hour, self.horizon_ms, random.Random(stream_seed), spawn,
        )

    def run(self) -> Journal:
        participant_processes = []

        def spawn(kind: Kind, segment: RoadSegment) -> None:
            participant_type = Car if kind is Kind.CAR else Pedestrian
            participant = participant_type(
                segment, self.journal.count(kind) + 1, float(self.env.now),
            )
            self.journal.participants.append(participant)
            participant.log(segment, "appeared")
            distance = segment.approach_length if kind is Kind.CAR and not self.is_car_at_stop_line else 0
            participant_processes.append(self.env.process(participant.run(distance)))

        for segment in self.segments.values():
            self.env.process(segment.light_phase_controller.run())
            self.env.process(Car.serve_crossing(segment))
            self.env.process(Pedestrian.serve_crossing(segment))
        sources = [self.env.process(self.source(
            self.car_arrivals, 360, self.seed,
            lambda: spawn(Kind.CAR, self.segments["A"]),
        ))]
        for index, (name, segment) in enumerate(self.segments.items(), start=1):
            sources.append(self.env.process(self.source(
                self.pedestrian_arrivals[name], 180, self.seed + index,
                lambda segment=segment: spawn(Kind.PEDESTRIAN, segment),
            )))
        self.env.run(until=simpy.AllOf(self.env, sources))
        self.env.run(until=simpy.AllOf(self.env, participant_processes))
        self.free_places_at_end_count = {
            name: segment.free_slots.level
            for name, segment in self.segments.items() if segment.free_slots is not None
        }
        self.journal.finished_at = float(self.env.now)
        return self.journal


def main() -> None:
    simulation = SequentialSegmentsSimulation(env=simpy.Environment())
    result = simulation.run()
    print(f"created_count={len(result.participants)}, finished_count={result.count_finished()}, "
          f"finished_at={result.finished_at:.3f}, free_places={simulation.free_places_at_end_count}")
    for participant in result.participants:
        for visit in participant.visits.values():
            print(f"id={participant.id}, segment={visit.segment_name}, "
                  f"reserved_at={visit.reserved_at}, entered_at={visit.entered_at}, "
                  f"queue_entered_at={visit.queue_entered_at}, "
                  f"crossing_started_at={visit.crossing_started_at}, "
                  f"crossing_finished_at={visit.crossing_finished_at}, "
                  f"downstream_blocked_time={visit.downstream_blocked_time}")


if __name__ == "__main__":
    main()
