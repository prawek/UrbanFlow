"""Задание 7: три направления и согласованные светофоры принимающих участков"""

from ..common.corridor import CarArrival, Direction, simulate


def run(seed=42, *, horizon=600, car_rate=360, pedestrian_rate=180,
        car_arrivals=None, pedestrian_arrivals=None, cars_at_crossing=False,
        prefilled_left_count=0, left_release_blocked_until=0):
    return simulate(seed, horizon=horizon, car_rate=car_rate, pedestrian_rate=pedestrian_rate,
                    car_arrivals=car_arrivals, pedestrian_arrivals=pedestrian_arrivals,
                    cars_at_crossing=cars_at_crossing, prefilled_left_count=prefilled_left_count,
                    left_release_blocked_until=left_release_blocked_until)


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
