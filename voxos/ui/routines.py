"""
Routines: things Vox does on a schedule ("every weekday at 9, read me the
news"). Each is a sentence Vox runs exactly as if you had typed it.

Stored in the user's data folder. The scheduler asks due() every half
minute; a routine missed while the PC was off still runs if Vox starts
within GRACE of its time, and never twice the same day.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

GRACE = timedelta(hours=2)
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAYS = (0, 1, 2, 3, 4)
EVERY_DAY = (0, 1, 2, 3, 4, 5, 6)

TEMPLATES = (
    ("Morning briefing", "What's the top tech news today? Keep it to three headlines.", "09:00", WEEKDAYS),
    ("Battery check", "What's my battery level?", "14:00", EVERY_DAY),
    ("Focus music", "Play lo-fi music on YouTube", "10:00", WEEKDAYS),
    ("End of day", "Show my recent downloads from the last 1 days", "18:30", WEEKDAYS),
)


@dataclass
class Routine:
    name: str
    command: str
    time: str                       # "HH:MM", 24-hour, local time
    days: list[int] = field(default_factory=lambda: list(WEEKDAYS))  # Monday = 0
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    last_run: str | None = None     # ISO local datetime

    def at(self, day: datetime) -> datetime:
        hh, mm = (int(x) for x in self.time.split(":"))
        return day.replace(hour=hh, minute=mm, second=0, microsecond=0)

    def ran_on(self, day: datetime) -> bool:
        return bool(self.last_run) and self.last_run[:10] == day.strftime("%Y-%m-%d")

    def is_due(self, now: datetime) -> bool:
        if not self.enabled or now.weekday() not in self.days:
            return False
        when = self.at(now)
        return when <= now <= when + GRACE and not self.ran_on(now)

    def next_run(self, now: datetime) -> datetime | None:
        if not self.enabled or not self.days:
            return None
        for offset in range(8):
            day = now + timedelta(days=offset)
            when = self.at(day)
            if day.weekday() in self.days and when > now and not (offset == 0 and self.ran_on(now)):
                return when
        return None

    def schedule_text(self) -> str:
        days = sorted(self.days)
        if days == list(EVERY_DAY):
            which = "Every day"
        elif days == list(WEEKDAYS):
            which = "Weekdays"
        elif days == [5, 6]:
            which = "Weekends"
        else:
            which = ", ".join(DAY_NAMES[d] for d in days) or "Never"
        hh, mm = (int(x) for x in self.time.split(":"))
        clock = datetime(2000, 1, 1, hh, mm).strftime("%I:%M %p").lstrip("0")
        return f"{which} at {clock}"


def valid_time(text: str) -> bool:
    try:
        datetime.strptime(text.strip(), "%H:%M")
    except ValueError:
        return False
    return True


class RoutineStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.items: list[Routine] = []
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.items = [Routine(**r) for r in raw if isinstance(r, dict)]
            except (ValueError, TypeError):
                self.items = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(r) for r in self.items], indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, routine: Routine) -> Routine:
        self.items.append(routine)
        self.save()
        return routine

    def remove(self, routine_id: str) -> None:
        self.items = [r for r in self.items if r.id != routine_id]
        self.save()

    def get(self, routine_id: str) -> Routine | None:
        return next((r for r in self.items if r.id == routine_id), None)

    def due(self, now: datetime) -> list[Routine]:
        return [r for r in self.items if r.is_due(now)]

    def mark_ran(self, routine: Routine, now: datetime) -> None:
        routine.last_run = now.isoformat(timespec="seconds")
        self.save()


_store: RoutineStore | None = None


def get_routines() -> RoutineStore:
    global _store
    if _store is None:
        from voxos.core.profile.store import data_dir

        _store = RoutineStore(data_dir() / "routines.json")
    return _store
