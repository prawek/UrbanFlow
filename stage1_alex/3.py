"""Воспроизводимые независимые пуассоновские потоки на одной улице."""
import math
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
        self.speed = 15.0 if kind == "car" else 1.4
        self.appeared_at = appeared_at
        self.arrived_at = None

    def travel(self, env: simpy.Environment, distance: float) -> None:
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
    horizon_s: float,
    rng: random.Random,
    spawn_factory: callable,
) -> None:
    rate_per_second = rate_per_hour / 3600.0
    while True:
        interval = rng.expovariate(rate_per_second)
        if env.now + interval >= horizon_s:
            yield env.timeout(horizon_s - env.now)
            return
        yield env.timeout(interval)
        spawn_factory(kind)


class RandomFlowSimulation:
    def __init__(
        self,
        seed: int = 42,
        horizon_s: float = 600.0,
        car_approach_length: float = 150.0,
        pedestrian_approach_length: float = 28.0,
    ) -> None:
        if not math.isfinite(horizon_s) or horizon_s <= 0:
            raise ValueError("Горизонт генерации должен быть конечным положительным числом")
        if not math.isfinite(car_approach_length) or car_approach_length <= 0:
            raise ValueError("Длина подъезда к перекрёстку должна быть конечным положительным числом")
        if not math.isfinite(pedestrian_approach_length) or pedestrian_approach_length < 0:
            raise ValueError("Длина подхода пешехода должна быть конечным неотрицательным числом")
        
        self.seed = seed
        self.horizon_s = horizon_s
        self.car_approach_length = car_approach_length
        self.pedestrian_approach_length = pedestrian_approach_length

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
                env.process(traveler.travel(env, self.car_approach_length))
            elif self.pedestrian_approach_length > 0:
                env.process(traveler.travel(env, self.pedestrian_approach_length))
            else:
                traveler.arrive(env)

        env.process(
            poisson_source(
                env, "car", 360.0, self.horizon_s, random.Random(self.seed), spawn
            )
        )
        env.process(
            poisson_source(
                env, "pedestrian", 180.0, self.horizon_s, random.Random(self.seed + 1), spawn
            )
        )
        
        env.run()
        journal.finished_at = float(env.now)
        return journal


def main() -> None:
    result = RandomFlowSimulation().run()
    for kind in ("car", "pedestrian"):
        travelers = [t for t in result.travelers if t.kind == kind]
        completed = sum(1 for t in travelers if t.arrived_at is not None)
        print(f"kind={kind}, created={len(travelers)}, completed={completed}")
    print(f"simulation_finished_at={result.finished_at:.3f}")


if __name__ == "__main__":
    main()
