"""Циклический светофор как дискретно-событийный процесс."""

import simpy


def traffic_light(
    light_name,
    env: simpy.Environment,
    green_duration: float,
    yellow_duration: float,
    red_duration: float,
):
    """Бесконечно повторяет цикл сигналов и печатает каждое переключение."""
    while True:
        color = "green"
        print(f"{light_name}, time={env.now:g}, color={color}")
        yield env.timeout(green_duration)

        color = "yellow"
        print(f"{light_name}, time={env.now:g}, color={color}")
        yield env.timeout(yellow_duration)

        color = "red"
        print(f"{light_name}, time={env.now:g}, color={color}")
        yield env.timeout(red_duration)

class TrafficLightSimulation:
    def __init__(
        self,
        green_duration: float = 30,
        yellow_duration: float = 5,
        red_duration: float = 25,
    ) -> None:
        if min(green_duration, yellow_duration, red_duration) <= 0:
            raise ValueError("Длительности сигналов должны быть положительными")
        self.green_duration = green_duration
        self.yellow_duration = yellow_duration
        self.red_duration = red_duration

    def run(self, until: float = 360) -> None:
        if until <= 0:
            raise ValueError("Время моделирования должно быть положительным")
        env = simpy.Environment()
        env.process(
            traffic_light(
                "light1",
                env,
                self.green_duration,
                self.yellow_duration,
                self.red_duration,
            )
        )
        env.run(until=until)


def main() -> None:
    TrafficLightSimulation().run()


if __name__ == "__main__":
    main()
