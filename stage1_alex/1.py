"""Циклический светофор как дискретно-событийный процесс."""
import math
import simpy


def traffic_light(
    light_name: str,
    env: simpy.Environment,
    green_duration: float,
    yellow_duration: float,
    red_duration: float,
) -> None:
    """Бесконечно повторяет цикл сигналов и логирует каждое переключение."""
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
        green_duration: float = 30.0,
        yellow_duration: float = 5.0,
        red_duration: float = 25.0,
    ) -> None:
        if not all(
            math.isfinite(d) and d > 0
            for d in (green_duration, yellow_duration, red_duration)
        ):
            raise ValueError("Длительности сигналов должны быть конечными положительными числами")
        
        self.green_duration = green_duration
        self.yellow_duration = yellow_duration
        self.red_duration = red_duration

    def run(self, until: float = 180.0) -> None:
        if not math.isfinite(until) or until <= 0:
            raise ValueError("Время моделирования должно быть конечным положительным числом")
            
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
