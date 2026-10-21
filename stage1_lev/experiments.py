"""График из задания 4: один автомобильный поток, разные пешеходные потоки"""

import argparse
import csv
from pathlib import Path
from random import Random

from . import exercise04
from .data import validate_number


def car_schedule(seed, rate, horizon):
    rng = Random(seed)
    moment = 0.0
    arrivals = []
    while True:
        moment += rng.expovariate(rate / 3600)
        if moment >= horizon:
            return arrivals
        arrivals.append(moment)


def study(seed=42, *, car_rate=360, horizon=600,
          pedestrian_rates=(0, 60, 180, 360, 720, 1440)):
    validate_number("Интенсивность автомобилей", car_rate)
    validate_number("Горизонт генерации", horizon)
    arrivals = car_schedule(seed, car_rate, horizon)
    if not arrivals:
        raise ValueError("Для графика нужен хотя бы один автомобиль: увеличьте горизонт или поток")
    rows = []
    for rate in pedestrian_rates:
        result = exercise04.run(seed, horizon=horizon, pedestrian_rate=rate,
                                car_arrivals=arrivals)
        rows.append({
            "exercise": "4", "seed": seed, "car_rate": car_rate, "horizon": horizon,
            "pedestrian_rate": rate, "car_count": len(arrivals),
            "mean_car_travel_time": result.summary()["car"]["mean_travel_time"],
        })
    return rows


def save_study(rows, output):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "pedestrian_rates.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    figure = Figure(figsize=(9, 5), layout="constrained")
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    for exercise in dict.fromkeys(row["exercise"] for row in rows):
        selected = [row for row in rows if row["exercise"] == exercise]
        axis.plot([row["pedestrian_rate"] for row in selected],
                  [row["mean_car_travel_time"] for row in selected],
                  marker="o", label=f"Задание {exercise}")
    axis.set_xlabel("Интенсивность пешеходов, чел./ч")
    axis.set_ylabel("Среднее время проезда автомобиля, с")
    axis.set_title(f"Одинаковый автомобильный поток: {rows[0]['car_rate']:g} авт./ч; "
                   f"seed={rows[0]['seed']}; горизонт {rows[0]['horizon']:g} с")
    axis.set_ylim(bottom=0)
    axis.grid(alpha=0.3)
    axis.legend()
    for extension in ("png", "svg"):
        figure.savefig(destination / f"pedestrian_rates.{extension}", dpi=160)
    return destination


def main():
    parser = argparse.ArgumentParser(description="Среднее время проезда при разных потоках пешеходов")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--car-rate", type=float, default=360)
    parser.add_argument("--horizon", type=float, default=600)
    parser.add_argument("--pedestrian-rates", type=float, nargs="+", default=[0, 60, 180, 360, 720, 1440])
    parser.add_argument("--output", default="stage1_lev/results/experiments")
    args = parser.parse_args()
    rows = study(args.seed, car_rate=args.car_rate, horizon=args.horizon,
                 pedestrian_rates=args.pedestrian_rates)
    print(f"Saved: {save_study(rows, args.output)}")


if __name__ == "__main__":
    main()
