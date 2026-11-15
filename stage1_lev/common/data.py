"""Участники движения, направления и входные расписания."""

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite


def validate_number(name: str, value: float, *, allow_zero: bool = False) -> None:
    if not isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        bound = "неотрицательным" if allow_zero else "положительным"
        raise ValueError(f"{name} должно быть конечным {bound} числом")


class Direction(str, Enum):
    STRAIGHT = "straight"
    LEFT = "left"
    RIGHT = "right"


class Route(str, Enum):
    S1_STRAIGHT = "S1_straight"
    S1_LEFT = "S1_left"
    S2_TO_I3 = "S2_to_I3"
    S3_TO_I3 = "S3_to_I3"
    S3_TO_I1 = "S3_to_I1"
    S4_TO_EXIT = "S4_to_exit"


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
class RoadParticipant(CrossingParticipant):
    current_segment: object | None = field(default=None, repr=False, compare=False,
                                          metadata={"export": False})
    # Место появления местного пешехода; сохраняется после выхода из модели.
    local_segment: str = ""
    queue_wait_total: float = 0.0

    @property
    def queue_wait(self):
        """Сумма ожиданий на маршруте для CrossingResult.summary()."""
        return self.queue_wait_total if self.arrived_at is not None else None


@dataclass
class Vehicle(RoadParticipant):
    velocity: float = 15.0


@dataclass
class Pedestrian(RoadParticipant):
    velocity: float = 1.4


@dataclass
class CorridorParticipant(RoadParticipant):
    direction: Direction | None = None
    # Первый поворот нужен для статистики потока; direction меняется на маршруте.
    entry_direction: Direction | None = None
    is_initial: bool = False
    reserved_segment: object | None = field(default=None, repr=False, compare=False,
                                           metadata={"export": False})


@dataclass
class NetworkVehicle(RoadParticipant):
    source: str = ""
    route: Route | None = None
    admitted_at: float | None = None
    exit: str = ""
    free_flow_time: float = 0.0
    reserved_segment: object | None = field(default=None, repr=False, compare=False,
                                           metadata={"export": False})
    path: tuple = field(default=(), repr=False, compare=False, metadata={"export": False})
    path_index: int = field(default=0, metadata={"export": False})

    @property
    def next_segment(self):
        return self.path[self.path_index + 1] if self.path_index + 1 < len(self.path) else None

    @property
    def external_wait(self):
        return self.admitted_at - self.appeared_at if self.admitted_at is not None else None

    @property
    def delay(self):
        return self.travel_time - self.free_flow_time if self.arrived_at is not None else None
