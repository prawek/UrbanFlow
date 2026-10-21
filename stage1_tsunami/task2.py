"""Task 2: two concurrent independent travelers."""
import simpy


def move(env, kind, distance, speed, log):
    if distance < 0 or speed <= 0:
        raise ValueError("Distance must be non-negative; speed must be positive")
    log.append((env.now, kind, "started"))
    yield env.timeout(distance / speed)
    log.append((env.now, kind, "arrived"))


def simulate():
    env = simpy.Environment()
    log = []
    env.process(move(env, "car", 150, 15, log))
    env.process(move(env, "pedestrian", 28, 1.4, log))
    env.run()
    return log, env.now


if __name__ == "__main__":
    records, finish_time = simulate()
    for moment, kind, event in records:
        print(f"t={moment:5.1f}s | {kind:10} | {event}")
    print(f"Simulation finished at t={finish_time:.1f}s")
