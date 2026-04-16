from datetime import time
from pathlib import Path

from tools.config import load_business_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "business.yaml"


def test_loads_default_config():
    cfg = load_business_config(CONFIG_PATH)
    assert len(cfg.services) >= 1
    assert cfg.service_by_name("Consultation").duration_minutes == 30
    # case-insensitive lookup
    assert cfg.service_by_name("consultation") is not None
    assert cfg.service_by_name("nope") is None


def test_business_hours_per_weekday():
    cfg = load_business_config(CONFIG_PATH)
    monday = cfg.hours_for_weekday(0)
    assert monday is not None
    assert monday.open == time(9, 0)
    assert monday.close == time(17, 0)
    # Sunday is closed in the default config
    assert cfg.hours_for_weekday(6) is None


def test_location_and_owner_load():
    cfg = load_business_config(CONFIG_PATH)
    assert "Main St" in cfg.location.address
    assert cfg.location.review_url.startswith("https://")
    assert cfg.owner.whatsapp.startswith("+")
