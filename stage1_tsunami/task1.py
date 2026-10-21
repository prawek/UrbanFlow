"""Task 1: fixed-cycle traffic light (all times in seconds)."""
import simpy


def traffic_light(env, green_duration=30, yellow_duration=5, red_duration=25, log=None):
    """Cycle green -> yellow -> red, recording at phase start."""
    if log is None:
        log = []
    phases = (("green", green_duration), ("yellow", yellow_duration), ("red", red_duration))
    if any(duration <= 0 for _, duration in phases):
        raise ValueError("All phase durations must be positive")
    while True:
        for color, duration in phases:
            log.append((env.now, color))
            yield env.timeout(duration)


def simulate(until=180):
    env = simpy.Environment()
    log = []
    env.process(traffic_light(env, 30, 5, 25, log))
    env.run(until=until)
    return log


if __name__ == "__main__":
    for moment, color in simulate():
        print(f"t={moment:5.1f}s | signal={color}")
