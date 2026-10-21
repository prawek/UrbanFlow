"""Task 3: seeded Poisson arrivals, independent concurrent trips."""
from dataclasses import dataclass
import random
import simpy


@dataclass
class Traveler:
    id: str
    kind: str
    created_at: float
    finished_at: float | None = None


def move(env, traveler, travel_duration, log):
    log.append((env.now, traveler.id, traveler.kind, "appeared"))
    yield env.timeout(travel_duration)
    traveler.finished_at = env.now
    log.append((env.now, traveler.id, traveler.kind, "arrived"))


def generate(env, kind, arrivals_per_hour, travel_duration, horizon, rng, travelers, log):
    rate = arrivals_per_hour / 3600.0
    if rate <= 0:
        raise ValueError("Arrival rate must be positive")
    index = 0
    while True:
        interval = rng.expovariate(rate)
        if env.now + interval >= horizon:
            break
        yield env.timeout(interval)
        index += 1
        traveler = Traveler(f"{kind}-{index}", kind, env.now)
        travelers.append(traveler)
        env.process(move(env, traveler, travel_duration, log))


def simulate(seed=42, horizon=600):
    env = simpy.Environment()
    travelers = []
    log = []
    # Separate seeded generators prevent changes in one stream from changing the other.
    car_rng = random.Random(seed)
    ped_rng = random.Random(seed + 1)
    env.process(generate(env, "car", 360, 150 / 15, horizon, car_rng, travelers, log))
    env.process(generate(env, "pedestrian", 180, 28 / 1.4, horizon, ped_rng, travelers, log))
    # No until: pending trips finish after the last arrival.
    env.run()
    return travelers, log, env.now


if __name__ == "__main__":
    travelers, records, finish_time = simulate()
    for t, traveler_id, kind, action in records:
        print(f"t={t:7.2f}s | {traveler_id:14} | {kind:10} | {action}")
    for kind in ("car", "pedestrian"):
        group = [person for person in travelers if person.kind == kind]
        print(f"{kind}: appeared={len(group)}, arrived={sum(p.finished_at is not None for p in group)}")
    print(f"Simulation finished at t={finish_time:.2f}s")
