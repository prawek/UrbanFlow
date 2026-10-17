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
