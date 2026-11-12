"""Standalone SimPy model. All classes are defined in this file.

Time is measured in seconds; capacity tokens represent free road spaces.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from typing import Optional

import simpy


@dataclass
class Person:
    id: str
    kind: str
    created_at: float
    segment: str
    queue_entered_at: Optional[float] = None
    queue_left_at: Optional[float] = None
    finished_at: Optional[float] = None
    crossing_start: Optional[float] = None
    crossing_end: Optional[float] = None
    visits: list = field(default_factory=list)
    done: object = None

    @property
    def trip_time(self):
        return None if self.finished_at is None else self.finished_at - self.created_at


@dataclass(frozen=True)
class Parameters:
    name: str
    approach_length: float
    crossing_length: float
    speed: float
    capacity: Optional[int]
    green: float
    red: float

    def __post_init__(self):
        if min(self.approach_length, self.crossing_length, self.speed, self.green, self.red) <= 0:
            raise ValueError('Lengths, speed and phase durations must be positive')
        if self.capacity is not None and self.capacity <= 0:
            raise ValueError('Capacity must be positive')
        if self.crossing_length / self.speed > self.green or self.red < 5:
            raise ValueError('A phase must fit one full crossing')


class Road:
    def __init__(self, env, parameters):
        self.env = env
        self.p = parameters
        self.car_queue = simpy.Store(env)
        self.ped_queue = simpy.Store(env)
        self.free = (simpy.Container(env, capacity=parameters.capacity, init=parameters.capacity)
                     if parameters.capacity is not None else None)
        self.green = True
        self.phase_end = parameters.green
        self.phase_change = env.event()
        self.phase_log = [(0.0, 'cars')]
        self.occupancy_log = [(0.0, 0)]
        self.next = None

    def permission(self):
        return self.green and self.env.now + self.p.crossing_length / self.p.speed <= self.phase_end + 1e-8

    def phase_process(self):
        while True:
            yield self.env.timeout(self.p.green if self.green else self.p.red)
            self.green = not self.green
            self.phase_end = self.env.now + (self.p.green if self.green else self.p.red)
            self.phase_log.append((float(self.env.now), 'cars' if self.green else 'pedestrians'))
            previous = self.phase_change
            self.phase_change = self.env.event()
            previous.succeed()

    def record_occupancy(self):
        if self.free is not None:
            self.occupancy_log.append((float(self.env.now), self.p.capacity - self.free.level))


class Simulation:
    def __init__(self, task='4', *, seed=42, horizon=600, car_arrivals=None,
                 pedestrian_arrivals=None, cars_at_stop_line=False,
                 exit_blocked_until=0, block_c_until=0, overrides=None,
                 car_rate=360, pedestrian_rate=180, verbose=False):
        if task not in ('4', '6', '6a'):
            raise ValueError('task must be 4, 6 or 6a')
        self.task = task
        self.env = simpy.Environment()
        self.seed, self.horizon = seed, horizon
        self.car_arrivals = None if car_arrivals is None else tuple(car_arrivals)
        self.pedestrian_arrivals = pedestrian_arrivals
        self.cars_at_stop_line = cars_at_stop_line
        self.exit_blocked_until = exit_blocked_until
        self.block_c_until = block_c_until
        self.car_rate, self.pedestrian_rate = car_rate, pedestrian_rate
        self.verbose = verbose
        self.people = []
        self.jobs = []
        self.sources = []
        self.errors = []
        defaults = {
            'A': Parameters('A', 150, 30, 15, None, 20, 10),
            'B': Parameters('B', 80, 30, 10, 5, 27, 13),
            'C': Parameters('C', 120, 40, 10, 3, 17, 19),
        }
        for name, parameter in (overrides or {}).items():
            defaults[name] = parameter
        names = ('A',) if task == '4' else (('A','B') if task == '6' else ('A','B','C'))
        self.roads = {name: Road(self.env, defaults[name]) for name in names}
        for left, right in zip(names, names[1:]):
            self.roads[left].next = self.roads[right]
        # Exercise 6: B is a capacity-constrained exit road, not an intersection.
        self.signal_names = ('A',) if task in ('4','6') else names
        self.events = []

    def log(self, person, label, road):
        entry = (float(self.env.now), person.id, road, label)
        self.events.append(entry)
        if self.verbose:
            print(f'{entry[0]:8.3f} {entry[1]:16s} {road} {label}')

    def spawn(self, kind, road_name='A'):
        count = sum(p.kind == kind and (kind == 'car' or p.segment == road_name) for p in self.people) + 1
        pid = f'{kind}-{road_name}-{count:03d}'
        p = Person(pid, kind, float(self.env.now), road_name, done=self.env.event())
        self.people.append(p)
        self.log(p, 'created', road_name)
        if kind == 'car':
            self.jobs.append(self.env.process(self.car_process(p)))
        else:
            self.jobs.append(self.env.process(self.ped_process(p, self.roads[road_name])))

    def source(self, kind, road, rate, seed, arrivals):
        if arrivals is not None:
            for t in sorted(arrivals):
                if t < 0:
                    raise ValueError('Arrival times must be nonnegative')
                if t < self.env.now:
                    raise ValueError('Arrival time is in past')
                yield self.env.timeout(t - self.env.now)
                self.spawn(kind, road)
            return
        rng = random.Random(seed)
        if rate <= 0:
            return
        while True:
            dt = rng.expovariate(rate / 3600)
            if self.env.now + dt >= self.horizon:
                yield self.env.timeout(max(0, self.horizon - self.env.now))
                return
            yield self.env.timeout(dt)
            self.spawn(kind, road)

    def ped_process(self, p, road):
        p.queue_entered_at = float(self.env.now)
        yield road.ped_queue.put(p)
        self.log(p, 'ped_queued', road.p.name)
        yield p.done
        p.finished_at = float(self.env.now)
        self.log(p, 'finished', road.p.name)

    def ped_server(self, road):
        # Snapshot the FIFO queue strictly before the red phase starts.
        while True:
            event = road.phase_change
            yield event
            if road.green:
                continue
            eligible = sum(p.queue_entered_at < self.env.now - 1e-9 for p in road.ped_queue.items)
            group = []
            for _ in range(eligible):
                p = yield road.ped_queue.get()
                p.queue_left_at = float(self.env.now)
                p.crossing_start = float(self.env.now)
                self.log(p, 'ped_cross_start', road.p.name)
                group.append(p)
            if group:
                yield self.env.timeout(5)
                for p in group:
                    p.crossing_end = float(self.env.now)
                    self.log(p, 'ped_cross_end', road.p.name)
                    p.done.succeed()

    def car_process(self, p):
        current = self.roads['A']
        if current.free is not None:
            yield current.free.get(1)
            current.record_occupancy()
        while True:
            p.segment = current.p.name
            p.visits.append({'segment': current.p.name, 'entered': float(self.env.now), 'queued': None,
                             'cross_start': None, 'cross_end': None})
            visit = p.visits[-1]
            travel = 0 if (current.p.name == 'A' and self.cars_at_stop_line) else current.p.approach_length / current.p.speed
            yield self.env.timeout(travel)
            p.queue_entered_at = float(self.env.now)
            visit['queued'] = float(self.env.now)
            self.log(p, 'car_queued', current.p.name)
            exit_event = self.env.event()
            yield current.car_queue.put((p, exit_event))
            yield exit_event
            if current.next is None:
                if current.free is not None:
                    yield current.free.put(1)
                    current.record_occupancy()
                p.finished_at = float(self.env.now)
                self.log(p, 'finished', current.p.name)
                return
            current = current.next
            if self.task == '6' and current.p.name == 'B':
                # The B segment has a fixed 10-second exit travel time.
                yield self.env.timeout(10)
                if self.env.now < self.exit_blocked_until:
                    yield self.env.timeout(self.exit_blocked_until - self.env.now)
                yield current.free.put(1)
                current.record_occupancy()
                p.finished_at = float(self.env.now)
                self.log(p, 'finished', 'B')
                return

    def car_server(self, road):
        while True:
            p, depart = yield road.car_queue.get()
            next_road = road.next
            while True:
                if road.p.name == 'C' and self.env.now < self.block_c_until:
                    yield self.env.timeout(self.block_c_until - self.env.now)
                    continue
                if not road.permission():
                    yield road.phase_change
                    continue
                if next_road is None or next_road.free is None:
                    break
                # The request may remain pending while the green phase ends.
                reservation = next_road.free.get(1)
                change = road.phase_change
                yield reservation | change
                if not reservation.triggered:
                    reservation.cancel()
                    continue
                if not road.permission() or (road.p.name == 'C' and self.env.now < self.block_c_until):
                    yield next_road.free.put(1)
                    next_road.record_occupancy()
                    continue
                next_road.record_occupancy()
                break
            p.queue_left_at = float(self.env.now)
            p.crossing_start = float(self.env.now)
            p.visits[-1]['cross_start'] = float(self.env.now)
            self.log(p, 'car_cross_start', road.p.name)
            yield self.env.timeout(road.p.crossing_length / road.p.speed)
            p.crossing_end = float(self.env.now)
            p.visits[-1]['cross_end'] = float(self.env.now)
            self.log(p, 'car_cross_end', road.p.name)
            if road.free is not None:
                yield road.free.put(1)
                road.record_occupancy()
            depart.succeed()

    def run(self):
        if self.horizon <= 0 or self.exit_blocked_until < 0 or self.block_c_until < 0:
            raise ValueError('Invalid time parameter')
        for name in self.signal_names:
            road = self.roads[name]
            self.env.process(road.phase_process())
            self.env.process(self.car_server(road))
            self.env.process(self.ped_server(road))
        self.sources.append(self.env.process(self.source('car', 'A', self.car_rate, self.seed,
                                                         self.car_arrivals)))
        for index, name in enumerate(self.signal_names):
            arrivals = self.pedestrian_arrivals
            if isinstance(arrivals, dict):
                arrivals = arrivals.get(name, ())
            self.sources.append(self.env.process(self.source('pedestrian', name, self.pedestrian_rate,
                                                            self.seed + 100 + index, arrivals)))
        self.env.run(until=simpy.AllOf(self.env, self.sources))
        if self.jobs:
            self.env.run(until=simpy.AllOf(self.env, self.jobs))
        self.finished_at = float(self.env.now)
        return self

    def summary(self):
        cars = [p for p in self.people if p.kind == 'car']
        peds = [p for p in self.people if p.kind == 'pedestrian']
        return {
            'task': self.task,
            'simulated_seconds': round(self.finished_at, 3),
            'cars_created': len(cars),
            'cars_finished': sum(p.finished_at is not None for p in cars),
            'pedestrians_created': len(peds),
            'pedestrians_finished': sum(p.finished_at is not None for p in peds),
            'mean_car_trip_seconds': round(sum(p.trip_time for p in cars) / len(cars), 3) if cars else None,
            'max_occupancy': {name: max((x[1] for x in road.occupancy_log), default=0)
                              for name, road in self.roads.items() if road.free is not None},
        }


# -------- Exercise 4 entry point --------
"""Exercise 4: signalized crossing and pedestrian-demand plot."""
import argparse
import csv
from pathlib import Path


def graph(output='task4_pedestrian_sensitivity.png', rates=(0, 90, 180, 360, 720), repeats=5):
    # The same car arrivals and same signal schedule for all pedestrian rates.
    # Pedestrian flow cannot change car trip times under the fixed controller.
    anchor = Simulation('4', seed=42, horizon=600, pedestrian_rate=0).run()
    car_times = sorted(p.created_at for p in anchor.people if p.kind == 'car')
    points = []
    for rate in rates:
        values = []
        for rep in range(repeats):
            model = Simulation('4', seed=100 + rep, horizon=600, car_arrivals=car_times,
                               pedestrian_rate=rate).run()
            values.append(model.summary()['mean_car_trip_seconds'])
        points.append((rate, sum(values) / len(values)))
    csv_path = Path(output).with_suffix('.csv')
    with csv_path.open('w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(['pedestrians_per_hour', 'mean_car_trip_seconds'])
        writer.writerows(points)
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print('Install matplotlib to create a PNG; CSV was saved:', csv_path)
        return
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot([p[0] for p in points], [p[1] for p in points], marker='o')
    ax.set(xlabel='Pedestrian intensity (persons/hour)', ylabel='Mean car trip (seconds)',
           title='Exercise 4: fixed-time signal, varying pedestrian demand')
    ax.grid(True, alpha=.3)
    fig.tight_layout()
    fig.savefig(output, dpi=150)
    plt.close(fig)
    print('Saved:', output, 'and', csv_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plot', action='store_true', help='Save required pedestrian sensitivity plot')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    if args.plot:
        graph()
    else:
        model = Simulation('4', car_arrivals=[0, 0, 0], pedestrian_arrivals=[1, 1],
                           cars_at_stop_line=True, verbose=args.verbose).run()
        print(model.summary())
        for p in model.people:
            print(p.id, 'cross:', p.crossing_start, '->', p.crossing_end)


if __name__ == '__main__':
    main()
