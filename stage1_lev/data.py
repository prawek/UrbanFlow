"""Записи результатов"""

from dataclasses import dataclass, field
from math import isfinite


def validate_number(name: str, value: float, *, allow_zero: bool = False) -> None:
    if not isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        bound = "неотрицательным" if allow_zero else "положительным"
        raise ValueError(f"{name} должно быть конечным {bound} числом")


@dataclass(frozen=True)
class Event:
    time: float
    actor: str
    kind: str
    action: str


@dataclass
class Participant:
    id: str
    kind: str
    appeared_at: float
    arrived_at: float | None = None

    @property
    def travel_time(self) -> float | None:
        if self.arrived_at is None:
            return None
        return self.arrived_at - self.appeared_at


@dataclass
class Result:
    exercise: int
    seed: int | None = None
    events: list[Event] = field(default_factory=list)
    participants: list[Participant] = field(default_factory=list)
    finished_at: float = 0.0

    @property
    def participant_type(self):
        return Participant

    def record(self, time: float, actor: str, kind: str, action: str) -> None:
        self.events.append(Event(float(time), actor, kind, action))

    def summary(self) -> dict:
        counts = {}
        for kind in ("car", "pedestrian"):
            members = [p for p in self.participants if p.kind == kind]
            counts[kind] = {
                "created": len(members),
                "arrived": sum(p.arrived_at is not None for p in members),
            }
        return {
            "exercise": self.exercise, "seed": self.seed,
            "finished_at": self.finished_at, "event_count": len(self.events), **counts,
        }


@dataclass
class CrossingParticipant(Participant):
    velocity: float = 0.0
    queue_entered_at: float | None = None
    queue_left_at: float | None = None

    @property
    def queue_wait(self) -> float | None:
        if self.queue_left_at is None:
            return None
        return self.queue_left_at - self.queue_entered_at


@dataclass
class Vehicle(CrossingParticipant):
    velocity: float = 15.0


@dataclass
class Pedestrian(CrossingParticipant):
    velocity: float = 1.4


@dataclass
class CrossingResult(Result):
    @property
    def participant_type(self):
        return CrossingParticipant

    def summary(self) -> dict:
        summary = super().summary()
        for kind in ("car", "pedestrian"):
            finished = [p for p in self.participants
                        if p.kind == kind and p.arrived_at is not None]
            summary[kind]["mean_travel_time"] = (
                sum(p.travel_time for p in finished) / len(finished) if finished else None
            )
            summary[kind]["mean_queue_wait"] = (
                sum(p.queue_wait for p in finished) / len(finished) if finished else None
            )
        return summary
