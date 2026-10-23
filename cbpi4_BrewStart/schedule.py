"""Pure helpers for brew start scheduling (no CraftBeerPi imports)."""

from __future__ import annotations

import math
from dataclasses import dataclass
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


# ---------------------------------------------------------------------------
# Ready by: work back from when the water must be hot to when heating starts.
# ---------------------------------------------------------------------------

WATER_J_PER_KG_K = 4181.0
WATER_KG_PER_L = 0.995
L_PER_GAL = 3.785411784

#: Share of the element's power that goes into raising the water's
#: temperature, with the controller easing off near the target folded in.
#:
#: Calibrated rather than guessed. A model of a measured 20 gal HLT - 5346 W
#: element, 237,686 J/K, 9 W/K of losses with its pump running, a probe 65 s
#: behind the water - driven by PIDBoil's controller took 50.5 min to bring
#: 15 gal from 60 F to within half a degree of 170 F, 54.6 min from 50 F, and
#: 33.7 min for 10 gal. Water alone at full power would take 45.0, 49.1 and
#: 30.0 min. 0.90 reproduces all three to within a minute.
HEAT_EFFICIENCY = 0.90

#: The water temperature planned from when the vessel's sensor gives no usable
#: reading. Colder than any tap, so heating starts early rather than late:
#: water held hot for a while costs energy, water not ready costs the morning.
ASSUMED_COLD_C = 2.0

DEFAULT_MARGIN_MIN = 10.0


def is_fahrenheit(temp_unit) -> bool:
    return str(temp_unit or "C").strip().upper().startswith("F")


def to_unit(celsius: float, temp_unit) -> float:
    return celsius * 9.0 / 5.0 + 32.0 if is_fahrenheit(temp_unit) else celsius


def _positive(value, error_key: str, language: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(t(error_key, language)) from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(t(error_key, language))
    return number


def litres(volume, volume_unit, temp_unit, language: str = DEFAULT_LANGUAGE) -> float:
    """The volume in litres. A blank unit follows the temperature unit:
    gallons on a Fahrenheit rig, litres on a Celsius one."""
    amount = _positive(volume, "err_ready_volume", language)
    unit = str(volume_unit or "").strip().lower()
    if not unit:
        unit = "gal" if is_fahrenheit(temp_unit) else "l"
    return amount * L_PER_GAL if unit.startswith("gal") else amount


def heat_up_minutes(volume, volume_unit, start_temp, target_temp, temp_unit, watts,
                    efficiency=None,
                    language: str = DEFAULT_LANGUAGE) -> float:
    """Minutes to heat `volume` of water from start_temp to target_temp.

    Temperatures are in temp_unit. Zero when the water is already there.
    efficiency is a fraction (0.9) or a percentage (90); blank is
    HEAT_EFFICIENCY.
    """
    water_l = litres(volume, volume_unit, temp_unit, language)
    power = _positive(watts, "err_ready_watts", language)
    share = efficiency_fraction(efficiency)
    per_degree = 5.0 / 9.0 if is_fahrenheit(temp_unit) else 1.0
    rise_c = (float(target_temp) - float(start_temp)) * per_degree
    if not rise_c > 0:
        return 0.0
    joules = water_l * WATER_KG_PER_L * WATER_J_PER_KG_K * rise_c
    return joules / (power * share) / 60.0


def efficiency_fraction(value) -> float:
    """The share of the element's power that heats the water, as a fraction.

    Accepts 0.9 or 90. Blank, unreadable or out of range is HEAT_EFFICIENCY:
    above 100% is impossible, and below 10% is a typo rather than a vessel.
    """
    if value is None or str(value).strip() == "":
        return HEAT_EFFICIENCY
    try:
        number = float(value)
    except (TypeError, ValueError):
        return HEAT_EFFICIENCY
    if not math.isfinite(number):
        return HEAT_EFFICIENCY
    fraction = number / 100.0 if number > 1.0 else number
    return fraction if 0.1 <= fraction <= 1.0 else HEAT_EFFICIENCY


@dataclass(frozen=True)
class ReadyPlan:
    """When heating must start for the water to be ready on time."""

    start: datetime
    heat_minutes: float      # all the water, including strike_minutes
    strike_minutes: float    # the share for strike water heated through a coil
    margin_minutes: float
    water_temp: float        # the heated kettle's water, where the plan starts
    target_temp: float
    assumed: bool            # True when a reading was unusable and ASSUMED_COLD_C stood in

    @property
    def lead_minutes(self) -> float:
        return self.heat_minutes + self.margin_minutes


def margin_minutes(value) -> float:
    """Spare minutes: blank or unreadable is the default, negative is none."""
    if value is None or str(value).strip() == "":
        return DEFAULT_MARGIN_MIN
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_MARGIN_MIN
    if not math.isfinite(number):
        return DEFAULT_MARGIN_MIN
    return max(0.0, number)


def volume_given(value) -> bool:
    """Is an optional volume actually given? Blank and 0 both mean none."""
    if value is None or str(value).strip() == "":
        return False
    try:
        return float(value) != 0
    except (TypeError, ValueError):
        return True  # let litres() report it


def volume_label(volume, volume_unit, temp_unit) -> str:
    unit = str(volume_unit or "").strip() or ("gal" if is_fahrenheit(temp_unit) else "L")
    return "{:g} {}".format(float(volume), unit)


#: One Campden tablet treats 20 US gallons (about 75.7 L) for chlorine and
#: chloramine.
CAMPDEN_L_PER_TABLET = 20 * L_PER_GAL

_QUARTERS = {0: "", 1: "¼", 2: "½", 3: "¾"}


def campden_tablets(water_l: float) -> float:
    """Tablets for water_l of water, rounded UP to a quarter tablet.

    Up, because a little extra metabisulfite is harmless at these doses and
    too little leaves chloramine behind - and a tablet only splits so finely.
    """
    quarters = math.ceil(max(0.0, float(water_l)) / CAMPDEN_L_PER_TABLET * 4 - 1e-9)
    return max(1, quarters) / 4.0


def format_tablets(tablets: float) -> str:
    whole, rest = divmod(int(round(float(tablets) * 4)), 4)
    if whole == 0:
        return _QUARTERS[rest]
    return "{}{}".format(whole, _QUARTERS[rest])


def plan_ready_by(ready_at: datetime, target_temp, watts, temp_unit, volume,
                  water_temp, strike_volume=None, strike_temp=None,
                  volume_unit=None, margin=None, efficiency=None,
                  language: str = DEFAULT_LANGUAGE) -> ReadyPlan:
    """Plan the heating start for water that must be at target_temp by ready_at.

    volume is the water in the kettle with the element, water_temp its
    current reading. strike_volume is strike water heated alongside it, as on
    a HERMS whose mash tun is circulated through the coil, strike_temp its own
    reading; blank or 0 when there is none. A reading of None - nothing
    usable - is planned as ASSUMED_COLD_C, and the plan says so.
    """
    try:
        target = float(target_temp)
    except (TypeError, ValueError):
        raise ValueError(t("err_ready_temp", language)) from None
    if not math.isfinite(target):
        raise ValueError(t("err_ready_temp", language))

    cold = to_unit(ASSUMED_COLD_C, temp_unit)
    assumed = water_temp is None
    water = cold if assumed else float(water_temp)
    heat = heat_up_minutes(volume, volume_unit, water, target, temp_unit, watts,
                           efficiency=efficiency, language=language)

    strike = 0.0
    if volume_given(strike_volume):
        if strike_temp is None:
            # Usually filled from the same tap the same evening, so the heated
            # kettle's reading is the better stand-in - when there is one.
            assumed = assumed or water_temp is None
            strike_temp = water
        strike = heat_up_minutes(strike_volume, volume_unit, strike_temp, target,
                                 temp_unit, watts, efficiency=efficiency,
                                 language=language)

    spare = margin_minutes(margin)
    return ReadyPlan(
        start=ready_at - timedelta(minutes=heat + strike + spare),
        heat_minutes=heat + strike,
        strike_minutes=strike,
        margin_minutes=spare,
        water_temp=water,
        target_temp=target,
        assumed=assumed,
    )


def format_temp(value: float, temp_unit) -> str:
    return "{:.1f} °{}".format(value, "F" if is_fahrenheit(temp_unit) else "C")
