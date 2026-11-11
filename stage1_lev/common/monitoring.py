"""Журналы событий, посещения участков и статистика симуляций."""

from dataclasses import dataclass, field

from .data import (
    CorridorParticipant, CrossingParticipant, Direction, Participant, RoadParticipant,
)


@dataclass(frozen=True)
class Event:
    time: float
    actor: str
    kind: str
    action: str


@dataclass
class Result:
    exercise: int | str
    seed: int | None = None
    events: list[Event] = field(default_factory=list)
    participants: list[Participant] = field(default_factory=list)
    finished_at: float = 0.0

    @property
    def participant_type(self):
        return Participant

    def record(self, time: float, actor: str, kind: str, action: str) -> None:
        self.events.append(Event(float(time), actor, kind, action))

    def extra_tables(self):
        return ()

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


@dataclass
class SegmentVisit:
    participant_id: str
    segment: str
    entered_at: float
    queue_entered_at: float | None = None
    crossing_started_at: float | None = None
    crossing_finished_at: float | None = None
    exited_at: float | None = None
    capacity_wait: float = 0.0


@dataclass
class Occupancy:
    time: float
    segment: str
    capacity: int | None
    occupied_count: int
    reserved_count: int
    free_count: int | None
    car_queue_len: int
    pedestrian_queue_len: int


@dataclass
class RoadResult(CrossingResult):
    visits: list[SegmentVisit] = field(default_factory=list)
    monitoring: list[Occupancy] = field(default_factory=list)
    parameters: list[dict] = field(default_factory=list)
    closures: dict = field(default_factory=dict)
    final_segments: dict = field(default_factory=dict)

    @property
    def participant_type(self):
        return RoadParticipant

    @property
    def visit_type(self):
        return SegmentVisit

    @property
    def occupancy_type(self):
        return Occupancy

    def new_visit(self, participant_id, segment, entered_at, **details):
        visit = self.visit_type(participant_id, segment, entered_at, **details)
        self.visits.append(visit)
        return visit

    def record_occupancy(self, time, segment, capacity, occupied, free,
                         car_queue_len, pedestrian_queue_len, **details):
        # Container.get уже резервирует место до возобновления ожидающего процесса.
        reserved = capacity - free - occupied if free is not None else 0
        row = self.occupancy_type(float(time), segment, capacity, occupied, reserved,
                                  free, car_queue_len, pedestrian_queue_len, **details)
        self.monitoring.append(row)
        return row

    def capture_final_segments(self, segments):
        for segment in segments:
            row = segment.snapshot()
            self.final_segments[segment.name] = {
                "capacity": row.capacity, "free_count": row.free_count,
                "occupied_count": row.occupied_count, "reserved_count": row.reserved_count,
                "car_queue_len": row.car_queue_len,
                "pedestrian_queue_len": row.pedestrian_queue_len,
                "pending_requests": (len(segment.free_slots.get_queue)
                                     if segment.free_slots is not None else 0),
            }

    def extra_tables(self):
        return (("visits.csv", self.visit_type, self.visits),
                ("monitoring.csv", self.occupancy_type, self.monitoring))

    def summary(self):
        result = super().summary()
        result["car"]["capacity_wait_total"] = sum(v.capacity_wait for v in self.visits)
        result["segments"] = self.final_segments
        result["parameters"] = self.parameters
        result["closures"] = self.closures
        return result


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

    @property
    def visit_type(self):
        return CorridorVisit

    @property
    def occupancy_type(self):
        return CorridorOccupancy

    def completed_by_direction(self):
        return {
            direction.value: sum(p.kind == "car" and p.entry_direction == direction
                                 and p.arrived_at is not None for p in self.participants)
            for direction in Direction
        }

    def summary(self):
        result = super().summary()
        result["completed_by_direction"] = self.completed_by_direction()
        result["directions"] = {}
        for direction in Direction:
            members = [p for p in self.participants if p.kind == "car" and p.entry_direction == direction]
            result["directions"][direction.value] = {
                "created": len(members),
                "initial": sum(p.is_initial for p in members),
                "generated": sum(not p.is_initial for p in members),
                "arrived": result["completed_by_direction"][direction.value],
            }
        return result
