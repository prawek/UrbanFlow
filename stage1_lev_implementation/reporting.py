"""Консольный журнал и выгрузка результатов без округления времени в CSV."""

import csv
import json
from dataclasses import asdict, fields
from pathlib import Path

from .data import Event, Participant


def display(result, show_log=False):
    print(f"Exercise {result.exercise}: finished_at={result.finished_at:.6f} s")
    if show_log:
        for event in result.events:
            print(f"t={event.time:.6f} s | {event.actor} | {event.kind} | {event.action}")
    if result.participants:
        for kind in ("car", "pedestrian"):
            counts = result.summary()[kind]
            print(f"{kind}: created={counts['created']}, arrived={counts['arrived']}")


def export(result, output):
    destination = Path(output) / f"exercise{result.exercise:02d}"
    destination.mkdir(parents=True, exist_ok=True)
    for filename, record_type, rows in (
        ("events.csv", Event, result.events),
        ("participants.csv", Participant, result.participants),
    ):
        with (destination / filename).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=[f.name for f in fields(record_type)])
            writer.writeheader()
            writer.writerows(asdict(row) for row in rows)
    (destination / "summary.json").write_text(
        json.dumps(result.summary(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return destination
