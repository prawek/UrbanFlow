"""Задание 6а: маршрут A → B → C с независимыми местными светофорами"""

from ..common.roads import SegmentConfig, simulate


SEGMENTS = (
    SegmentConfig("A", 150, 30, 15, green_duration=20, red_duration=10),
    SegmentConfig("B", 80, 30, 10, capacity=5, green_duration=27, red_duration=13),
    SegmentConfig("C", 120, 40, 10, capacity=3, green_duration=17, red_duration=19),
)


def run(seed=42, *, segments=SEGMENTS, horizon=600, car_rate=360, pedestrian_rate=180,
        car_arrivals=None, pedestrian_arrivals=None, cars_at_crossing=False,
        crossing_closed_until=None, observe_until=None):
    return simulate("6a", segments, seed, horizon=horizon, car_rate=car_rate,
                    pedestrian_rate=pedestrian_rate, car_arrivals=car_arrivals,
                    pedestrian_arrivals=pedestrian_arrivals, cars_at_crossing=cars_at_crossing,
                    crossing_closed_until=crossing_closed_until, observe_until=observe_until)


def demo():
    return run(car_arrivals=[0], pedestrian_arrivals={})


def spillback_demo():
    return run(car_arrivals=[0] * 9, cars_at_crossing=True,
               pedestrian_arrivals={"A": [1], "B": [1], "C": [1]},
               crossing_closed_until={"C": 180})


if __name__ == "__main__":
    from ..common.reporting import display

    display(run(), show_log=True)
