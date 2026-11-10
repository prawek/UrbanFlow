"""Конфигурация сети с несколькими источниками и фиксированными маршрутами."""

from dataclasses import asdict, dataclass
from math import isclose

from .data import Route, validate_number


@dataclass(frozen=True)
class LinkConfig:
    name: str
    approach_length: float
    speed: float
    capacity: int
    upstream: str | None = None

    def validate(self):
        if not self.name:
            raise ValueError("У участка должно быть имя")
        validate_number("Длина участка", self.approach_length, allow_zero=True)
        validate_number("Скорость на участке", self.speed)
        if type(self.capacity) is not int or self.capacity <= 0:
            raise ValueError("Вместимость должна быть положительным целым числом")


@dataclass(frozen=True)
class PassageConfig:
    segment: str
    length: float
    speed: float

    @property
    def travel_time(self):
        return self.length / self.speed

    def validate(self):
        validate_number("Длина проезда перекрёстка", self.length)
        validate_number("Скорость проезда перекрёстка", self.speed)


@dataclass(frozen=True)
class JunctionConfig:
    name: str
    approaches: dict[str, PassageConfig]
    phases: tuple[tuple[str, float], ...]
    exits: tuple[str, ...] = ()
    initial_phase: int = 0
    initial_elapsed: float = 0

    def validate(self):
        if not self.name or not self.approaches:
            raise ValueError("Перекрёстку нужны имя и входные подходы")
        names = [name for name, _ in self.phases]
        if len(set(names)) != len(names) or set(names) != set(self.approaches):
            raise ValueError("Каждому подходу нужна ровно одна собственная фаза")
        for name, duration in self.phases:
            validate_number("Длительность фазы", duration)
            self.approaches[name].validate()
            if duration < self.approaches[name].travel_time:
                raise ValueError("Фаза должна вмещать полный проезд перекрёстка")
        if type(self.initial_phase) is not int or not 0 <= self.initial_phase < len(self.phases):
            raise ValueError("Неверный индекс начальной фазы")
        validate_number("Прошедшее время начальной фазы", self.initial_elapsed, allow_zero=True)
        if self.initial_elapsed >= self.phases[self.initial_phase][1]:
            raise ValueError("Начальная фаза ещё не должна закончиться")


@dataclass(frozen=True)
class SourceConfig:
    name: str
    segment: str
    rate: float
    seed: int


@dataclass(frozen=True)
class RouteConfig:
    name: Route
    source: str
    segments: tuple[str, ...]
    exit: str
    probability: float


@dataclass(frozen=True)
class RouteArrival:
    time: float
    route: Route

    def __post_init__(self):
        validate_number("Время появления", self.time, allow_zero=True)
        if not isinstance(self.route, Route):
            raise ValueError("Маршрут должен быть константой Route")


@dataclass(frozen=True)
class NetworkConfig:
    segments: tuple[LinkConfig, ...]
    intersections: tuple[JunctionConfig, ...]
    sources: tuple[SourceConfig, ...]
    routes: tuple[RouteConfig, ...]
    horizon: float = 600
    route_seed: int = 4

    def validate(self):
        validate_number("Горизонт генерации", self.horizon)
        for entries in (self.segments, self.intersections, self.sources, self.routes):
            if (not entries or any(not entry.name for entry in entries)
                    or len({entry.name for entry in entries}) != len(entries)):
                raise ValueError("Имена объектов одного типа должны быть непустыми и различаться")
        segments = {segment.name: segment for segment in self.segments}
        junctions = {junction.name: junction for junction in self.intersections}
        sources = {source.name: source for source in self.sources}
        destinations = {}
        for segment in self.segments:
            segment.validate()
            if segment.upstream is not None and segment.upstream not in junctions:
                raise ValueError("Неизвестный исходящий перекрёсток участка")
        for junction in self.intersections:
            junction.validate()
            for passage in junction.approaches.values():
                if passage.segment not in segments or passage.segment in destinations:
                    raise ValueError("Каждому участку нужен один известный выходной перекрёсток")
                destinations[passage.segment] = junction.name
        if destinations.keys() != segments.keys():
            raise ValueError("Для каждого участка нужен подход к перекрёстку")
        seeds = [source.seed for source in self.sources] + [self.route_seed]
        if any(type(seed) is not int for seed in seeds) or len(set(seeds)) != len(seeds):
            raise ValueError("Источникам и выбору маршрутов нужны отдельные целочисленные seed")
        if len({source.segment for source in self.sources}) != len(self.sources):
            raise ValueError("Каждому источнику нужен отдельный входной участок")
        for source in self.sources:
            validate_number("Интенсивность источника", source.rate, allow_zero=True)
            if source.segment not in segments or segments[source.segment].upstream is not None:
                raise ValueError("Источник должен вести на входной участок сети")
            choices = [route for route in self.routes if route.source == source.name]
            if not choices or not isclose(sum(route.probability for route in choices), 1,
                                         rel_tol=0, abs_tol=1e-12):
                raise ValueError("Вероятности маршрутов каждого источника должны давать 1")
        for route in self.routes:
            validate_number("Вероятность маршрута", route.probability, allow_zero=True)
            if route.probability > 1 or not isinstance(route.name, Route):
                raise ValueError("Неверное имя маршрута или вероятность вне [0, 1]")
            if (route.source not in sources or not route.segments
                    or any(name not in segments for name in route.segments)
                    or len(set(route.segments)) != len(route.segments)):
                raise ValueError("Маршруту нужны известный источник и путь без повторов участков")
            if route.segments[0] != sources[route.source].segment:
                raise ValueError("Маршрут должен начинаться на участке своего источника")
            for previous, following in zip(route.segments, route.segments[1:]):
                if segments[following].upstream != destinations[previous]:
                    raise ValueError("Маршрут содержит несвязанные участки")
            if route.exit not in junctions[destinations[route.segments[-1]]].exits:
                raise ValueError("Маршрут должен заканчиваться доступным выходом")

    def parameters(self, seed):
        """Возвращает JSON-совместимые настройки с фактическими seed запуска."""
        return {
            "horizon": self.horizon, "route_seed": seed + self.route_seed,
            "segments": [asdict(segment) for segment in self.segments],
            "intersections": [
                {**asdict(junction), "phases": [list(phase) for phase in junction.phases],
                 "exits": list(junction.exits)} for junction in self.intersections
            ],
            "sources": [{**asdict(source), "seed": seed + source.seed} for source in self.sources],
            "routes": [{**asdict(route), "name": route.name.value,
                        "segments": list(route.segments)} for route in self.routes],
        }
