"""Задание 6: заполненный участок B останавливает выпуск с A"""

from ..common.roads import SegmentConfig, simulate


SEGMENTS = (
    SegmentConfig("A", 150, 30, 15),
    SegmentConfig("B", 150, 0, 15, capacity=5),
)


def run(seed=42, *, exit_open_at=0, horizon=600, car_rate=360, pedestrian_rate=180,
        car_arrivals=None, pedestrian_arrivals=None, cars_at_crossing=False):
    local_arrivals = None if pedestrian_arrivals is None else {"A": pedestrian_arrivals}
    return simulate(6, SEGMENTS, seed, horizon=horizon, car_rate=car_rate,
                    pedestrian_rate=pedestrian_rate, car_arrivals=car_arrivals,
                    pedestrian_arrivals=local_arrivals, cars_at_crossing=cars_at_crossing,
                    exit_closed_until={"B": exit_open_at})


def compare(seed=42):
    """Парные запуски: отдельные RNG сохраняют входные потоки при смене выхода"""
    return run(seed), run(seed, exit_open_at=60)


def demo():
    return run(exit_open_at=60, car_arrivals=[0] * 8, pedestrian_arrivals=[1, 1],
               cars_at_crossing=True)


if __name__ == "__main__":
    from ..common.reporting import display

    display(run(), show_log=True)
