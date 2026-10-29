from datetime import datetime

import pytest

from cbpi4_BrewStart.schedule import (
    format_countdown,
    format_start,
    parse_time,
    resolve_start,
)


def test_parse_time_hhmm():
    assert parse_time("06:30") == (6, 30, 0)


def test_parse_time_hhmmss():
    assert parse_time("18:05:09") == (18, 5, 9)


def test_parse_time_invalid():
    with pytest.raises(ValueError):
        parse_time("25:00")


def test_resolve_today_future():
    now = datetime(2026, 10, 10, 5, 0, 0)
    start = resolve_start("Today", "06:00", now=now)
    assert start == datetime(2026, 10, 10, 6, 0, 0)


def test_resolve_today_already_past_stays_today():
    """Today means today (1.2.0.post4). Until then a passed time quietly became
    tomorrow's, which is what Tomorrow is now for; the step refuses to act on a
    passed time by itself (test_step_clock)."""
    now = datetime(2026, 10, 10, 7, 0, 0)
    start = resolve_start("Today", "06:00", now=now)
    assert start == datetime(2026, 10, 10, 6, 0, 0)


def test_resolve_blank_mode_is_today():
    now = datetime(2026, 10, 10, 7, 0, 0)
    assert resolve_start("", "06:00", now=now) == datetime(2026, 10, 10, 6, 0, 0)
    assert resolve_start(None, "08:00", now=now) == datetime(2026, 10, 10, 8, 0, 0)


@pytest.mark.parametrize("now", [datetime(2026, 10, 23, 22, 0), datetime(2026, 10, 23, 5, 0),
                                 datetime(2026, 10, 23, 0, 30)])
def test_resolve_tomorrow_is_the_next_day_at_that_time(now):
    assert resolve_start("Tomorrow", "07:00", now=now) == datetime(2026, 10, 24, 7, 0)


def test_resolve_tomorrow_across_the_end_of_daylight_saving():
    # Naive local times: tomorrow at 07:00 is 07:00 on the clock, not 24 h on.
    assert resolve_start("Tomorrow", "07:00", now=datetime(2026, 10, 31, 22, 0)) == \
        datetime(2026, 11, 1, 7, 0)


def test_resolve_daily_same_as_next_occurrence():
    """Daily, from older forms, still means the next time the clock shows it."""
    now = datetime(2026, 10, 10, 21, 0, 0)
    start = resolve_start("Daily", "05:30", now=now)
    assert start == datetime(2026, 10, 11, 5, 30, 0)


def test_resolve_date_from_the_browsers_pickers():
    """What <input type=date> and <input type=time> give: YYYY-MM-DD, HH:MM."""
    now = datetime(2026, 10, 1, 12, 0, 0)
    assert resolve_start("Date", "06:15", "2026-10-12", now=now) == datetime(2026, 10, 12, 6, 15)
    assert resolve_start("Date", "06:15:30", "2026-10-12", now=now) == \
        datetime(2026, 10, 12, 6, 15, 30)


@pytest.mark.parametrize("time_, date_, says", [
    ("", "2026-10-12", "The time is blank"),
    ("06:15", "", "no date is set"),
    ("", "", "set the date and the time"),
    ("06:15", "12/10/2026", "Invalid start date '12/10/2026'"),
    ("6 am", "2026-10-12", "Invalid start time '6 am'"),
])
def test_resolve_date_says_what_is_missing(time_, date_, says):
    with pytest.raises(ValueError, match=says):
        resolve_start("Date", time_, date_, now=datetime(2026, 10, 1))


def test_resolve_blank_time_is_an_error_not_six_oclock():
    """Until 1.2.0.post4 a blank StartTime quietly meant 06:00."""
    with pytest.raises(ValueError, match="The time is blank"):
        resolve_start("Today", "", now=datetime(2026, 10, 1, 5, 0))


def test_errors_follow_the_language():
    with pytest.raises(ValueError, match="Die Uhrzeit ist leer"):
        resolve_start("Today", "", now=datetime(2026, 10, 1), language="de")


def test_resolve_datetime_split_fields():
    now = datetime(2026, 10, 1, 12, 0, 0)
    start = resolve_start(
        "DateTime",
        start_time="06:15",
        start_date="2026-10-12",
        now=now,
    )
    assert start == datetime(2026, 10, 12, 6, 15, 0)


def test_resolve_datetime_german_format_in_date_field():
    start = resolve_start(
        "DateTime",
        start_time="",
        start_date="12.10.2026 06:15",
        now=datetime(2026, 10, 1, 0, 0, 0),
    )
    assert start == datetime(2026, 10, 12, 6, 15, 0)


def test_resolve_datetime_waitstep_format():
    start = resolve_start(
        "DateTime",
        start_time="",
        start_date="12.10.26-06:15",
        now=datetime(2026, 10, 1, 0, 0, 0),
    )
    assert start == datetime(2026, 10, 12, 6, 15, 0)


def test_format_countdown():
    assert format_countdown(3661) == "1h 01m 01s"
    assert format_countdown(65) == "1m 05s"
    assert format_countdown(9) == "9s"
    assert format_countdown(-3) == "0s"


def test_format_start_in_english_names_the_day():
    """English text gets an English date (Scott, 29 Oct: "If my language is
    English, I should not see other languages"), with the weekday - so a start
    a day later than meant stands out. Named by the plugin, not by strftime's
    %a/%b, which follow the process's locale."""
    assert format_start(datetime(2026, 10, 11, 6, 0)) == "Sun Oct 11, 06:00"
    assert format_start(datetime(2026, 10, 30, 19, 5), "en") == "Fri Oct 30, 19:05"
    assert format_start(datetime(2026, 10, 11, 6, 0), "de") == "11.10.2026 06:00"
    assert format_start(datetime(2026, 10, 11, 6, 0), "Deutsch") == "11.10.2026 06:00"


def test_format_start_ignores_the_process_locale(monkeypatch):
    import cbpi4_BrewStart.schedule as schedule

    class GermanLocale(datetime):
        def strftime(self, fmt):
            return datetime.strftime(self, fmt.replace("%a", "So").replace("%b", "Okt"))

    moment = GermanLocale(2026, 10, 11, 6, 0)
    assert schedule.format_start(moment, "en") == "Sun Oct 11, 06:00"


# ----------------------------------------------------------- negative controls
#
# schedule.py as released in 1.2.0.post3, so the tests above are known to
# tell the new rules from the old.

def _post3_schedule():
    import importlib.util
    import subprocess
    import tempfile
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    try:
        source = subprocess.run(
            ["git", "-C", str(root), "show", "4c14a92:cbpi4_BrewStart/schedule.py"],
            capture_output=True, text=True, encoding="utf-8", check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git history with 4c14a92 not available")
    path = Path(tempfile.mkdtemp()) / "schedule_post3.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("schedule_post3", path)
    module = importlib.util.module_from_spec(spec)
    # Registered first: its dataclass looks itself up in sys.modules.
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_control_post3_moved_a_passed_today_to_tomorrow():
    old = _post3_schedule()
    assert old.resolve_start("Today", "06:00", now=datetime(2026, 10, 10, 7, 0)) == \
        datetime(2026, 10, 11, 6, 0)


def test_control_post3_had_no_tomorrow_or_date():
    old = _post3_schedule()
    for mode in ("Tomorrow", "Date"):
        with pytest.raises(ValueError, match="Unknown mode"):
            old.resolve_start(mode, "07:00", "2026-10-12", now=datetime(2026, 10, 1))


def test_control_post3_wrote_german_dates_in_english_text():
    old = _post3_schedule()
    assert old.format_start(datetime(2026, 10, 11, 6, 0)) == "11.10.2026 06:00"
