"""Консольный журнал и выгрузка результатов без округления времени в CSV"""

import csv
import json
from dataclasses import fields
from enum import Enum
from pathlib import Path

from .data import Event


def display(result, show_log=False):
    summary = result.summary()
    print(f"Exercise {result.exercise}: finished_at={result.finished_at:.6f} s")
    if show_log:
        for event in result.events:
            print(f"t={event.time:.6f} s | {event.actor} | {event.kind} | {event.action}")
    if result.participants:
        for kind in ("car", "pedestrian"):
            counts = summary[kind]
            print(f"{kind}: created={counts['created']}, arrived={counts['arrived']}")
            if counts.get("mean_travel_time") is not None:
                print(f"  mean_travel_time={counts['mean_travel_time']:.6f} s, "
                      f"mean_queue_wait={counts['mean_queue_wait']:.6f} s")
    for direction, counts in summary.get("directions", {}).items():
        print(f"{direction}: generated={counts['generated']}, initial={counts['initial']}, "
              f"arrived={counts['arrived']}")


def write_csv(path, record_type, rows):
    columns = [f.name for f in fields(record_type) if f.metadata.get("export", True)]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            values = {column: getattr(row, column) for column in columns}
            writer.writerow({column: value.value if isinstance(value, Enum) else value
                             for column, value in values.items()})


def export(result, output, *, scenario=None):
    label = f"{result.exercise:02d}" if isinstance(result.exercise, int) else str(result.exercise)
    destination = Path(output) / f"exercise{label}"
    if scenario is not None:
        destination /= scenario
    destination.mkdir(parents=True, exist_ok=True)
    for filename, record_type, rows in (
        ("events.csv", Event, result.events),
        ("participants.csv", result.participant_type, result.participants),
    ):
        write_csv(destination / filename, record_type, rows)
    for filename, record_type, rows in result.extra_tables():
        write_csv(destination / filename, record_type, rows)
    (destination / "summary.json").write_text(
        json.dumps(result.summary(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return destination
