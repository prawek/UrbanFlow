"""Воспроизводимые независимые пуассоновские потоки на одной улице."""

import random
from dataclasses import dataclass, field

import simpy


@dataclass(init=False)
class Participant:
    id: str
    kind: str
    speed: float
    appeared_at: float
    arrived_at: float | None = None

    def __init__(self, id: str, kind: str, appeared_at: float) -> None:
        self.id = id
        self.kind = kind
        self.speed = 15 if kind == "car" else 1.4
        self.appeared_at = appeared_at
        self.arrived_at = None

    def travel(self, env: simpy.Environment, distance: float):
        yield env.timeout(distance / self.speed)
        self.arrive(env)

    def arrive(self, env: simpy.Environment) -> None:
        self.arrived_at = float(env.now)
        print(f"time={env.now:.3f}, id={self.id}, kind={self.kind}, arrived")




@dataclass
class Journal:
    travelers: list[Participant] = field(default_factory=list)
    finished_at: float = 0.0


def poisson_source(
    env: simpy.Environment,
    kind: str,
    rate_per_hour: float,
    horizon_ms: float,
    rng: random.Random,
    spawn_factory,
):
    rate_per_second = rate_per_hour / 3600
    while True:
        interval = rng.expovariate(rate_per_second)
        arrival_time = env.now + interval
        if arrival_time >= horizon_ms:
            yield env.timeout(horizon_ms - env.now)
            return
        yield env.timeout(interval)
        spawn_factory(kind)


class RandomFlowSimulation:
    def __init__(
        self,
        seed: int = 42,
        horizon_ms: float = 600,
        approach_length: float = 150,
    ) -> None:
        if horizon_ms <= 0:
            raise ValueError("Горизонт генерации должен быть положительным")
        if approach_length <= 0:
            raise ValueError("Длина подъезда к перекрёстку должна быть положительной")
        self.seed = seed
        self.horizon_ms = horizon_ms
        self.approach_length = approach_length

    def run(self) -> Journal:
        env = simpy.Environment()
        journal = Journal()
        counters = {"car": 0, "pedestrian": 0}

        def spawn(kind: str) -> None:
            counters[kind] += 1
            traveler = Participant(
                id=f"{kind}-{counters[kind]:03d}",
                kind=kind,
                appeared_at=float(env.now),
            )
            journal.travelers.append(traveler)
            print(f"time={env.now:.3f}, id={traveler.id}, kind={traveler.kind}, appeared")
            if kind == "car":
                env.process(traveler.travel(env, self.approach_length))
            else:
                traveler.arrive(env)

        env.process(
            poisson_source(
                env,
                "car",
                360,
                self.horizon_ms,
                random.Random(self.seed),
                spawn,
            )
        )
        env.process(
            poisson_source(
                env,
                "pedestrian",
                180,
                self.horizon_ms,
                random.Random(self.seed + 1),
                spawn,
            )
        )
        env.run()
        journal.finished_at = float(env.now)
        return journal


def main() -> None:
    result = RandomFlowSimulation().run()
    for kind in ("car", "pedestrian"):
        travelers = [item for item in result.travelers if item.kind == kind]
        completed = sum(item.arrived_at is not None for item in travelers)
        print(f"kind={kind}, created={len(travelers)}, completed={completed}")
    print(f"simulation_finished_at={result.finished_at:.3f}")


if __name__ == "__main__":
    main()
