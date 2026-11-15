"""Проверки дорожной схемы задания 8, спроса, резервов и локальных сигналов."""

import csv
import io
import json
import random
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stage1_lev import __main__
from stage1_lev.common.data import Route
from stage1_lev.common.network_config import RouteArrival
from stage1_lev.common.reporting import export
from stage1_lev.common.road_network import TrafficNetwork
from stage1_lev.exercises import exercise08


def itinerary(result, car):
    return sorted((visit for visit in result.visits if visit.participant_id == car.id),
                  key=lambda visit: visit.entered_at)


def demand(result):
    return [(car.id, car.source, car.route, car.appeared_at) for car in result.participants]


def with_capacity(config, name, capacity):
    return replace(config, segments=tuple(replace(link, capacity=capacity) if link.name == name
                                          else link for link in config.segments))


class NetworkTests(unittest.TestCase):
    def assert_safe_and_finished(self, result, config=exercise08.CONFIG):
        self.assertTrue(all(car.arrived_at is not None for car in result.participants))
        self.assertTrue(all(car.current_segment is None and car.reserved_segment is None
                            for car in result.participants))
        self.assertEqual(len({car.id for car in result.participants}), len(result.participants))
        self.assertEqual([event.time for event in result.events], sorted(event.time for event in result.events))
        links = {link.name: link for link in config.segments}
        passages = {passage.segment: (junction, approach, passage)
                    for junction in config.intersections
                    for approach, passage in junction.approaches.items()}
        routes = {route.name: route for route in config.routes}
        for car in result.participants:
            visits = itinerary(result, car)
            route = routes[car.route]
            self.assertEqual([visit.segment for visit in visits], list(route.segments))
            self.assertEqual(car.exit, route.exit)
            self.assertEqual(car.admitted_at, visits[0].entered_at)
            self.assertEqual(car.arrived_at, visits[-1].exited_at)
            self.assertAlmostEqual(car.travel_time, car.free_flow_time + car.queue_wait + car.external_wait)
            self.assertAlmostEqual(car.delay, car.queue_wait + car.external_wait)
            self.assertGreaterEqual(car.external_wait, 0)
            for index, visit in enumerate(visits):
                junction, approach, passage = passages[visit.segment]
                link = links[visit.segment]
                self.assertEqual((visit.intersection, visit.approach), (junction.name, approach))
                self.assertAlmostEqual(visit.queue_entered_at - visit.entered_at,
                                       link.approach_length / link.speed)
                self.assertAlmostEqual(visit.crossing_finished_at - visit.crossing_started_at,
                                       passage.length / passage.speed)
                self.assertGreaterEqual(visit.crossing_started_at, visit.queue_entered_at)
                self.assertEqual(visit.exited_at, visit.crossing_finished_at)
                cycle = sum(duration for _, duration in junction.phases)
                offset = sum(duration for _, duration in junction.phases[:junction.initial_phase])
                moment = (visit.crossing_started_at + offset + junction.initial_elapsed) % cycle
                start = 0
                for name, duration in junction.phases:
                    if name == approach:
                        self.assertGreaterEqual(moment + 1e-9, start)
                        self.assertLessEqual(moment + passage.travel_time, start + duration + 1e-9)
                    start += duration
                if index:
                    self.assertEqual(visit.reserved_at, visits[index - 1].crossing_started_at)
                    self.assertEqual(visit.entered_at, visits[index - 1].crossing_finished_at)

        for link in config.segments:
            visits = [visit for visit in result.visits if visit.segment == link.name]
            self.assertEqual(
                [visit.participant_id for visit in sorted(visits, key=lambda visit: visit.queue_entered_at)],
                [visit.participant_id for visit in sorted(visits, key=lambda visit: visit.crossing_started_at)],
            )
            # Восстанавливаем занятость по интервалам жизни мест, независимо
            # от счётчиков monitoring. Резерв и машина занимают одно место.
            changes = []
            for visit in visits:
                start = visit.reserved_at if visit.reserved_at is not None else visit.entered_at
                changes.extend(((start, 1), (visit.exited_at, -1)))
            occupied = 0
            for _, change in sorted(changes):
                occupied += change
                self.assertGreaterEqual(occupied, 0)
                self.assertLessEqual(occupied, link.capacity)
            self.assertEqual(occupied, 0)
            state = result.final_segments[link.name]
            self.assertEqual(state["free_count"], link.capacity)
            for key in ("occupied_count", "reserved_count", "car_queue_len", "pending_requests"):
                self.assertEqual(state[key], 0)

        for junction in config.intersections:
            visits = sorted((visit for visit in result.visits if visit.intersection == junction.name),
                            key=lambda visit: visit.crossing_started_at)
            for previous, current in zip(visits, visits[1:]):
                self.assertLessEqual(previous.crossing_finished_at, current.crossing_started_at)
        for row in result.monitoring:
            self.assertGreaterEqual(min(row.occupied_count, row.reserved_count, row.free_count), 0)
            self.assertEqual(row.occupied_count + row.reserved_count + row.free_count, row.capacity)
        for state in result.states:
            self.assertEqual(state.created, state.arrived + state.in_network + state.external_waiting)
            self.assertGreaterEqual(min(state.arrived, state.in_network, state.external_waiting), 0)
        self.assertEqual(result.states[-1].in_network, 0)
        self.assertEqual(result.states[-1].external_waiting, 0)
        self.assertTrue(all(count == 0 for count in result.final_external_queues.values()))
        created, exited = set(), set()
        for event in result.events:
            if event.action == "appeared":
                self.assertNotIn(event.actor, created)
                created.add(event.actor)
            if event.action == "arrived":
                self.assertIn(event.actor, created - exited)
                exited.add(event.actor)
        self.assertEqual(created, exited)
        for counts in result.summary()["sources"].values():
            self.assertEqual(counts["created"], counts["arrived"])
        for source in config.sources:
            members = [car for car in result.participants if car.source == source.name]
            self.assertEqual([car.id for car in members],
                             [car.id for car in sorted(members, key=lambda car: car.admitted_at)])

    def test_six_routes_in_separate_runs_have_exact_times(self):
        expected = (
            (["I1", "I2", "I3"], [10, 22, 54], [10, 40, 60], 62, 38, "east_I3"),
            (["I1", "I2"], [10, 22], [10, 40], 44, 26, "north_I2"),
            (["I1", "I2", "I3"], [8, 32, 54], [20, 40, 60], 62, 36, "east_I3"),
            (["I2", "I3"], [6, 38], [25, 38], 40, 21, "east_I3"),
            (["I2", "I1"], [6, 38], [25, 38], 40, 21, "west_I1"),
            (["I3"], [7], [18], 20, 9, "east_I3"),
        )
        for result, values in zip(exercise08.demo(), expected):
            car = result.participants[0]
            visits = itinerary(result, car)
            with self.subTest(route=car.route):
                intersections, stops, starts, exit_time, free_time, exit_name = values
                self.assertEqual([visit.intersection for visit in visits], intersections)
                self.assertEqual([visit.queue_entered_at for visit in visits], stops)
                self.assertEqual([visit.crossing_started_at for visit in visits], starts)
                self.assertEqual((car.arrived_at, car.free_flow_time, car.exit), (exit_time, free_time, exit_name))
                self.assert_safe_and_finished(result)

    def test_free_motion_has_no_double_counting_of_intersection_lengths(self):
        for route in exercise08.CONFIG.routes:
            phases = {"I1": "east" if route.name == Route.S3_TO_I1 else
                      "north" if route.source == "S2" else "west",
                      "I2": "north" if route.source == "S3" else "west",
                      "I3": "north" if route.source == "S4" else "west"}
            config = replace(exercise08.CONFIG, horizon=1, intersections=tuple(
                replace(junction, phases=tuple((name, 1000) for name, _ in junction.phases),
                        initial_phase=[name for name, _ in junction.phases].index(phases[junction.name]))
                for junction in exercise08.CONFIG.intersections
            ))
            with self.subTest(route=route.name):
                result = exercise08.run(config=config, car_arrivals=[RouteArrival(0, route.name)])
                car = result.participants[0]
                self.assertEqual(car.travel_time, car.free_flow_time)
                self.assertEqual(car.delay, 0)
                self.assert_safe_and_finished(result, config)

    def test_each_source_and_probability_endpoints(self):
        scenarios = (("S1", {"p1_left": 0}, Route.S1_STRAIGHT),
                     ("S1", {"p1_left": 1}, Route.S1_LEFT),
                     ("S2", {}, Route.S2_TO_I3), ("S3", {"p3_to_I3": 1}, Route.S3_TO_I3),
                     ("S3", {"p3_to_I3": 0}, Route.S3_TO_I1), ("S4", {}, Route.S4_TO_EXIT))
        for source, probabilities, route in scenarios:
            rates = {name: 360 if name == source else 0 for name in ("S1", "S2", "S3", "S4")}
            config = exercise08.configuration(**rates, **probabilities, horizon=120)
            with self.subTest(source=source, route=route):
                result = exercise08.run(config=config)
                self.assertTrue(result.participants)
                self.assertEqual({car.route for car in result.participants}, {route})
                self.assert_safe_and_finished(result, config)
                if route in (Route.S1_LEFT, Route.S3_TO_I1, Route.S4_TO_EXIT):
                    self.assertEqual(result.summary()["segment_entries"]["I2_I3"], 0)

    def test_zero_sources_and_explicit_empty_schedule(self):
        for config, arrivals in ((exercise08.configuration(S1=0, S2=0, S3=0, S4=0), None),
                                  (exercise08.CONFIG, [])):
            result = exercise08.run(config=config, car_arrivals=arrivals)
            self.assertEqual(result.participants, [])
            self.assertEqual(result.finished_at, 600)
            self.assert_safe_and_finished(result, config)

    def test_shared_fifo_conflicts_and_merges(self):
        arrivals = [RouteArrival(time, route) for time in (0, 1, 2, 3) for route in Route]
        result = exercise08.run(car_arrivals=arrivals)
        self.assert_safe_and_finished(result)
        for link in ("I1_I2", "I2_I3"):
            transfers = [(itinerary(result, car)[index - 1].crossing_finished_at, car.id)
                         for car in result.participants for index, visit in enumerate(itinerary(result, car))
                         if visit.segment == link]
            entries = [(visit.entered_at, visit.participant_id)
                       for visit in result.visits if visit.segment == link]
            self.assertEqual(sorted(transfers), sorted(entries))

    def test_turn_with_free_exit_cannot_overtake_blocked_head_of_approach(self):
        config = with_capacity(exercise08.CONFIG, "I2_I3", 1)
        result = exercise08.run(config=config, release_blocked_until={"I3": 120}, car_arrivals=[
            RouteArrival(0, Route.S1_STRAIGHT), RouteArrival(1, Route.S1_STRAIGHT),
            RouteArrival(2, Route.S1_LEFT), RouteArrival(0, Route.S3_TO_I1),
        ])
        s1 = [car for car in result.participants if car.source == "S1"]
        first, blocked, left = (itinerary(result, car)[1] for car in s1)
        self.assertEqual(first.crossing_started_at, 40)
        self.assertEqual(blocked.crossing_started_at, 122)
        self.assertEqual(left.crossing_started_at, 126)
        s3 = next(car for car in result.participants if car.source == "S3")
        self.assertEqual(itinerary(result, s3)[0].crossing_started_at, 25)
        self.assert_safe_and_finished(result, config)

    def test_closure_fills_upstream_segments_and_external_queue_then_drains(self):
        config = replace(exercise08.CONFIG, horizon=1)
        network = TrafficNetwork(42, config, release_blocked_until={"I3": 240})
        for _ in range(30):
            network.spawn_car("S1", Route.S1_STRAIGHT)
        network.env.run(until=200)
        for name in ("I2_I3", "I1_I2", "S1_I1"):
            road = network.segments[name]
            self.assertEqual(road.free_slots.level, 0)
            self.assertEqual(len(road.occupied), road.capacity)
        self.assertEqual(len(network.external_queues["S1"].items), 15)
        self.assertTrue(all(intersection.crossing.count == 0
                            for intersection in network.intersections.values()))
        self.assertFalse(any(visit.crossing_started_at is not None and visit.intersection == "I3"
                             for visit in network.result.visits))
        self.assertGreater(sum(event.kind == "signal" and event.actor == "traffic_light:I3"
                               for event in network.result.events), 10)
        result = network.run([])
        self.assertGreater(result.finished_at, 240)
        self.assertTrue(any(car.external_wait > 200 for car in result.participants))
        self.assert_safe_and_finished(result, config)

    def test_reservation_rechecks_green_at_last_start_and_phase_boundary(self):
        config = with_capacity(exercise08.CONFIG, "I2_I3", 1)
        config = replace(config, intersections=tuple(
            replace(junction, phases=(("west", 100), ("north", 10))) if junction.name == "I3"
            else junction for junction in config.intersections
        ))
        for opening, expected in ((59, 61), (60, 80), (63, 80)):
            with self.subTest(opening=opening):
                result = exercise08.run(config=config, release_blocked_until={"I3": opening},
                                        car_arrivals=[RouteArrival(0, Route.S1_STRAIGHT),
                                                      RouteArrival(1, Route.S1_STRAIGHT)])
                second = itinerary(result, result.participants[1])
                self.assertEqual(second[1].crossing_started_at, expected)
                self.assertEqual(second[2].reserved_at, expected)
                self.assert_safe_and_finished(result, config)

    def test_capacity_release_on_red_does_not_reserve_until_next_green(self):
        config = with_capacity(exercise08.CONFIG, "I2_I3", 1)
        result = exercise08.run(config=config, release_blocked_until={"I3": 106},
                                car_arrivals=[RouteArrival(0, Route.S1_STRAIGHT),
                                              RouteArrival(1, Route.S1_STRAIGHT)])
        second = itinerary(result, result.participants[1])
        self.assertEqual(second[1].crossing_started_at, 120)
        self.assertEqual(second[2].reserved_at, 120)
        self.assert_safe_and_finished(result, config)

    def test_initial_phase_and_elapsed_time_are_local(self):
        config = replace(exercise08.CONFIG, horizon=120, intersections=(
            replace(exercise08.CONFIG.intersections[0], initial_phase=1, initial_elapsed=7),
            *exercise08.CONFIG.intersections[1:],
        ))
        baseline = exercise08.run(config=replace(exercise08.CONFIG, horizon=120), car_arrivals=[])
        result = exercise08.run(config=config, car_arrivals=[RouteArrival(0, Route.S1_LEFT)])
        switches = [(event.time, event.action) for event in result.events if event.actor == "traffic_light:I1"]
        self.assertEqual(switches[:4], [(0, "north"), (3, "east"), (13, "west"), (33, "north")])
        for name in ("I2", "I3"):
            self.assertEqual([event for event in baseline.events if event.actor == f"traffic_light:{name}"],
                             [event for event in result.events if event.actor == f"traffic_light:{name}"])
        self.assert_safe_and_finished(result, config)

    def test_generation_clocks_are_independent_and_not_slowed_by_spillback(self):
        baseline = exercise08.run()
        blocked = exercise08.run(release_blocked_until={"I3": 800})
        self.assertEqual(demand(baseline), demand(blocked))
        self.assertTrue(any(car.admitted_at > 600 for car in blocked.participants))
        for source in exercise08.CONFIG.sources:
            rng = random.Random(42 + source.seed)
            expected = []
            moment = rng.expovariate(source.rate / 3600)
            while moment < 600:
                expected.append(moment)
                moment += rng.expovariate(source.rate / 3600)
            actual = [car.appeared_at for car in baseline.participants if car.source == source.name]
            self.assertEqual(actual, expected)
            self.assertGreater(actual[0], 0)
        self.assert_safe_and_finished(blocked)

    def test_reproducibility_and_paired_experiments(self):
        state = random.getstate()
        baseline, changed = exercise08.compare()
        self.assertEqual(baseline, exercise08.run())
        self.assertEqual(demand(baseline), demand(changed))
        self.assertNotEqual(demand(baseline), demand(exercise08.run(43)))
        self.assertEqual(random.getstate(), state)
        first, second = (result.parameters[0] for result in (baseline, changed))
        for key in ("sources", "segments", "routes", "route_seed", "horizon"):
            self.assertEqual(first[key], second[key])
        self.assertEqual(first["intersections"][0], second["intersections"][0])
        self.assertEqual(first["intersections"][2], second["intersections"][2])
        self.assertEqual(first["intersections"][1]["phases"], [["west", 25], ["north", 15]])
        self.assertEqual(second["intersections"][1]["phases"], [["west", 35], ["north", 5]])
        self.assertLess(changed.summary()["sources"]["S1"]["mean_delay"],
                        baseline.summary()["sources"]["S1"]["mean_delay"])
        self.assertGreater(changed.summary()["sources"]["S3"]["mean_delay"],
                           baseline.summary()["sources"]["S3"]["mean_delay"])
        for result in (baseline, changed):
            for source in ("S1", "S2", "S3", "S4"):
                members = [car for car in result.participants if car.source == source]
                self.assertEqual(result.summary()["sources"][source]["mean_travel_time"],
                                 sum(car.arrived_at - car.appeared_at for car in members) / len(members))

    def test_route_and_segment_count_identities(self):
        result = exercise08.run()
        summary = result.summary()
        n1, n2, n3, n4 = (summary["sources"][source]["created"] for source in ("S1", "S2", "S3", "S4"))
        counts = summary["routes"]
        self.assertEqual(n1, counts["S1_straight"] + counts["S1_left"])
        self.assertEqual(n3, counts["S3_to_I3"] + counts["S3_to_I1"])
        self.assertEqual(summary["intersection_passages"], {
            "I1": n1 + n2 + counts["S3_to_I1"], "I2": n1 + n2 + n3,
            "I3": counts["S1_straight"] + n2 + counts["S3_to_I3"] + n4,
        })
        self.assertEqual(summary["segment_entries"]["I2_I3"],
                         counts["S1_straight"] + n2 + counts["S3_to_I3"])
        self.assert_safe_and_finished(result)

    def test_explicit_schedule_uses_routes_even_with_zero_probability_and_excludes_T(self):
        config = exercise08.configuration(p1_left=0, horizon=30)
        result = exercise08.run(config=config, car_arrivals=[RouteArrival(30, Route.S1_LEFT),
                                                            RouteArrival(29, Route.S1_LEFT)])
        self.assertEqual(len(result.participants), 1)
        self.assertEqual(result.participants[0].appeared_at, 29)
        self.assertEqual(result.participants[0].route, Route.S1_LEFT)
        self.assertGreater(result.finished_at, 30)
        self.assert_safe_and_finished(result, config)

    def test_names_do_not_determine_service_or_statistics(self):
        config = exercise08.CONFIG
        links = {link.name: f"road-{index}" for index, link in enumerate(config.segments)}
        junctions = {junction.name: f"junction-{index}" for index, junction in enumerate(config.intersections)}
        renamed = replace(config,
            segments=tuple(replace(link, name=links[link.name], upstream=junctions.get(link.upstream))
                           for link in config.segments),
            intersections=tuple(replace(junction, name=junctions[junction.name], approaches={
                name: replace(passage, segment=links[passage.segment])
                for name, passage in junction.approaches.items()}) for junction in config.intersections),
            sources=tuple(replace(source, segment=links[source.segment]) for source in config.sources),
            routes=tuple(replace(route, segments=tuple(links[name] for name in route.segments))
                         for route in config.routes),
        )
        original, result = exercise08.run(), exercise08.run(config=renamed)
        self.assertEqual(original.participants, result.participants)
        self.assertEqual(original.summary()["sources"], result.summary()["sources"])
        self.assert_safe_and_finished(result, renamed)

    def test_export_keeps_times_sources_routes_and_flat_tables(self):
        result = exercise08.run()
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            path = export(result, temporary)
            self.assertEqual(json.loads((path / "summary.json").read_text(encoding="utf-8")), result.summary())
            for filename, count in (("participants.csv", len(result.participants)),
                                     ("visits.csv", len(result.visits)), ("states.csv", len(result.states)),
                                     ("monitoring.csv", len(result.monitoring))):
                with (path / filename).open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    self.assertFalse({"current_segment", "reserved_segment", "path", "path_index"}
                                     & set(reader.fieldnames))
                    rows = list(reader)
                    self.assertEqual(len(rows), count)
                    if filename == "participants.csv":
                        for row, car in zip(rows, result.participants):
                            self.assertEqual(row["route"], car.route.value)
                            for name in ("appeared_at", "admitted_at", "arrived_at"):
                                self.assertEqual(float(row[name]), getattr(car, name))

    def test_cli_comparison_and_six_separate_demo_exports(self):
        with TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temporary:
            for option in ("--compare", "--demo", "--spillback"):
                with self.subTest(option=option), patch("sys.argv", ["stage1_lev", "--exercise", "8",
                                                                    option, "--output", temporary]), \
                        patch("sys.stdout", new_callable=io.StringIO) as output:
                    __main__.main()
                    if option == "--compare":
                        self.assertIn("S1 mean_delay:", output.getvalue())
                        self.assertIn("S3 mean_delay:", output.getvalue())
            for label in ("baseline", "long_main", *(route.value for route in Route)):
                self.assertTrue((Path(temporary) / "exercise08" / label / "summary.json").exists())

    def test_invalid_parameters_configs_and_schedules(self):
        for kwargs in ({"S1": -1}, {"S2": float("nan")}, {"S3": float("inf")}, {"horizon": 0},
                       {"p1_left": -0.1}, {"p1_left": 1.1}, {"p3_to_I3": float("nan")},
                       {"p3_to_I3": 1.01}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                exercise08.run(config=exercise08.configuration(**kwargs), car_arrivals=[])
        config = exercise08.CONFIG
        for bad in (
            with_capacity(config, "I2_I3", 0), with_capacity(config, "S1_I1", 1.5),
            replace(config, route_seed=0), replace(config, segments=(*config.segments, config.segments[0])),
            replace(config, intersections=(replace(config.intersections[0], initial_phase=3), *config.intersections[1:])),
            replace(config, intersections=(replace(config.intersections[0], initial_elapsed=20), *config.intersections[1:])),
            replace(config, intersections=(replace(config.intersections[0], phases=(("west", 1), ("north", 10), ("east", 10))),
                                           *config.intersections[1:])),
            replace(config, routes=(replace(config.routes[0], segments=("S1_I1", "I2_I3")), *config.routes[1:])),
            replace(config, routes=(replace(config.routes[0], exit="north_I2"), *config.routes[1:])),
        ):
            with self.subTest(config=bad), self.assertRaises(ValueError):
                exercise08.run(config=bad, car_arrivals=[])
        for kwargs in ({"release_blocked_until": {"unknown": 10}},
                       {"release_blocked_until": {"I3": float("nan")}}, {"car_arrivals": [0]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                exercise08.run(**kwargs)
        for time, route in ((-1, Route.S1_LEFT), (float("inf"), Route.S1_LEFT), (0, "S1_left")):
            with self.subTest(time=time, route=route), self.assertRaises(ValueError):
                RouteArrival(time, route)


if __name__ == "__main__":
    unittest.main()
