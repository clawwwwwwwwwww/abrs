"""Load and validate business config from YAML."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Dict, List

import yaml

WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass(frozen=True)
class Service:
    name: str
    duration_minutes: int


@dataclass(frozen=True)
class DayHours:
    open: time
    close: time


@dataclass(frozen=True)
class Location:
    address: str
    parking: str
    access_notes: str
    review_url: str


@dataclass(frozen=True)
class Owner:
    name: str
    whatsapp: str


@dataclass(frozen=True)
class BusinessConfig:
    services: List[Service]
    business_hours: Dict[str, DayHours]  # weekday key -> hours; missing key = closed
    location: Location
    owner: Owner

    def service_by_name(self, name: str) -> Service | None:
        for s in self.services:
            if s.name.lower() == name.lower():
                return s
        return None

    def hours_for_weekday(self, weekday_idx: int) -> DayHours | None:
        return self.business_hours.get(WEEKDAY_KEYS[weekday_idx])


def _parse_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def load_business_config(path: str | Path) -> BusinessConfig:
    raw = yaml.safe_load(Path(path).read_text())

    services = [Service(s["name"], int(s["duration_minutes"])) for s in raw["services"]]
    if not services:
        raise ValueError("config: services list is empty")

    hours: Dict[str, DayHours] = {}
    for key, val in (raw.get("business_hours") or {}).items():
        if key not in WEEKDAY_KEYS:
            raise ValueError(f"config: unknown weekday {key!r}")
        hours[key] = DayHours(_parse_time(val["open"]), _parse_time(val["close"]))

    loc = raw["location"]
    location = Location(
        address=loc["address"],
        parking=loc.get("parking", ""),
        access_notes=loc.get("access_notes", ""),
        review_url=loc.get("review_url", ""),
    )

    own = raw["owner"]
    owner = Owner(name=own["name"], whatsapp=own["whatsapp"])

    return BusinessConfig(services=services, business_hours=hours, location=location, owner=owner)
