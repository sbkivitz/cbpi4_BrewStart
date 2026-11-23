"""BrewStart's times of day follow CraftBeerPi's CLOCK_FORMAT (1.3.0.post2).

CraftBeerPi 4.7.7.post60 shows times of day in 12-hour form by default ("7:00 AM"), or 24-hour ("07:00") by its
CLOCK_FORMAT setting. BrewStart writes every time of day the same way, in its notices, its status line and its
refusals, in English and German. Durations don't change. The setting missing, blank or any other value is 24-hour,
as on a core without the setting; a core without its helper (cbpi.api.timefmt) reads the setting itself.

The status line keeps its shape, so Brew Day's reading of a waiting BrewStart's start, widened to take " AM" and
" PM", still finds it: " · " between the parts, the start right before a " ·".
"""

import asyncio
import re
from datetime import datetime, timedelta

import pytest

pytest.importorskip("cbpi")

import cbpi4_BrewStart.brew_start as bs  # noqa: E402
from test_heats_its_kettle import NIGHT, READY, READY_BY, Server, Wall, make  # noqa: E402

#: Brew Day's reading of a waiting BrewStart's heating start (screen.js), widened for 12-hour times.
STARTS = re.compile(r"(\d{1,2}:\d{2}(?:\s?[AP]M)?)\s*·")


def server(**settings):
    s = Server()
    values = dict({"TEMP_UNIT": "F"}, **settings)
    s.config.get = lambda key, default=None: values.get(key, default)
    return s


def waiting(monkeypatch, **settings):
    """Started the night before (Fri Oct 23, 22:00), waiting for Sat Oct 24, 07:00."""
    Wall(NIGHT).install(monkeypatch)
    s, step = make(server=server(**settings))
    asyncio.run(step.on_start())
    step.running = True
    return s, step


def test_waiting_in_12_hour(monkeypatch):
    s, step = waiting(monkeypatch, CLOCK_FORMAT="12-hour")
    planned = s.notes[-1].message
    assert planned.startswith("Ready by Sat Oct 24, 7:00 AM: heating starts about Sat Oct 24, "), planned
    start = step.target_start
    assert "heating starts about Sat Oct 24, {}:{:02d} AM. ".format(start.hour, start.minute) in planned
    assert re.search(r"takes about \d+ min, plus 10 min spare\.$", planned), planned
    line = step.summary
    assert re.fullmatch(r"Ready Sat Oct 24, 7:00 AM · heating from Sat Oct 24, \d{1,2}:\d{2} AM · water 60\.0 °F · "
                        r"\d+h \d\dm \d\ds to go", line), line
    assert "{}:{:02d} AM".format(start.hour, start.minute) in STARTS.findall(line)


def test_a_new_time_and_a_refusal_in_12_hour(monkeypatch):
    s, step = waiting(monkeypatch, CLOCK_FORMAT="12-hour")
    asyncio.run(step.change_time(StartTime="06:30"))
    assert s.notes[-1].message.startswith("Time changed, was Sat Oct 24, 7:00 AM. Ready by Sat Oct 24, 6:30 AM: "
                                          "heating starts about Sat Oct 24, "), s.notes[-1].message
    with pytest.raises(ValueError) as refused:
        asyncio.run(step.change_time(Mode="Today", StartTime="21:15"))
    assert str(refused.value) == ("Fri Oct 23, 9:15 PM has already passed, so BrewStart's time didn't change. For "
                                  "tomorrow morning, choose Tomorrow.")
    assert s.notes[-1].message == str(refused.value)


def test_heating_and_late_lines_in_12_hour(monkeypatch):
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    s, step = make(server=server(CLOCK_FORMAT="12-hour"))
    asyncio.run(step.on_start())
    step.running = True
    wall.at = step.target_start + timedelta(seconds=1)
    asyncio.run(step._tick(wall.at, 100000.0))
    assert step.summary == "Heating to 152.0 °F · ready by Sat Oct 24, 7:00 AM"
    assert s.said("Time to heat - the water should be ready by Sat Oct 24, 7:00 AM.")
    wall.at = READY + timedelta(minutes=1)
    asyncio.run(step._tick(wall.at, 100060.0))
    assert step.summary == "Heating to 152.0 °F · late for Sat Oct 24, 7:00 AM"
    assert s.said("The water isn't ready by Sat Oct 24, 7:00 AM: the HLT reads 60.0 °F")
    assert STARTS.search(step.summary) is None


def test_german_follows_the_setting_too(monkeypatch):
    s, step = waiting(monkeypatch, CLOCK_FORMAT="12-hour", BREWSTART_LANGUAGE="Deutsch")
    assert "24.10.2026 7:00 AM" in step.summary, step.summary
    s, step = waiting(monkeypatch, CLOCK_FORMAT="24-hour", BREWSTART_LANGUAGE="Deutsch")
    assert "24.10.2026 07:00" in step.summary, step.summary


@pytest.mark.parametrize("setting", [{"CLOCK_FORMAT": "24-hour"}, {}, {"CLOCK_FORMAT": ""},
                                     {"CLOCK_FORMAT": "12h"}, {"CLOCK_FORMAT": None}],
                         ids=["24-hour", "missing", "blank", "other", "none"])
def test_anything_but_12_hour_is_24_hour_as_before(monkeypatch, setting):
    s, step = waiting(monkeypatch, **setting)
    assert s.notes[-1].message.startswith("Ready by Sat Oct 24, 07:00: heating starts about Sat Oct 24, 0")
    assert re.fullmatch(r"Ready Sat Oct 24, 07:00 · heating from Sat Oct 24, \d\d:\d\d · water 60\.0 °F · .+ to go",
                        step.summary), step.summary


@pytest.mark.parametrize("setting, shown", [("12-hour", "Sat Oct 24, 7:00 AM"), (None, "Sat Oct 24, 07:00")])
def test_on_a_core_without_the_helper_it_reads_the_setting_itself(monkeypatch, setting, shown):
    monkeypatch.setattr(bs, "_core_twelve_hour", None)
    s, step = waiting(monkeypatch, **({"CLOCK_FORMAT": setting} if setting else {}))
    assert step.summary.startswith("Ready {} · ".format(shown)), step.summary


def test_a_change_of_setting_shows_at_once(monkeypatch):
    values = {"TEMP_UNIT": "F", "CLOCK_FORMAT": "24-hour"}
    Wall(NIGHT).install(monkeypatch)
    s, step = make(server=Server())
    s.config.get = lambda key, default=None: values.get(key, default)
    asyncio.run(step.on_start())
    assert step._at(READY) == "Sat Oct 24, 07:00"
    values["CLOCK_FORMAT"] = "12-hour"
    assert step._at(READY) == "Sat Oct 24, 7:00 AM"
    assert step._at(datetime(2026, 10, 24, 0, 0)) == "Sat Oct 24, 12:00 AM"
    assert step._at(datetime(2026, 10, 24, 12, 0)) == "Sat Oct 24, 12:00 PM"
