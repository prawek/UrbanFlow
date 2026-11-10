"""Задание 8: четыре источника, три местных светофора и шесть маршрутов."""

from dataclasses import replace
from random import Random

from ..common.data import Route, validate_number
from ..common.flows import source
from ..common.network_config import (
    JunctionConfig, LinkConfig, NetworkConfig, PassageConfig, RouteArrival, RouteConfig, SourceConfig,
)
from ..common.road_network import TrafficNetwork


def configuration(*, S1=480, S2=120, S3=180, S4=120, p1_left=0.25, p3_to_I3=0.6, horizon=600):
    """Все числа эксперимента; L12 и L23 не включают длины перекрёстков."""
    for name, probability in (("p1_left", p1_left), ("p3_to_I3", p3_to_I3)):
        validate_number(name, probability, allow_zero=True)
        if probability > 1:
            raise ValueError(f"{name} должна находиться в [0, 1]")
    return NetworkConfig(
        segments=(
            LinkConfig("S1_I1", 150, 15, 6),
            LinkConfig("S2_I1", 80, 10, 3),
            LinkConfig("S3_I2", 60, 10, 3),
            LinkConfig("S4_I3", 70, 10, 3),
            LinkConfig("I1_I2", 120, 12, 5, upstream="I1"),
            LinkConfig("I2_I3", 150, 15, 4, upstream="I2"),
            LinkConfig("I2_I1", 120, 12, 3, upstream="I2"),
        ),
        intersections=(
            JunctionConfig(
                "I1", {"west": PassageConfig("S1_I1", 30, 15),
                       "north": PassageConfig("S2_I1", 20, 10),
                       "east": PassageConfig("I2_I1", 30, 15)},
                (("west", 20), ("north", 10), ("east", 10)), exits=("west_I1",),
            ),
            JunctionConfig(
                "I2", {"west": PassageConfig("I1_I2", 40, 10),
                       "north": PassageConfig("S3_I2", 30, 10)},
                (("west", 25), ("north", 15)), exits=("north_I2",),
            ),
            JunctionConfig(
                "I3", {"west": PassageConfig("I2_I3", 30, 15),
                       "north": PassageConfig("S4_I3", 20, 10)},
                (("west", 18), ("north", 12)), exits=("east_I3",),
            ),
        ),
        sources=(SourceConfig("S1", "S1_I1", S1, 0), SourceConfig("S2", "S2_I1", S2, 1),
                 SourceConfig("S3", "S3_I2", S3, 2), SourceConfig("S4", "S4_I3", S4, 3)),
        routes=(
            RouteConfig(Route.S1_STRAIGHT, "S1", ("S1_I1", "I1_I2", "I2_I3"),
                        "east_I3", 1 - p1_left),
            RouteConfig(Route.S1_LEFT, "S1", ("S1_I1", "I1_I2"), "north_I2", p1_left),
            RouteConfig(Route.S2_TO_I3, "S2", ("S2_I1", "I1_I2", "I2_I3"), "east_I3", 1),
            RouteConfig(Route.S3_TO_I3, "S3", ("S3_I2", "I2_I3"), "east_I3", p3_to_I3),
            RouteConfig(Route.S3_TO_I1, "S3", ("S3_I2", "I2_I1"), "west_I1", 1 - p3_to_I3),
            RouteConfig(Route.S4_TO_EXIT, "S4", ("S4_I3",), "east_I3", 1),
        ),
        horizon=horizon,
    )


CONFIG = configuration()


def run(seed=42, *, config=CONFIG, car_arrivals=None, release_blocked_until=None):
    """None включает четыре случайных источника; список RouteArrival заменяет их все."""
    config.validate()
    routes = {route.name: route for route in config.routes}
    prepared = None
    if car_arrivals is not None:
        arrivals = list(car_arrivals)
        if any(not isinstance(arrival, RouteArrival) or arrival.route not in routes
               for arrival in arrivals):
            raise ValueError("Расписание должно содержать RouteArrival с известными маршрутами")
        prepared = {entry.name: [] for entry in config.sources}
        for arrival in sorted(arrivals, key=lambda entry: entry.time):
            if arrival.time < config.horizon:
                prepared[routes[arrival.route].source].append(arrival)
    network = TrafficNetwork(seed, config, release_blocked_until=release_blocked_until)
    sources = [network.env.process(source(
        network.env, entry.rate, config.horizon, Random(seed + entry.seed),
        None if prepared is None else prepared[entry.name],
        spawn=lambda arrival, name=entry.name: network.spawn_car(
            name, arrival.route if arrival is not None else None,
        ),
    )) for entry in config.sources]
    return network.run(sources)


def compare(seed=42, *, config=CONFIG):
    """Меняет только длительности I2: west/north 25/15 → 35/5 с."""
    changed = replace(config, intersections=tuple(
        replace(junction, phases=(("west", 35), ("north", 5))) if junction.name == "I2"
        else junction for junction in config.intersections
    ))
    return run(seed, config=config), run(seed, config=changed)


def demo():
    """По одному автомобилю каждого маршрута, каждый в собственной симуляции."""
    config = replace(CONFIG, horizon=1)
    return tuple(run(config=config, car_arrivals=[RouteArrival(0, route)]) for route in Route)


def spillback_demo(seed=42):
    return run(seed, release_blocked_until={"I3": 240})


if __name__ == "__main__":
    from ..common.reporting import display

    display(run(), show_log=True)
