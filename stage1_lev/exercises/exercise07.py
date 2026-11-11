"""Задание 7: три направления и согласованные светофоры принимающих участков."""

from random import Random

from ..common.corridor import CoordinatedCorridor
from ..common.data import CarArrival, Direction, validate_number
from ..common.flows import prepare_arrivals, source
from ..common.roads import IntersectionConfig, SegmentConfig


CONFIG = IntersectionConfig(
    incoming=SegmentConfig("A", 150, 30, 15),
    phases_duration=((Direction.STRAIGHT.value, 24), (Direction.LEFT.value, 12),
                     (Direction.RIGHT.value, 12), ("pedestrian", 12)),
    left_probability=0.3,
    right_probability=0.2,
    outgoing={
        Direction.STRAIGHT: IntersectionConfig(
            incoming=SegmentConfig("B_T", 150, 30, 15, capacity=5),
            phases_duration=(("red", 12), ("green", 24), ("red", 24)),
            car_phases={Direction.STRAIGHT: "green"},
            left_probability=0, right_probability=0,
        ),
        Direction.LEFT: IntersectionConfig(
            incoming=SegmentConfig("B_L", 150, 30, 15, capacity=3),
            phases_duration=(("red", 36), ("green", 12), ("red", 12)),
            car_phases={Direction.STRAIGHT: "green"},
            left_probability=0, right_probability=0,
        ),
        Direction.RIGHT: IntersectionConfig(
            incoming=SegmentConfig("B_R", 150, 30, 15, capacity=2),
            phases_duration=(("red", 48), ("green", 12)),
            car_phases={Direction.STRAIGHT: "green"},
            left_probability=0, right_probability=0,
        ),
    },
)


def run(seed=42, *, config=CONFIG, horizon=600, car_rate=360, pedestrian_rate=180,
        car_arrivals=None, pedestrian_arrivals=None, cars_at_crossing=False,
        prefilled_left_count=0, left_release_blocked_until=0):
    validate_number("Горизонт генерации", horizon)
    validate_number("Интенсивность автомобилей", car_rate, allow_zero=True)
    validate_number("Интенсивность пешеходов", pedestrian_rate, allow_zero=True)
    validate_number("Время открытия левого принимающего перекрёстка",
                    left_release_blocked_until, allow_zero=True)
    if type(prefilled_left_count) is not int or prefilled_left_count < 0:
        raise ValueError("Предзаполнение должно быть неотрицательным целым числом")
    if car_arrivals is not None:
        car_arrivals = list(car_arrivals)
        if any(not isinstance(arrival, CarArrival) for arrival in car_arrivals):
            raise ValueError("Автомобильное расписание должно содержать CarArrival(time, direction)")
        car_arrivals = sorted((arrival for arrival in car_arrivals if arrival.time < horizon),
                              key=lambda arrival: arrival.time)
    pedestrian_arrivals = prepare_arrivals(pedestrian_arrivals)
    if pedestrian_arrivals is not None:
        pedestrian_arrivals = [moment for moment in pedestrian_arrivals if moment < horizon]

    left = config.outgoing.get(Direction.LEFT)
    if left is None and (prefilled_left_count or left_release_blocked_until):
        raise ValueError("Для сценария затора нужен левый принимающий перекрёсток")
    closures = ({left.incoming.name: left_release_blocked_until} if left is not None else {})
    network = CoordinatedCorridor(seed, config, release_blocked_until=closures)
    if prefilled_left_count:
        network.prefill(network.central.outgoing[Direction.LEFT], prefilled_left_count,
                        entry_direction=Direction.LEFT)

    sources = [
        network.env.process(source(
            network.env, car_rate, horizon, Random(seed), car_arrivals,
            spawn=lambda arrival: network.spawn_car(
                arrival.direction if arrival is not None else None, at_stop=cars_at_crossing,
            ),
        )),
        network.env.process(source(
            network.env, pedestrian_rate, horizon, Random(seed + 1), pedestrian_arrivals,
            spawn=lambda _: network.spawn_pedestrian(),
        )),
    ]
    return network.run(sources)


def demo():
    return run(car_arrivals=[CarArrival(0, direction) for direction in Direction],
               pedestrian_arrivals=[], cars_at_crossing=True)


def spillback_demo():
    return run(car_arrivals=[CarArrival(0, direction) for direction in Direction],
               pedestrian_arrivals=[1], cars_at_crossing=True,
               prefilled_left_count=3, left_release_blocked_until=120)


if __name__ == "__main__":
    from ..common.reporting import display

    display(run(), show_log=True)
