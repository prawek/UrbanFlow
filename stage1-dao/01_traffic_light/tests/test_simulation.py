import io
import unittest
from contextlib import redirect_stdout

from .. import TrafficLightSimulation


class TrafficLightTests(unittest.TestCase):
    def test_exact_three_cycles(self):
        output = io.StringIO()
        with redirect_stdout(output):
            TrafficLightSimulation().run()
        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "light1, time=0, color=green",
                "light1, time=30, color=yellow",
                "light1, time=35, color=red",
                "light1, time=60, color=green",
                "light1, time=90, color=yellow",
                "light1, time=95, color=red",
                "light1, time=120, color=green",
                "light1, time=150, color=yellow",
                "light1, time=155, color=red",
            ],
        )


if __name__ == "__main__":
    unittest.main()
