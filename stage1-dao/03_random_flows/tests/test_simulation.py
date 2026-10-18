import io
import unittest
from contextlib import redirect_stdout

from .. import RandomFlowSimulation


class RandomFlowTests(unittest.TestCase):
    def test_reproducibility_and_drain(self):
        first_output = io.StringIO()
        with redirect_stdout(first_output):
            first = RandomFlowSimulation(seed=42).run()

        second_output = io.StringIO()
        with redirect_stdout(second_output):
            second = RandomFlowSimulation(seed=42).run()

        different_output = io.StringIO()
        with redirect_stdout(different_output):
            different = RandomFlowSimulation(seed=43).run()

        self.assertEqual(first, second)
        self.assertEqual(first_output.getvalue(), second_output.getvalue())
        self.assertNotEqual(first.travelers, different.travelers)
        self.assertNotEqual(first_output.getvalue(), different_output.getvalue())
        self.assertGreaterEqual(first.finished_at, 600)
        self.assertTrue(first.travelers)
        output_lines = first_output.getvalue().splitlines()
        self.assertEqual(len(output_lines), len(first.travelers) * 2)
        self.assertTrue(
            all(
                "time=" in line
                and "id=" in line
                and "kind=" in line
                and (line.endswith("appeared") or line.endswith("arrived"))
                for line in output_lines
            )
        )
        for traveler in first.travelers:
            self.assertGreater(traveler.appeared_at, 0)
            self.assertLess(traveler.appeared_at, 600)
            expected_speed, distance = (
                (15, 150)
                if traveler.kind == "car"
                else (1.4, 0)
            )
            self.assertEqual(traveler.speed, expected_speed)
            self.assertAlmostEqual(
                traveler.arrived_at - traveler.appeared_at,
                distance / traveler.speed,
            )

    def test_pedestrians_arrive_immediately_without_extending_simulation(self):
        with redirect_stdout(io.StringIO()):
            result = RandomFlowSimulation(seed=42, horizon_ms=5).run()
        self.assertTrue(result.travelers)
        self.assertTrue(all(p.kind == "pedestrian" for p in result.travelers))
        for pedestrian in result.travelers:
            self.assertEqual(pedestrian.arrived_at, pedestrian.appeared_at)
        self.assertEqual(result.finished_at, 5)


if __name__ == "__main__":
    unittest.main()
