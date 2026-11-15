"""Запуск из корня проекта: python -m stage1_lev"""

import argparse

from .exercises import (
    exercise01, exercise02, exercise03, exercise04, exercise06, exercise06a, exercise07, exercise08,
)
from .common.reporting import display, export


def main():
    parser = argparse.ArgumentParser(description="Решения заданий по SimPy")
    parser.add_argument("--exercise", choices=("all", "1", "2", "3", "4", "6", "6a", "7", "8"), default="all")
    parser.add_argument("--seed", type=int, default=42, help="Seed случайных потоков")
    parser.add_argument("--log", action="store_true", help="Показать все события")
    parser.add_argument("--output", help="Каталог выгрузки CSV и JSON")
    parser.add_argument("--compare", action="store_true", help="Парные запуски 6 (выход B) или 8 (фазы I2)")
    parser.add_argument("--demo", action="store_true", help="Сценарий методички для 4, 6, 6a, 7 или 8")
    parser.add_argument("--spillback", action="store_true", help="Затор для 6a, 7 или 8")
    args = parser.parse_args()
    if args.compare:
        if args.exercise not in ("6", "8") or args.demo or args.spillback:
            parser.error("--compare используется отдельно с --exercise 6 или 8")
        if args.exercise == "6":
            scenarios, results = ("open", "blocked"), exercise06.compare(args.seed)
        else:
            scenarios, results = ("baseline", "long_main"), exercise08.compare(args.seed)
        for scenario, result in zip(scenarios, results):
            print(f"Scenario: {scenario}")
            display(result, args.log)
            if args.output:
                print(f"Saved: {export(result, args.output, scenario=scenario)}")
        if args.exercise == "8":
            for source in ("S1", "S3"):
                before, after = (result.summary()["sources"][source]["mean_delay"] for result in results)
                if before is not None and after is not None:
                    print(f"{source} mean_delay: {before:.6f} -> {after:.6f} s "
                          f"(change {after - before:+.6f} s)")
        return
    if args.spillback and (args.exercise not in ("6a", "7", "8") or args.demo):
        parser.error("--spillback используется отдельно с --exercise 6a, 7 или 8")
    if args.demo and args.exercise not in ("4", "6", "6a", "7", "8"):
        parser.error("--demo требует --exercise 4, 6, 6a, 7 или 8")
    if args.demo and args.exercise == "8":
        for result in exercise08.demo():
            scenario = result.participants[0].route.value
            print(f"Scenario: {scenario}")
            display(result, args.log)
            if args.output:
                print(f"Saved: {export(result, args.output, scenario=scenario)}")
        return
    runners = {"1": exercise01.run, "2": exercise02.run,
               "3": lambda: exercise03.run(args.seed),
               "4": lambda: exercise04.run(args.seed),
               "6": lambda: exercise06.run(args.seed),
               "6a": lambda: exercise06a.run(args.seed),
               "7": lambda: exercise07.run(args.seed),
               "8": lambda: exercise08.run(args.seed)}
    if args.demo:
        runners[args.exercise] = {"4": exercise04.demo, "6": exercise06.demo,
                                  "6a": exercise06a.demo, "7": exercise07.demo}[args.exercise]
    if args.spillback:
        runners[args.exercise] = {"6a": exercise06a.spillback_demo,
                                  "7": exercise07.spillback_demo,
                                  "8": lambda: exercise08.spillback_demo(args.seed)}[args.exercise]
    selected = runners if args.exercise == "all" else (args.exercise,)
    for number in selected:
        result = runners[number]()
        display(result, show_log=args.log or number in ("1", "2"))
        if args.output:
            print(f"Saved: {export(result, args.output)}")


if __name__ == "__main__":
    main()
