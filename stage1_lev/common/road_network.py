"""Дорожная сеть: сборка графа, создание участников и завершение симуляции."""

from dataclasses import asdict
from random import Random

import simpy

from .data import CorridorParticipant, NetworkVehicle
from .monitoring import CorridorResult, NetworkResult, NetworkState
from .roads import Intersection, NetworkIntersection, RoadSegment


class RoadSimulation:
    """Общий жизненный цикл: регистрация, остановка источников и полный слив."""

    def __init__(self, result):
        self.env = simpy.Environment()
        self.result = result
        self.active_count = 0
        self.has_sources_finished = False
        self.done = self.env.event()

    def register(self, participant):
        self.result.participants.append(participant)
        self.result.record(self.env.now, participant.id, participant.kind, "appeared")
        self.active_count += 1

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
        self.result.capture_final_segments(self.road_segments)
        return self.result


class RoadNetwork(RoadSimulation):
    """Разветвлённая сеть задания 7 с местным выбором поворотов."""

    def __init__(self, seed, config, *, release_blocked_until=None):
        if config.incoming.capacity is not None:
            raise ValueError("Вместимость входного участка не ограничена")
        super().__init__(CorridorResult(exercise=7, seed=seed))
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
        self.road_segments = [intersection.incoming for intersection in self.intersections]

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


class TrafficNetwork(RoadSimulation):
    """Граф с несколькими входами, слияниями и неизменными маршрутами."""

    def __init__(self, seed, config, *, release_blocked_until=None):
        config.validate()
        super().__init__(NetworkResult(exercise=8, seed=seed, parameters=[config.parameters(seed)]))
        self.config = config
        self.routes = {route.name: route for route in config.routes}
        self.sources = {source.name: source for source in config.sources}
        self.route_rng = Random(seed + config.route_seed)
        self.counts = {name: 0 for name in self.sources}
        self.completed_count = 0
        self.segments = {link.name: RoadSegment(self.env, self.result, link, lanes=("inbound",))
                         for link in config.segments}
        self.road_segments = list(self.segments.values())
        self.external_queues = {name: simpy.Store(self.env) for name in self.sources}
        self.source_arrived = {name: self.env.event() for name in self.sources}
        closures = release_blocked_until or {}
        if set(closures) - {junction.name for junction in config.intersections}:
            raise ValueError("Запрет задан для неизвестного перекрёстка")
        self.intersections = {
            junction.name: NetworkIntersection(
                self.env, self.result, junction, self.segments, finish=self.finish,
                release_blocked_until=closures.get(junction.name, 0),
            ) for junction in config.intersections
        }
        self.result.closures = {
            name: {"release_blocked_until": value} for name, value in closures.items()
        }
        self.free_flow_times = {}
        passages = {passage.segment: passage.travel_time for junction in config.intersections
                    for passage in junction.approaches.values()}
        for route in config.routes:
            self.free_flow_times[route.name] = sum(
                self.segments[name].travel_time + passages[name] for name in route.segments
            )
        for segment in self.road_segments:
            segment.snapshot()
        for intersection in self.intersections.values():
            intersection.start()
        for name in self.sources:
            self.env.process(self.admit_source(name))
        self.snapshot()

    def choose_route(self, source):
        choices = [route for route in self.routes.values()
                   if route.source == source and route.probability > 0]
        if len(choices) == 1:
            return choices[0].name
        value = self.route_rng.random()
        cumulative = 0.0
        for route in choices:
            cumulative += route.probability
            if value < cumulative:
                return route.name
        return choices[-1].name

    def spawn_car(self, source, route=None):
        route = self.choose_route(source) if route is None else route
        if route not in self.routes or self.routes[route].source != source:
            raise ValueError("Маршрут не принадлежит источнику")
        self.counts[source] += 1
        car = NetworkVehicle(
            f"{source}-{self.counts[source]}", "car", float(self.env.now), source=source,
            route=route, free_flow_time=self.free_flow_times[route],
            path=tuple(self.segments[name] for name in self.routes[route].segments),
        )
        self.register(car)
        self.external_queues[source].put(car)
        self.result.record(self.env.now, car.id, "car", f"external_queued:{source}")
        previous = self.source_arrived[source]
        self.source_arrived[source] = self.env.event()
        previous.succeed()
        self.snapshot()
        return car

    def admit_source(self, source):
        queue = self.external_queues[source]
        road = self.segments[self.sources[source].segment]
        while True:
            while not queue.items:
                yield self.source_arrived[source]
            car = queue.items[0]
            yield road.free_slots.get(1)
            queue.get()
            car.admitted_at = float(self.env.now)
            self.result.record(self.env.now, car.id, "car", "admitted")
            road.enter(car)
            self.env.process(road.approach(car, "inbound"))
            self.snapshot()

    def finish(self, car):
        car.exit = self.routes[car.route].exit
        super().finish(car)
        self.completed_count += 1
        self.snapshot()

    def snapshot(self):
        external = sum(len(queue.items) for queue in self.external_queues.values())
        self.result.states.append(NetworkState(
            float(self.env.now), len(self.result.participants), self.completed_count,
            self.active_count - external, external,
        ))

    def run(self, sources):
        result = super().run([*sources, self.env.timeout(max(0, self.config.horizon - self.env.now))])
        result.final_external_queues = {name: len(queue.items)
                                        for name, queue in self.external_queues.items()}
        return result
