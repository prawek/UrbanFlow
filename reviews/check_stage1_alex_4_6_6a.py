"""Run the reference suites and checks against the written exercise requirements.

From the repository root:
  .venv/Scripts/python.exe reviews/check_stage1_alex_4_6_6a.py
  .venv/Scripts/python.exe reviews/check_stage1_alex_4_6_6a.py --reference-only
  .venv/Scripts/python.exe reviews/check_stage1_alex_4_6_6a.py --implementation both

Requirement checks intentionally fail while the reviewed discrepancies remain.
The exercise files and the original tests are loaded without modification.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import sys
import types
import unittest

import simpy


ROOT = Path(__file__).resolve().parents[1]
EXERCISES = {
    "4": "04_signalized_crossing",
    "6": "06_downstream_spillback",
    "6a": "06a_sequential_segments",
}


def load_module(name: str, path: Path, *, is_package: bool = False):
    options = {"submodule_search_locations": [str(path.parent)]} if is_package else {}
    spec = importlib.util.spec_from_file_location(name, path, **options)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_implementation(implementation: str):
    modules = {}
    suite = unittest.TestSuite()
    for exercise, folder in EXERCISES.items():
        name = f"_review_{implementation}_{exercise}"
        path = (
            ROOT / "stage1_alex" / f"{exercise}.py"
            if implementation == "alex"
            else ROOT / "stage1-dao" / folder / "simulation.py"
        )
        modules[exercise] = load_module(name, path, is_package=True)
        tests_name = f"{name}.tests"
        tests_dir = ROOT / "stage1-dao" / folder / "tests"
        package = types.ModuleType(tests_name)
        package.__path__ = [str(tests_dir)]
        sys.modules[tests_name] = package
        original_tests = load_module(f"{tests_name}.test_simulation", tests_dir / "test_simulation.py")
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(original_tests))
    return modules, suite


def run_quietly(simulation):
    with redirect_stdout(io.StringIO()):
        return simulation.run()


def cars(module, journal):
    return [p for p in journal.participants if p.kind is module.Kind.CAR]


def requirement_checks(modules, implementation):
    class RequirementChecks(unittest.TestCase):
        def stop_line_scenario(self, car_count, pedestrian_arrivals):
            """Exercise 4's low-level API allows an initial stop-line queue."""
            module = modules["4"]
            env = simpy.Environment()
            car_queue, pedestrian_queue = simpy.Store(env), simpy.Store(env)
            controller = module.PhaseController(env)
            generated_cars = [module.Car(env, car_queue, i + 1, 0) for i in range(car_count)]
            generated_pedestrians = []
            processes = []

            def pedestrians_source():
                for index, arrival in enumerate(pedestrian_arrivals, start=1):
                    yield env.timeout(arrival - env.now)
                    participant = module.Pedestrian(env, pedestrian_queue, index, arrival)
                    generated_pedestrians.append(participant)
                    if index == 1:
                        env.process(participant.serve_crossing(controller, 7))
                    processes.append(env.process(participant.run(0)))

            with redirect_stdout(io.StringIO()):
                env.process(controller.run())
                env.process(generated_cars[0].serve_crossing(controller, 30))
                processes.extend(env.process(car.run(0)) for car in generated_cars)
                env.run(until=env.process(pedestrians_source()))
                env.run(until=simpy.AllOf(env, processes))
            return generated_cars, generated_pedestrians

        def test_task4_fixed_stopline_scenario(self):
            generated_cars, pedestrians = self.stop_line_scenario(3, [1, 1])
            self.assertEqual(
                [(c.crossing_started_at, c.crossing_finished_at) for c in generated_cars],
                [(0, 2), (2, 4), (4, 6)],
            )
            self.assertEqual(
                [(p.crossing_started_at, p.crossing_finished_at) for p in pedestrians],
                [(20, 25), (20, 25)],
            )

        def test_task4_full_green_services_ten_cars(self):
            generated_cars, _ = self.stop_line_scenario(12, [])
            self.assertEqual(
                [c.crossing_started_at for c in generated_cars],
                list(range(0, 20, 2)) + [30, 32],
            )
            self.assertEqual(sum(c.crossing_finished_at <= 20 for c in generated_cars), 10)

        def test_task4_schedule_respects_horizon(self):
            module = modules["4"]
            result = run_quietly(module.SignalizedCrossingSimulation(
                env=simpy.Environment(), car_arrivals=[599, 600, 601],
                pedestrian_arrivals=[599, 600, 601],
            ))
            self.assertEqual([p.appeared_at for p in result.participants if p.kind is module.Kind.CAR], [599])
            self.assertEqual([p.appeared_at for p in result.participants if p.kind is module.Kind.PEDESTRIAN], [599])

        def test_task6_arrival_must_fit_green(self):
            module = modules["6"]
            result = run_quietly(module.DownstreamSpillbackSimulation(
                env=simpy.Environment(), car_arrivals=[19], pedestrian_arrivals=[1],
                is_car_at_stop_line=True,
            ))
            self.assertEqual(cars(module, result)[0].crossing_started_at, 30)

        def test_task6_capacity_release_must_fit_green(self):
            module = modules["6"]
            result = run_quietly(module.DownstreamSpillbackSimulation(
                env=simpy.Environment(), car_arrivals=[0, 0], pedestrian_arrivals=[1],
                is_car_at_stop_line=True, downstream_capacity_count=1, downstream_length=255,
            ))
            self.assertEqual(cars(module, result)[1].crossing_started_at, 30)

        def test_task6_random_crossings_do_not_overlap(self):
            module = modules["6"]
            counts = {}
            for seed in (7, 42):
                result = run_quietly(module.DownstreamSpillbackSimulation(env=simpy.Environment(), seed=seed))
                pedestrians = [p for p in result.participants if p.kind is module.Kind.PEDESTRIAN]
                counts[seed] = sum(
                    c.crossing_started_at < p.crossing_finished_at
                    and p.crossing_started_at < c.crossing_finished_at
                    for c in cars(module, result) for p in pedestrians
                )
            self.assertEqual(counts, {7: 0, 42: 0}, "Counts are overlapping car/pedestrian pairs")

        def test_task6_tracks_current_segment(self):
            module = modules["6"]
            env = simpy.Environment()
            simulation = module.DownstreamSpillbackSimulation(
                env=env, car_arrivals=[0], pedestrian_arrivals=[], is_car_at_stop_line=True,
            )
            locations = []

            def location(car):
                segment = getattr(car, "current_segment", "<missing>")
                return getattr(segment, "name", segment)

            def observe():
                for time in (1, 3):
                    yield env.timeout(time - env.now)
                    locations.append(location(cars(module, simulation.journal)[0]))

            env.process(observe())
            result = run_quietly(simulation)
            locations.append(location(cars(module, result)[0]))
            self.assertEqual(locations, ["A", "B", None])

        def test_task6_schedule_respects_horizon(self):
            module = modules["6"]
            result = run_quietly(module.DownstreamSpillbackSimulation(
                env=simpy.Environment(), car_arrivals=[599, 600, 601],
                pedestrian_arrivals=[599, 600, 601],
            ))
            self.assertEqual([p.appeared_at for p in result.participants if p.kind is module.Kind.CAR], [599])
            self.assertEqual([p.appeared_at for p in result.participants if p.kind is module.Kind.PEDESTRIAN], [599])

    RequirementChecks.__name__ = f"RequirementChecks_{implementation}"
    RequirementChecks.__qualname__ = RequirementChecks.__name__
    return unittest.defaultTestLoader.loadTestsFromTestCase(RequirementChecks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--implementation", choices=("alex", "dao", "both"), default="alex")
    parser.add_argument("--reference-only", action="store_true")
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    suite = unittest.TestSuite()
    for implementation in (("alex", "dao") if args.implementation == "both" else (args.implementation,)):
        modules, reference_suite = load_implementation(implementation)
        suite.addTests(reference_suite)
        if not args.reference_only:
            suite.addTests(requirement_checks(modules, implementation))
    with redirect_stdout(io.StringIO()):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
