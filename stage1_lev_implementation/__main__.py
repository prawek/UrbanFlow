"""Запуск из корня проекта: python -m stage1_lev_implementation."""

import argparse

from . import exercise01, exercise02, exercise03
from .reporting import display, export


def main():
    parser = argparse.ArgumentParser(description="Решения заданий 1–3 по SimPy")
    parser.add_argument("--exercise", choices=("all", "1", "2", "3"), default="all")
    parser.add_argument("--seed", type=int, default=42, help="Seed потоков задания 3")
    parser.add_argument("--log", action="store_true", help="Показать все события")
    parser.add_argument("--output", help="Каталог выгрузки CSV и JSON")
    args = parser.parse_args()
    runners = {"1": exercise01.run, "2": exercise02.run,
               "3": lambda: exercise03.run(args.seed)}
    selected = runners if args.exercise == "all" else (args.exercise,)
    for number in selected:
        result = runners[number]()
        display(result, show_log=args.log or number in ("1", "2"))
        if args.output:
            print(f"Saved: {export(result, args.output)}")


if __name__ == "__main__":
    main()
