"""Перекрёстки и движение по графу с резервированием принимающих участков."""

from dataclasses import asdict
from random import Random

import simpy

from .data import CorridorParticipant, Direction, validate_number
from .monitoring import CorridorResult
from .roads import RoadSegment
from .signals import PhaseController


class Intersection:
    def __init__(self, env, result, config, *, rng, finish, release_blocked_until=0):
        config.validate()
        validate_number("Время открытия перекрёстка", release_blocked_until, allow_zero=True)
        self.env = env
        self.config = config
        self.rng = rng
        self.finish = finish
        self.incoming = RoadSegment(env, result, config.incoming, lanes=config.directions)
        self.controller = PhaseController(
            env, result, f"traffic_light:{self.incoming.name}", config.phases_duration,
        )
        self.outgoing = {}
        self.release_blocked_until = release_blocked_until

    def choose_direction(self, preferred=None):
        """Выбирает местный поворот один раз при входе; ожидание его не меняет."""
        if preferred is not None:
            if not isinstance(preferred, Direction) or preferred not in self.config.directions:
                raise ValueError("Направление отсутствует на перекрёстке")
            return preferred
        choices = [(direction, probability)
                   for direction, probability in self.config.probabilities.items()
                   if probability > 0]
        if len(choices) == 1:
            return choices[0][0]
        value = self.rng.random()
        cumulative = 0.0
        for direction, probability in choices:
            cumulative += probability
            if value < cumulative:
                return direction
        return choices[-1][0]

    def enter(self, car, *, direction=None, at_stop=False):
        car.direction = self.choose_direction(direction)
        if car.entry_direction is None:
            car.entry_direction = car.direction
        self.incoming.enter(car, car.direction)
        self.env.process(self.incoming.approach(car, car.direction, at_stop=at_stop))

    def enqueue_pedestrian(self, pedestrian):
        if not any(phase == "pedestrian" for phase, _ in self.config.phases_duration):
            raise ValueError("У перекрёстка нет пешеходной фазы")
        pedestrian.queue_entered_at = float(self.env.now)
        pedestrian.local_segment = self.incoming.name
        self.incoming.pedestrians.put(pedestrian)
        self.incoming.record(pedestrian, "queued")
        self.incoming.snapshot()

    def reserve_receiver(self, car, direction):
        """Ждёт свой зелёный и место; при потере разрешения отменяет резерв."""
        road = self.incoming
        receiver = self.outgoing.get(direction)
        target = receiver.incoming if receiver is not None else None
        phase = self.config.car_phases[direction]
        while True:
            if self.env.now < self.release_blocked_until:
                yield self.env.timeout(self.release_blocked_until - self.env.now)
                continue
            if not self.controller.allows(phase, road.crossing_time):
                yield self.controller.changed
                continue
            if target is None:
                return
            if target.free_slots is None:
                car.reserved_segment = target
                target.reserve_visit(car)
                target.record(car, "reserved")
                return
            request = target.free_slots.get(1)
            target.snapshot()
            wait_started = self.env.now
            if not request.triggered:
                road.record(car, "capacity_wait")
            yield request | self.controller.changed
            road.visits[car.id].capacity_wait += self.env.now - wait_started
            # get и changed могут сработать одновременно: проверяем саму заявку
            # и фактическую фазу, а не только события, попавшие в AnyOf.
            if request.triggered and self.controller.allows(phase, road.crossing_time):
                car.reserved_segment = target
                target.reserve_visit(car)
                target.record(car, "reserved")
                target.snapshot()
                return
            if request.triggered:
                target.free_slots.put(1)
            else:
                request.cancel()
            target.snapshot()

    def serve_cars(self, direction):
        """Одинаковое обслуживание полосы с продолжением маршрута или выходом."""
        road = self.incoming
        queue = road.queues[direction]
        while True:
            while not queue.items:
                yield road.arrived[direction]
            car = queue.items[0]
            yield from self.reserve_receiver(car, direction)
            yield queue.get()
            road.begin_crossing(car)
            yield self.env.timeout(road.crossing_time)
            road.finish_crossing(car)
            receiver = self.outgoing.get(direction)
            if receiver is None:
                self.finish(car)
            else:
                receiver.enter(car)

    def serve_pedestrians(self):
        road = self.incoming
        while True:
            yield self.controller.changed
            if self.controller.phase != "pedestrian":
                continue
            phase_start = self.env.now
            group = []
            while road.pedestrians.items and road.pedestrians.items[0].queue_entered_at < phase_start:
                pedestrian = yield road.pedestrians.get()
                pedestrian.queue_left_at = float(self.env.now)
                pedestrian.queue_wait_total = self.env.now - pedestrian.queue_entered_at
                road.record(pedestrian, "crossing_started")
                group.append(pedestrian)
            road.snapshot()
            if group:
                yield self.env.timeout(self.config.pedestrian_crossing_time)
                for pedestrian in group:
                    road.record(pedestrian, "crossing_finished")
                    self.finish(pedestrian)

    def start(self):
        self.incoming.snapshot()
        self.env.process(self.controller.run())
        for direction in self.config.directions:
            self.env.process(self.serve_cars(direction))
        if any(phase == "pedestrian" for phase, _ in self.config.phases_duration):
            self.env.process(self.serve_pedestrians())


class CoordinatedCorridor:
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
