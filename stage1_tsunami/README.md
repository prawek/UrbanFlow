# UrbanFlow — my stage 1 solutions (tasks 1–3)

Independent implementation based on `openspec/project.md` stage 1.
All simulation times are seconds, distances meters, velocities meters/second.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run

```bash
python task1.py
python task2.py
python task3.py
python -m unittest discover -p 'test_*.py' -v
```

### Task 1
A light starts green at t=0 and repeats green (30 s), yellow (5 s), red (25 s), three times. `env.run(until=180)` excludes the event at exactly 180 seconds.

### Task 2
A car drives 150 m at 15 m/s (10 s). A pedestrian walks 28 m at 1.4 m/s (20 s). Both start at t=0 and finish independently.

### Task 3
Two independently seeded Poisson arrival streams: 360 cars/hour and 180 pedestrians/hour. Every arrival starts a separate process. Arrivals must occur strictly before 600 seconds, but already started trips may finish later. The event log and traveler records are reproducible for a fixed seed. Expected arrival counts 60 and 30 respectively, not fixed results.

## Notes
No interaction, traffic signals, pedestrian crossings or queues in tasks 2–3 by design; these are introduced in later tasks.
