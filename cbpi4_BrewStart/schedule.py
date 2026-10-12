"""Pure helpers for brew start scheduling (no CraftBeerPi imports)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Tuple

try:
    from .strings import DEFAULT_LANGUAGE, t
except ImportError:  # pragma: no cover - direct import in tests
    from cbpi4_BrewStart.strings import DEFAULT_LANGUAGE, t


DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%y %H:%M",
    "%d.%m.%y-%H:%M",  # DatetimeWaitStep-Format
    "%d.%m.%Y-%H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
)

TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%H")


def parse_time(value: str, language: str = DEFAULT_LANGUAGE) -> Tuple[int, int, int]:
    """Parse HH:MM[:SS] into (hour, minute, second)."""
    text = (value or "").strip()
    if not text:
        raise ValueError(t("err_time_missing", language))

    for fmt in TIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.hour, parsed.minute, parsed.second
        except ValueError:
            continue
    raise ValueError(t("err_time_invalid", language, value=value))


def parse_datetime(value: str, language: str = DEFAULT_LANGUAGE) -> datetime:
    """Parse a full date+time string."""
    text = (value or "").strip()
    if not text:
        raise ValueError(t("err_datetime_missing", language))

    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(t("err_datetime_invalid", language, value=value))


def resolve_start(
    mode: str,
    start_time: str,
    start_date: str = "",
    now: Optional[datetime] = None,
    language: str = DEFAULT_LANGUAGE,
) -> datetime:
    """
    Resolve the absolute start datetime.

    Modes:
      - DateTime: start_date + start_time (or full datetime in StartDateTime field via start_date)
      - Today: today at start_time; if already past, tomorrow
      - Daily: next occurrence of start_time (today or tomorrow)
    """
    now = now or datetime.now()
    mode = (mode or "Today").strip()

    if mode == "DateTime":
        date_text = (start_date or "").strip()
        time_text = (start_time or "").strip()

        # Allow putting the full datetime into StartDate alone
        if date_text and not time_text:
            return parse_datetime(date_text)

        if date_text and time_text:
            # Prefer combined parse for flexible formats
            try:
                return parse_datetime(f"{date_text} {time_text}")
            except ValueError:
                hour, minute, second = parse_time(time_text)
                day = datetime.strptime(date_text.strip(), "%Y-%m-%d")
                return day.replace(hour=hour, minute=minute, second=second, microsecond=0)

        if time_text and " " in time_text:
            return parse_datetime(time_text)

        raise ValueError(t("err_datetime_needs_both", language))

    hour, minute, second = parse_time(start_time)
    candidate = now.replace(hour=hour, minute=minute, second=second, microsecond=0)

    if mode == "Today":
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    if mode == "Daily":
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    raise ValueError(t("err_unknown_mode", language, mode=mode))


def format_countdown(seconds: int) -> str:
    """Human-readable countdown, e.g. 1h 05m 03s."""
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes > 0:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_start(start: datetime) -> str:
    return start.strftime("%d.%m.%Y %H:%M")
