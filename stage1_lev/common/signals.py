"""Контроллеры фаз и проверка допуска по фактической границе текущей фазы"""

from math import isclose

from .data import validate_number


class PhaseController:
    def __init__(self, env, result, name, phases):
        self.env = env
        self.result = result
        self.name = name
        self.phases = tuple(phases)
        if not self.phases:
            raise ValueError("Нужна хотя бы одна фаза")
        for _, duration in self.phases:
            validate_number("Длительность фазы", duration)
        self.phase = self.phases[0][0]
        self.phase_ends_at = float(env.now) + self.phases[0][1]
        self.changed = env.event()

    def switch(self, phase, duration):
        self.phase = phase
        self.phase_ends_at = float(self.env.now) + duration
        self.result.record(self.env.now, self.name, "signal", phase)
        previous = self.changed
        self.changed = self.env.event()
        previous.succeed(phase)

    def allows(self, phase, duration):
        if self.phase != phase or self.env.now >= self.phase_ends_at:
            return False
        finished_at = self.env.now + duration
        return finished_at <= self.phase_ends_at or isclose(
            finished_at, self.phase_ends_at, rel_tol=0.0, abs_tol=1e-9,
        )

    def run(self):
        while True:
            for phase, duration in self.phases:
                self.switch(phase, duration)
                yield self.env.timeout(duration)


class BiPhaseController(PhaseController):
    """Двухфазный светофор заданий 4, 6 и 6а"""

    def __init__(self, env, result, green_duration=20, red_duration=10,
                 name="traffic_light"):
        self.green_duration = green_duration
        self.red_duration = red_duration
        super().__init__(env, result, name,
                         (("car", green_duration), ("pedestrian", red_duration)))

    def car_time_left(self):
        if self.phase != "car":
            return 0.0
        return max(0.0, self.phase_ends_at - self.env.now)


class SignalController(PhaseController):
    """Местный светофор с произвольной начальной фазой и её остатком."""

    def __init__(self, env, result, config):
        super().__init__(env, result, f"traffic_light:{config.name}", config.phases)
        self.initial_phase = config.initial_phase
        self.initial_elapsed = config.initial_elapsed
        phase, duration = self.phases[self.initial_phase]
        self.phase = phase
        self.phase_ends_at = float(env.now) + duration - self.initial_elapsed

    def run(self):
        index = self.initial_phase
        remaining = self.phases[index][1] - self.initial_elapsed
        while True:
            self.switch(self.phases[index][0], remaining)
            yield self.env.timeout(remaining)
            index = (index + 1) % len(self.phases)
            remaining = self.phases[index][1]
