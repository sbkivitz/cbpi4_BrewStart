"""BrewStart's change_time: a new day and time, also while the step waits for it (1.2.0.post10).

CraftBeerPi refuses a running step's settings (PUT /step2/{id} answers 409), and a BrewStart waiting for its time is
running. So Brew Day, which sets the time the night before, calls the step's action change_time
(POST /step2/action/{id}), which works while a step runs. These check that:

- the action is declared, so a page finds it in the step types' actions, with the day and the time as its settings;
- waiting, it plans again as START does: the new ready time, the heating start worked out again from the water as it
  is, Today and Tomorrow counted from the change, the notice saying the new start, the moment kept for a restart and
  the server allowed to carry it on until two hours after the new time; a ready time too soon for the heating starts
  the heating now, as START does; a setting not given stays as it is, a day from an older form included, and a blank
  day is Today, as in the form;
- refused, with nothing changed and every screen told why as an info: a time that has passed, a time that can't be
  read, a day it doesn't offer, a step whose time has come, one waiting for Next Step or Heat now, and one that has
  finished;
- Ready by heating its kettle itself (1.3.0.post1): once the heating has begun, a new time only changes the ready time, and
  the heating carries on;
- not yet started, the time is saved for START, as the form saves it;
- its words are in German too.
"""

import asyncio
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cbpi")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cbpi4_BrewStart.brew_start as bs  # noqa: E402
from cbpi.api.dataclasses import NotificationType  # noqa: E402
from cbpi.api.decorator import resolve_action  # noqa: E402
from cbpi.api.step import StepState  # noqa: E402
from cbpi4_BrewStart.strings import MESSAGES  # noqa: E402
from test_step_clock import Kettle, Kettles  # noqa: E402

REAL_DATETIME = datetime
NIGHT = REAL_DATETIME(2026, 10, 23, 22, 0, 0)          # Friday night
READY = REAL_DATETIME(2026, 10, 24, 7, 0, 0)            # hot water by Saturday 07:00
NAME = "Hot water by Ready by"

READY_BY = {"Mode": "Date", "StartDate": "2026-10-24", "StartTime": "07:00", "Timing": "Ready by",
            "Heat_Kettle": "hlt", "Ready_Temp": 152, "Volume": 15, "Volume_Unit": "gal",
            "Heater_Watts": 5346, "NotifyBefore": 0}
START_AT = {"Mode": "Date", "StartDate": "2026-10-24", "StartTime": "06:00", "Timing": "Start at",
            "AutoNext": "Yes", "NotifyBefore": 0}


class Steps:
    """The server's step controller, as far as BrewStart reaches into it."""

    def __init__(self, status=StepState.ACTIVE):
        self.status = status
        self.allowed = {}
        self.saves = 0

    def find_by_id(self, step_id):
        return types.SimpleNamespace(id=step_id, status=self.status)

    async def save(self):
        self.saves += 1

    def push_udpate(self):
        pass

    async def allow_resume(self, step_id, until, by=None):
        self.allowed[step_id] = (until, by)
        return True


class Cbpi:
    def __init__(self, steps):
        self.notes = []
        self.step = steps
        settings = {"TEMP_UNIT": "F"}
        self.config = types.SimpleNamespace(get=lambda k, default=None: settings.get(k, default))
        kettles = {"hlt": Kettle("HLT", "hlt-probe")}
        readings = {"hlt-probe": {"value": 60.0, "age": 1, "max_age": 30}}
        self.kettle = Kettles(kettles)
        self.sensor = types.SimpleNamespace(get_sensor_value=lambda i: readings.get(i))

    def notify(self, title, message, type=None, *args, **kwargs):
        self.notes.append((message, type))

    def said(self):
        return [m for m, _ in self.notes]


class Wall:
    """The wall clock, which the test moves."""

    def __init__(self, at):
        self.at = at

    def install(self, monkeypatch):
        wall = self

        class Moved(REAL_DATETIME):
            @classmethod
            def now(cls, tz=None):
                return wall.at
        monkeypatch.setattr(bs, "datetime", Moved)


def make(props, status=StepState.ACTIVE):
    step = bs.BrewStart(Cbpi(Steps(status)), "bs", NAME, dict(props), lambda s, r: None)
    step.TICK_S = 0.01
    return step


def waiting(monkeypatch, props=READY_BY, at=NIGHT):
    """START pressed at `at`: the step planned, and waiting for its time."""
    wall = Wall(at)
    wall.install(monkeypatch)
    step = make(props)
    asyncio.run(step.on_start())
    step.running = True                           # as CBPiStep.start() leaves it
    step.cbpi.notes.clear()
    return step, wall


def change(step, **settings):
    return asyncio.run(step.change_time(**settings))


def refused(step, **settings):
    before = (dict(step.props), getattr(step, "target_start", None), getattr(step, "asked_for", None))
    with pytest.raises(ValueError) as raised:
        change(step, **settings)
    after = (dict(step.props), getattr(step, "target_start", None), getattr(step, "asked_for", None))
    assert after == before, "a refused change changed something"
    assert step.cbpi.notes and step.cbpi.notes[-1] == (str(raised.value), NotificationType.INFO)
    return str(raised.value)


# ----------------------------------------------------------- declared

def test_the_action_is_declared_with_the_day_and_the_time():
    declared = bs.BrewStart.change_time
    assert getattr(declared, "action", False) is True and declared.key == "Change time"
    assert [p.label for p in declared.parameters] == ["Mode", "StartDate", "StartTime"]
    assert resolve_action(make(READY_BY), "change_time") is not None


def test_its_words_are_in_german_too():
    keys = [k for k in MESSAGES["en"] if k.startswith(("change_", "prop_change_")) or
            k in ("action_change_time", "notify_time_changed")]
    assert len(keys) == 11 and all(k in MESSAGES["de"] for k in keys)
    assert [p.display_label for p in bs.change_time_parameters("de")] == ["Tag", "Datum", "Uhrzeit"]


# ----------------------------------------------------------- waiting

def test_waiting_for_ready_by_it_takes_a_new_time_and_plans_again(monkeypatch):
    step, _ = waiting(monkeypatch)
    first_start = step.target_start
    change(step, Mode="Date", StartDate="2026-10-24", StartTime="06:30")
    assert (step.props["Mode"], step.props["StartDate"], step.props["StartTime"]) == ("Date", "2026-10-24", "06:30")
    assert step.ready_at == step.asked_for == READY - timedelta(minutes=30)
    assert step.target_start == first_start - timedelta(minutes=30) == step.plan.start
    said = step.cbpi.said()
    assert len(said) == 1 and said[0].startswith("Time changed, was Sat Oct 24, 07:00. Ready by Sat Oct 24, 06:30")
    assert step.cbpi.notes[0][1] == NotificationType.INFO
    assert step.props[bs.BrewStart.PLANNED_PROP] == "bs|Date|06:30|2026-10-24|" + (
        READY - timedelta(minutes=30)).isoformat()
    assert step.cbpi.step.allowed["bs"][0] == (READY + timedelta(hours=1, minutes=30)).timestamp()
    assert "Sat Oct 24, 06:30" in step.summary


def test_a_setting_not_given_stays_as_it_is(monkeypatch):
    step, _ = waiting(monkeypatch)
    change(step, StartTime="06:00")
    assert (step.props["Mode"], step.props["StartDate"]) == ("Date", "2026-10-24")
    assert step.asked_for == READY - timedelta(hours=1)


def test_a_day_from_an_older_form_not_given_stays_and_a_blank_day_is_today(monkeypatch):
    step, _ = waiting(monkeypatch, dict(READY_BY, Mode="DateTime"))
    change(step, StartTime="06:30")
    assert step.props["Mode"] == "DateTime" and step.asked_for == READY - timedelta(minutes=30)
    change(step, Mode="", StartTime="23:30")
    assert step.props["Mode"] == "Today" and step.asked_for == NIGHT + timedelta(hours=1, minutes=30)


def test_tomorrow_counts_from_the_change(monkeypatch):
    step, _ = waiting(monkeypatch)
    change(step, Mode="Tomorrow", StartTime="07:30")
    assert step.asked_for == READY + timedelta(minutes=30)


def test_waiting_to_start_at_it_takes_a_new_time(monkeypatch):
    step, _ = waiting(monkeypatch, START_AT)
    change(step, StartTime="05:30")
    assert step.target_start == step.asked_for == REAL_DATETIME(2026, 10, 24, 5, 30)
    assert step.cbpi.said()[0].startswith("Time changed, was Sat Oct 24, 06:00. Brew start scheduled for "
                                          "Sat Oct 24, 05:30")


def test_a_ready_time_too_soon_for_the_heating_starts_it_now(monkeypatch):
    step, _ = waiting(monkeypatch)
    change(step, Mode="Date", StartDate="2026-10-23", StartTime="22:30")
    assert step.arrived is True and step.asked_for == NIGHT + timedelta(minutes=30)
    assert step.heating is True and step.cbpi.kettle.auto("hlt") == (True, 152)


def test_a_restart_waits_for_the_changed_time(monkeypatch):
    step, wall = waiting(monkeypatch)
    change(step, StartTime="06:30")
    wall.at = NIGHT + timedelta(hours=1)
    again = make(step.props)                     # what was saved, a new instance, as after a power cut
    asyncio.run(again.on_start())
    assert again.asked_for == READY - timedelta(minutes=30)


# ----------------------------------------------------------- refused

def test_a_time_that_has_passed_is_refused(monkeypatch):
    step, _ = waiting(monkeypatch)
    said = refused(step, Mode="Today", StartTime="21:00")
    assert said == ("Fri Oct 23, 21:00 has already passed, so BrewStart's time didn't change. For tomorrow morning, "
                    "choose Tomorrow.")
    said = refused(step, Mode="Date", StartDate="2026-10-22", StartTime="07:00")
    assert said.endswith("has already passed, so BrewStart's time didn't change. Choose a later day or time.")


def test_a_time_that_cant_be_read_is_refused(monkeypatch):
    step, _ = waiting(monkeypatch)
    assert refused(step, StartTime="25:99") == ("BrewStart's time didn't change: Invalid start time '25:99' "
                                                "(expected HH:MM)")
    assert refused(step, Mode="Date", StartDate="", StartTime="07:00").startswith("BrewStart's time didn't change")


def test_a_day_it_doesnt_offer_is_refused(monkeypatch):
    step, _ = waiting(monkeypatch)
    assert refused(step, Mode="Daily", StartTime="07:00") == "BrewStart's time didn't change: Unknown mode 'Daily'"


def test_refused_once_its_time_has_come(monkeypatch):
    step, wall = waiting(monkeypatch, dict(START_AT, AutoNext="No"))
    wall.at = step.target_start + timedelta(seconds=1)
    assert asyncio.run(step._tick(wall.at, 100000.0)) is False   # waits for Next Step
    assert step.arrived is True
    assert refused(step, StartTime="08:00").startswith("BrewStart's time has come (")


def test_once_it_heats_a_new_time_only_moves_the_ready_time(monkeypatch):
    """Ready by heating its kettle itself: the heating carries on, as it is."""
    step, wall = waiting(monkeypatch)
    wall.at = step.target_start + timedelta(seconds=1)
    assert asyncio.run(step._tick(wall.at, 100000.0)) is False
    assert step.heating is True and step.cbpi.kettle.auto("hlt") == (True, 152)
    calls = list(step.cbpi.kettle.calls)
    step.cbpi.notes.clear()
    change(step, StartTime="07:30")
    assert step.ready_at == step.asked_for == READY + timedelta(minutes=30)
    assert step.heating is True and step.cbpi.kettle.calls == calls, "the heating was touched"
    assert step.cbpi.notes == [("Time changed, was Sat Oct 24, 07:00. Ready by Sat Oct 24, 07:30: the HLT carries "
                                "on heating to 152.0 °F.", NotificationType.INFO)]
    assert step.cbpi.step.allowed["bs"][0] == (READY + timedelta(hours=2, minutes=30)).timestamp()
    assert step.props[bs.BrewStart.PLANNED_PROP].endswith((READY + timedelta(minutes=30)).isoformat())
    assert step.summary == "Heating to 152.0 °F · ready by Sat Oct 24, 07:30"
    assert refused(step, Mode="Date", StartDate="2026-10-23", StartTime="21:00").startswith(
        "Fri Oct 23, 21:00 has already passed")


def test_refused_while_it_waits_for_next_step(monkeypatch):
    step, _ = waiting(monkeypatch, dict(START_AT, Mode="Date", StartDate="2026-10-23", StartTime="21:00"))
    assert step.target_start is None
    assert refused(step, Mode="Tomorrow", StartTime="07:00") == (
        "BrewStart is waiting for Next Step, so its time can't change now: stop the brew, change it, then start it "
        "again.")


def test_refused_while_it_waits_for_heat_now(monkeypatch):
    step, _ = waiting(monkeypatch, dict(READY_BY, Mode="Date", StartDate="2026-10-23", StartTime="21:00"))
    assert step.target_start is None
    assert refused(step, Mode="Tomorrow", StartTime="07:00") == (
        "BrewStart is waiting for Heat now, so its time can't change now: stop the brew, change it, then start it "
        "again.")


def test_refused_once_it_has_finished(monkeypatch):
    Wall(NIGHT).install(monkeypatch)
    step = make(READY_BY, StepState.DONE)
    assert refused(step, StartTime="06:30") == "BrewStart has finished, so its time can't change now."


# ----------------------------------------------------------- not started

def test_not_started_the_time_is_saved_for_start(monkeypatch):
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    step = make(READY_BY, StepState.INITIAL)
    change(step, Mode="Date", StartDate="2026-10-24", StartTime="06:45")
    assert step.props["StartTime"] == "06:45" and step.cbpi.step.saves == 1 and step.cbpi.notes == []
    asyncio.run(step.on_start())
    assert step.asked_for == READY - timedelta(minutes=15)


def test_not_started_a_passed_time_is_refused_too(monkeypatch):
    Wall(NIGHT).install(monkeypatch)
    step = make(READY_BY, StepState.INITIAL)
    assert refused(step, Mode="Today", StartTime="20:00").startswith("Fri Oct 23, 20:00 has already passed")
