"""The real BrewStart step: it looks at the wall clock, and rebuilds nothing.

Needs CraftBeerPi importable - the step subclasses CBPiStep - and is skipped
without it.

On 12 Oct this fork made the step re-check the wall clock by rebuilding its
Timer whenever the two disagreed. The rebuild ran inside the old Timer's own
update, and a Timer cannot be stopped from inside itself: stop() cancels the
task and then awaits it, the await swallows the cancellation, and the old
countdown runs on, re-detecting the same drift every second. Measured on
23 Oct: one 100 s clock correction gave 24 "System clock changed"
notifications, 8 "start time reached" and a countdown still running after the
step had finished. That version, 6ef8098, is run here as the negative control.
"""

import asyncio
import importlib.util
import subprocess
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cbpi")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cbpi4_BrewStart.brew_start as bs  # noqa: E402
from cbpi.api.step import StepResult  # noqa: E402
from cbpi4_BrewStart.schedule import plan_ready_by  # noqa: E402

CONTROL = "6ef8098"
REAL_DATETIME = datetime


class Kettle:
    def __init__(self, name, sensor):
        self.name = name
        self.sensor = sensor


class Cbpi:
    """The parts of the server the step touches."""

    def __init__(self, unit="F", readings=None, kettles=None):
        self.notes = []
        self.readings = readings or {}
        kettles = kettles or {}
        self.config = types.SimpleNamespace(
            get=lambda k, default=None: unit if k == "TEMP_UNIT" else default)
        self.step = types.SimpleNamespace(push_udpate=lambda: None)
        self.kettle = types.SimpleNamespace(find_by_id=lambda i: kettles.get(i))
        self.sensor = types.SimpleNamespace(
            get_sensor_value=lambda i: self.readings.get(i))

    def notify(self, title, message, *args, **kwargs):
        self.notes.append((message, kwargs.get("action")))

    def said(self, text):
        return [m for m, _ in self.notes if text in m]


def frozen(at):
    class Frozen(REAL_DATETIME):
        @classmethod
        def now(cls, tz=None):
            return at
    return Frozen


def shifted(offset):
    class Shifted(REAL_DATETIME):
        @classmethod
        def now(cls, tz=None):
            return REAL_DATETIME.now() + offset[0]
    return Shifted


def step_with(props, cbpi, done=None, module=bs):
    base = {"Mode": "DateTime", "AutoNext": "Yes", "NotifyBefore": 0,
            "Language": "English"}
    base.update(props)
    on_done = (lambda s, r: done.append(r)) if done is not None else (lambda s, r: None)
    return module.BrewStart(cbpi, "s1", "BrewStart", base, on_done)


NIGHT = REAL_DATETIME(2026, 10, 23, 22, 0, 0)


# ----------------------------------------------------------- the clock

def test_a_clock_change_is_reported_once(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = Cbpi()
    step = step_with({"StartDate": "2026-10-24 06:00"}, cbpi)

    async def go():
        await step.on_start()
        handed_over = []
        for i in range(5):
            handed_over.append(await step._tick(NIGHT + timedelta(seconds=i), 1000.0 + i))
        # NTP steps the wall clock forward two hours; the monotonic clock does not jump.
        for i in range(5, 10):
            handed_over.append(await step._tick(
                NIGHT + timedelta(hours=2, seconds=i), 1000.0 + i))
        return handed_over

    handed_over = asyncio.run(go())
    assert len(cbpi.said("System clock changed")) == 1, cbpi.notes
    assert not any(handed_over)
    assert step.target_start == REAL_DATETIME(2026, 10, 24, 6, 0)


def test_reaching_the_time_hands_over_once(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = Cbpi()
    step = step_with({"StartDate": "2026-10-23 22:00:05"}, cbpi)

    async def go():
        await step.on_start()
        return [await step._tick(NIGHT + timedelta(seconds=i), 50.0 + i) for i in range(10)]

    handed_over = asyncio.run(go())
    assert handed_over[:5] == [False] * 5
    assert handed_over[5] is True
    assert len(cbpi.said("start time reached")) == 1, cbpi.notes


def test_autonext_no_offers_next_once_and_waits(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = Cbpi()
    step = step_with({"StartDate": "2026-10-23 22:00:02", "AutoNext": "No"}, cbpi)

    async def go():
        await step.on_start()
        return [await step._tick(NIGHT + timedelta(seconds=i), 50.0 + i) for i in range(10)]

    assert not any(asyncio.run(go()))
    offers = [m for m, action in cbpi.notes if action]
    assert len(offers) == 1 and "Continue to the next step" in offers[0], cbpi.notes


def test_a_time_already_past_hands_over_without_waiting(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = Cbpi()
    step = step_with({"StartDate": "2026-10-23 21:00"}, cbpi)

    async def go():
        await step.on_start()
        return await step._tick(NIGHT, 1.0)

    assert asyncio.run(go()) is True
    assert len(cbpi.said("already passed")) == 1, cbpi.notes


def test_a_configuration_error_never_starts_by_itself(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = Cbpi()
    step = step_with({"StartDate": "2026-10-24 06:00", "Timing": "Ready by"}, cbpi)

    async def go():
        await step.on_start()
        return [await step._tick(NIGHT + timedelta(hours=h), 1.0 + h * 3600) for h in range(12)]

    assert not any(asyncio.run(go()))
    offers = [m for m, action in cbpi.notes if action]
    assert len(offers) == 1 and "Kettle" in offers[0], cbpi.notes


# ----------------------------------------------------------- ready by

def hlt_and_tun(hlt=64.0, tun=63.0):
    readings = {"hlt-probe": {"value": hlt, "age": 1, "max_age": 30},
                "mt-probe": {"value": tun, "age": 1, "max_age": 30}}
    kettles = {"hlt": Kettle("HLT", "hlt-probe"), "mt": Kettle("Mash Tun", "mt-probe")}
    return Cbpi(unit="F", readings=readings, kettles=kettles)


HERMS = {"StartDate": "2026-10-24 07:00", "Timing": "Ready by",
         "Heat_Kettle": "hlt", "Ready_Temp": 163, "Volume": 15, "Volume_Unit": "gal",
         "Strike_Kettle": "mt", "Strike_Volume": 8, "Heater_Watts": 5346}


def test_ready_by_plans_from_both_probes(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = hlt_and_tun()
    step = step_with(HERMS, cbpi)
    asyncio.run(step.on_start())
    expected = plan_ready_by(REAL_DATETIME(2026, 10, 24, 7, 0), 163, 5346, "F", 15, 64.0,
                             strike_volume=8, strike_temp=63.0, volume_unit="gal")
    assert step.target_start == expected.start
    assert len(cbpi.said("for the strike water")) == 1, cbpi.notes


def test_ready_by_replans_as_the_water_cools(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = hlt_and_tun()
    step = step_with(HERMS, cbpi)

    async def go():
        await step.on_start()
        first = step.target_start
        cbpi.readings["hlt-probe"]["value"] = 55.0
        cbpi.readings["mt-probe"]["value"] = 55.0
        planned = step._planned_at
        await step._tick(NIGHT + timedelta(seconds=30), planned + 30)
        too_soon = step.target_start
        await step._tick(NIGHT + timedelta(seconds=61), planned + 61)
        return first, too_soon, step.target_start

    first, too_soon, later = asyncio.run(go())
    assert too_soon == first, "re-planned before REPLAN_INTERVAL_S"
    assert later < first, "colder water must move the start earlier"
    assert not cbpi.said("System clock changed")


def test_a_stale_reading_is_not_planned_from(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = hlt_and_tun()
    cbpi.readings["hlt-probe"] = {"value": 160.0, "age": 900, "max_age": 30}
    step = step_with(HERMS, cbpi)
    asyncio.run(step.on_start())
    assert step.plan.assumed
    assert step.plan.water_temp < 40
    assert len(cbpi.said("No usable reading from HLT")) == 1, cbpi.notes


def test_campden_reminder_doses_the_strike_water_when_asked(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    quiet = hlt_and_tun()
    asyncio.run(step_with(HERMS, quiet).on_start())
    assert not quiet.said("Campden")

    asked = hlt_and_tun()
    asyncio.run(step_with(dict(HERMS, Campden_Reminder="Yes"), asked).on_start())
    reminders = asked.said("Campden")
    assert len(reminders) == 1 and "½ Campden tablet(s) for 8 gal" in reminders[0], asked.notes


# ----------------------------------------------------------- the real loop

def run_with_clock_correction(module, monkeypatch, seconds):
    """Start the step for real, step the wall clock 118 s forward 0.3 s in."""
    offset = [timedelta(0)]
    monkeypatch.setattr(module, "datetime", shifted(offset))
    if hasattr(module.BrewStart, "TICK_S"):
        monkeypatch.setattr(module.BrewStart, "TICK_S", 0.05)
    cbpi = Cbpi()
    done = []
    target = REAL_DATETIME.now() + timedelta(seconds=120)
    step = step_with({"StartDate": target.strftime("%Y-%m-%d %H:%M:%S")}, cbpi,
                     done=done, module=module)

    async def go():
        await step.start()
        await asyncio.sleep(0.3)
        offset[0] = timedelta(seconds=118)
        await asyncio.sleep(seconds)
        leftovers = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in leftovers:
            task.cancel()
        return len(leftovers)

    leftovers = asyncio.run(go())
    return cbpi, done, leftovers


def test_the_real_loop_survives_a_clock_correction(monkeypatch):
    """And hands over with NEXT - the only result that moves a profile on.

    StepController.done() acts on NEXT and ERROR and ignores DONE. The first
    version of this rewrite returned DONE from run(); this test asserted DONE
    too, passed, and the profile then sat on the finished step on a live
    server. Asserting what the controller acts on is the point.
    """
    cbpi, done, leftovers = run_with_clock_correction(bs, monkeypatch, 3.5)
    assert done == [StepResult.NEXT]
    assert len(cbpi.said("scheduled")) == 1
    assert len(cbpi.said("System clock changed")) == 1, cbpi.notes
    assert len(cbpi.said("start time reached")) == 1, cbpi.notes
    assert leftovers == 0


def load_control():
    try:
        source = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{CONTROL}:cbpi4_BrewStart/brew_start.py"],
            capture_output=True, text=True, encoding="utf-8", check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"git history with {CONTROL} not available")
    path = Path(tempfile.mkdtemp()) / "brew_start_control.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("brew_start_control", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_control_the_timer_rebuild_floods_and_leaks(monkeypatch):
    cbpi, done, leftovers = run_with_clock_correction(load_control(), monkeypatch, 4.5)
    assert len(cbpi.said("System clock changed")) > 1, cbpi.notes
    assert leftovers > 0


def test_control_returning_from_run_does_not_hand_over(monkeypatch):
    """The first rewrite: run() returned, which reports DONE - ignored."""
    async def returning_run(self):
        while self.running is True:
            if await self._tick():
                return StepResult.DONE
            await asyncio.sleep(self.TICK_S)
        return StepResult.DONE

    monkeypatch.setattr(bs.BrewStart, "run", returning_run)
    cbpi, done, leftovers = run_with_clock_correction(bs, monkeypatch, 3.5)
    assert done == [StepResult.DONE], done
