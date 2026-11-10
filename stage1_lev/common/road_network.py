"""Дорожная сеть: сборка графа, создание участников и завершение симуляции."""

from dataclasses import asdict
from random import Random

import simpy

from .data import CorridorParticipant
from .monitoring import CorridorResult
from .roads import Intersection


class RoadNetwork:
    def __init__(self, seed, config, *, release_blocked_until=None):
        if config.incoming.capacity is not None:
            raise ValueError("Вместимость входного участка не ограничена")
        self.env = simpy.Environment()
        self.result = CorridorResult(exercise=7, seed=seed)
        self.active_count = 0
        self.has_sources_finished = False
        self.done = self.env.event()
        self.counts = {"car": 0, "pedestrian": 0}
        self.intersections = []
        self.by_name = {}
        closures = release_blocked_until or {}

        def build(intersection_config):
            name = intersection_config.incoming.name
            if name in self.by_name:
                if self.by_name[name].config is intersection_config:
                    return self.by_name[name]
                raise ValueError("Имена участков должны различаться")
            intersection = Intersection(
                self.env, self.result, intersection_config,
                rng=Random(seed + 2 + len(self.intersections)), finish=self.finish,
                release_blocked_until=closures.get(name, 0),
            )
            self.by_name[name] = intersection
            self.intersections.append(intersection)
            for direction, receiver_config in intersection_config.outgoing.items():
                intersection.outgoing[direction] = build(receiver_config)
            return intersection

        self.central = build(config)
        if set(closures) - self.by_name.keys():
            raise ValueError("Запрет задан для неизвестного перекрёстка")
        for intersection in self.intersections:
            intersection.start()
            parameters = asdict(intersection.config.incoming)
            parameters["phases"] = [list(phase) for phase in intersection.config.phases_duration]
            parameters["car_phases"] = {
                direction.value: phase for direction, phase in intersection.config.car_phases.items()
            }
            parameters["left_probability"] = intersection.config.left_probability
            parameters["right_probability"] = intersection.config.right_probability
            parameters["pedestrian_crossing_time"] = intersection.config.pedestrian_crossing_time
            parameters["outgoing"] = {
                direction.value: receiver.incoming.name
                for direction, receiver in intersection.outgoing.items()
            }
            self.result.parameters.append(parameters)
        self.result.closures = {
            name: {"release_blocked_until": value} for name, value in closures.items()
        }

    def register(self, participant):
        self.result.participants.append(participant)
        self.result.record(self.env.now, participant.id, participant.kind, "appeared")
        self.active_count += 1

    def prefill(self, intersection, count, *, entry_direction=None):
        road = intersection.incoming
        if (type(count) is not int or count < 0
                or road.capacity is None or count > road.free_slots.level):
            raise ValueError("Предзаполнение должно быть целым числом в пределах свободных мест")
        for number in range(1, count + 1):
            road.free_slots.get(1)
            car = CorridorParticipant(
                f"initial-{road.name}-{number}", "car", float(self.env.now),
                entry_direction=entry_direction, is_initial=True,
            )
            self.register(car)
            intersection.enter(car, at_stop=True)

    def spawn_car(self, direction=None, *, at_stop=False):
        self.counts["car"] += 1
        car = CorridorParticipant(f"car-{self.counts['car']}", "car", float(self.env.now))
        self.register(car)
        self.central.enter(car, direction=direction, at_stop=at_stop)

    def spawn_pedestrian(self):
        self.counts["pedestrian"] += 1
        pedestrian = CorridorParticipant(
            f"pedestrian-{self.counts['pedestrian']}", "pedestrian", float(self.env.now), velocity=1.4,
        )
        self.register(pedestrian)
        self.central.enqueue_pedestrian(pedestrian)

    def finish(self, participant):
        participant.current_segment = None
        participant.reserved_segment = None
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
        self.result.capture_final_segments(intersection.incoming for intersection in self.intersections)
        return self.result
