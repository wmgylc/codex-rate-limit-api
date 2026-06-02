from dataclasses import dataclass


@dataclass
class RateLimits:
    five_hour_pct: float | None = None
    five_hour_resets_at: int | None = None
    five_hour_remaining_pct: float | None = None
    seven_day_pct: float | None = None
    seven_day_resets_at: int | None = None
    seven_day_remaining_pct: float | None = None
    model: str = ""
    updated_at: str = ""
    source: str = ""
