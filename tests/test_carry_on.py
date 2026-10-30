"""BrewStart after a power cut: it carries on by itself, within its promise.

Scott, 30 Oct: "BrewStart should resume after an overnight power cut and
attempt to honor the contract to have hot water ready for me."

From cbpi4 4.7.7.post21 the server carries a step that was running when it
stopped on by itself only if that step was allowed to, until a time
(StepController.allow_resume); everything else still asks "Resume brew?".
These check what BrewStart allows, and for which step:

- itself, from when it starts waiting, until RESUME_GRACE (two hours) after
  the time it was asked for - the ready time, or the start time;
- the step it hands over to - the HLT's heating - whether the time came or
  Next Step was pressed, and the one the server will really start;
- nothing when its settings are wrong or the time had already passed, and
  nothing that breaks a server from before 4.7.7.post21;
- and that past RESUME_GRACE it never starts heating by itself, however it
  got there: a Pi has no battery clock, so after a long cut it can resume the
  wait thinking it is hours earlier, and be corrected while it waits.

Controls: a copy of the step with each part taken out, which must fail the
check for that part.
"""

import asyncio
import importlib.util
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
from cbpi.api.step import StepResult, StepState  # noqa: E402

REAL_DATETIME = datetime
NIGHT = REAL_DATETIME(2026, 10, 23, 22, 0, 0)          # Friday night
READY = REAL_DATETIME(2026, 10, 24, 7, 0, 0)            # hot water by Saturday 07:00
NAME = "Hot water by Ready by"


class Kettle:
    def __init__(self, name, sensor):
        self.name = name
        self.sensor = sensor


class Steps:
    """The server's step controller, as far as BrewStart reaches into it."""

    def __init__(self, profile):
        self.profile = profile
        self.allowed = {}

    async def save(self):
        pass

    def push_udpate(self):
        pass

    async def allow_resume(self, step_id, until, by=None):
        self.allowed[step_id] = (until, by)
        return True


class OldSteps:
    """A server from before 4.7.7.post21: no allow_resume."""

    def __init__(self, profile):
        self.profile = profile

    async def save(self):
        pass

    def push_udpate(self):
        pass


class Cbpi:
    def __init__(self, steps):
        self.notes = []
        self.step = steps
        settings = {"TEMP_UNIT": "F"}
        self.config = types.SimpleNamespace(get=lambda k, default=None: settings.get(k, default))
        kettles = {"hlt": Kettle("HLT", "hlt-probe")}
        readings = {"hlt-probe": {"value": 60.0, "age": 1, "max_age": 30}}
        self.kettle = types.SimpleNamespace(find_by_id=lambda i: kettles.get(i))
        self.sensor = types.SimpleNamespace(get_sensor_value=lambda i: readings.get(i))

    def notify(self, title, message, *args, **kwargs):
        self.notes.append((message, kwargs.get("action")))

    def offers(self):
        return [m for m, action in self.notes if action]


class Wall:
    """The wall clock, which the test moves."""

    def __init__(self, at):
        self.at = at

    def install(self, monkeypatch, module):
        wall = self

        class Moved(REAL_DATETIME):
            @classmethod
            def now(cls, tz=None):
                return wall.at
        monkeypatch.setattr(module, "datetime", Moved)


def item(step_id, status):
    return types.SimpleNamespace(id=step_id, name=step_id, status=status)


def profile():
    """Rig page 9b: BrewStart, the HLT's heating, the Mash Tun, the hold."""
    return [item("bs", StepState.ACTIVE), item("hlt-heat", StepState.INITIAL),
            item("mash-in", StepState.INITIAL), item("hold", StepState.INITIAL)]


READY_BY = {"Mode": "DateTime", "StartDate": "2026-10-24 07:00", "Timing": "Ready by",
            "Heat_Kettle": "hlt", "Ready_Temp": 152, "Volume": 15, "Volume_Unit": "gal",
            "Heater_Watts": 5346, "NotifyBefore": 0}
START_AT = {"Mode": "DateTime", "StartDate": "2026-10-24 06:00", "Timing": "Start at",
            "AutoNext": "Yes", "NotifyBefore": 0}


def make(props, steps=None, module=bs, done=None):
    steps = steps if steps is not None else Steps(profile())
    on_done = (lambda s, r: done.append(r)) if done is not None else (lambda s, r: None)
    step = module.BrewStart(Cbpi(steps), "bs", NAME, dict(props), on_done)
    step.TICK_S = 0.01
    return step


def two_hours_after(moment):
    return (moment + timedelta(hours=2)).timestamp()


# ------------------------------------------------------------ scenarios
# Observations, so a control can run the same thing on a copy of the step.

def scenario_starts_waiting(monkeypatch, props, module=bs):
    Wall(NIGHT).install(monkeypatch, module)
    step = make(props, module=module)
    asyncio.run(step.on_start())
    return step


def scenario_hands_over_when_the_time_comes(monkeypatch, module=bs):
    """The real start(): on_start, then run() until the planned start."""
    wall = Wall(NIGHT)
    wall.install(monkeypatch, module)
    done = []
    step = make(READY_BY, module=module, done=done)

    async def go():
        await step.start()
        await asyncio.sleep(0.05)               # waiting, overnight
        wall.at = step.target_start + timedelta(seconds=1)
        for _ in range(300):
            await asyncio.sleep(0.01)
            if done:
                return
    asyncio.run(go())
    return step, done


def scenario_corrected_late(monkeypatch, module=bs):
    """Resumed waiting, then the clock is corrected to after the promise."""
    wall = Wall(NIGHT)
    wall.install(monkeypatch, module)
    step = make(READY_BY, module=module)

    async def go():
        await step.on_start()
        wall.at = READY + timedelta(hours=2, minutes=1)
        return [await step._tick(wall.at + timedelta(seconds=s), 1000.0 + s)
                for s in range(5)]
    return step, asyncio.run(go())


def scenario_restarted_at(monkeypatch, at, module=bs):
    """Started the night before; the server restarts at `at` and the step starts
    again from what it kept, as after a power cut."""
    wall = Wall(NIGHT)
    wall.install(monkeypatch, module)
    first = make(READY_BY, module=module)
    asyncio.run(first.on_start())
    wall.at = at
    again = make(first.props, module=module)    # what was saved, a new instance
    asyncio.run(again.on_start())
    return again


# ----------------------------------------------------------- allowing

def test_ready_by_allows_itself_until_two_hours_after_the_ready_time(monkeypatch):
    step = scenario_starts_waiting(monkeypatch, READY_BY)
    assert step.cbpi.step.allowed == {"bs": (two_hours_after(READY), NAME)}


def test_start_at_allows_itself_until_two_hours_after_the_start_time(monkeypatch):
    step = scenario_starts_waiting(monkeypatch, START_AT)
    six = REAL_DATETIME(2026, 10, 24, 6, 0, 0)
    assert step.cbpi.step.allowed == {"bs": (two_hours_after(six), NAME)}


def test_the_step_it_hands_over_to_is_allowed_when_the_time_comes(monkeypatch):
    step, done = scenario_hands_over_when_the_time_comes(monkeypatch)
    assert done == [StepResult.NEXT], done
    assert step.cbpi.step.allowed.get("hlt-heat") == (two_hours_after(READY), NAME), \
        step.cbpi.step.allowed


def test_next_step_pressed_by_hand_allows_the_next_step_too(monkeypatch):
    step = scenario_starts_waiting(monkeypatch, READY_BY)
    asyncio.run(step.NextStep())
    assert step.cbpi.step.allowed.get("hlt-heat") == (two_hours_after(READY), NAME)


def test_the_step_allowed_is_the_one_the_server_will_start(monkeypatch):
    Wall(NIGHT).install(monkeypatch, bs)
    for statuses, expected in (
            ([StepState.DONE, StepState.STOP, StepState.INITIAL], "b"),
            ([StepState.DONE, StepState.INITIAL, StepState.INITIAL], "b"),
            ([StepState.DONE, StepState.DONE, StepState.INITIAL], "c")):
        steps = Steps([item("bs", StepState.ACTIVE)]
                      + [item(n, s) for n, s in zip("abc", statuses)])
        step = make(READY_BY, steps=steps)
        asyncio.run(step.on_start())
        asyncio.run(step.NextStep())
        assert set(steps.allowed) == {"bs", expected}, (statuses, steps.allowed)


def test_the_last_step_hands_over_to_nothing_without_complaint(monkeypatch):
    Wall(NIGHT).install(monkeypatch, bs)
    steps = Steps([item("bs", StepState.ACTIVE)])
    step = make(READY_BY, steps=steps)
    asyncio.run(step.on_start())
    asyncio.run(step.NextStep())
    assert set(steps.allowed) == {"bs"}


def test_nothing_is_allowed_when_the_settings_are_wrong(monkeypatch):
    step = scenario_starts_waiting(monkeypatch, dict(READY_BY, Heat_Kettle=""))
    assert step.cbpi.step.allowed == {}
    asyncio.run(step.NextStep())
    assert step.cbpi.step.allowed == {}, "no time was ever asked for"


def test_nothing_is_allowed_when_the_time_had_already_passed(monkeypatch):
    step = scenario_starts_waiting(monkeypatch, dict(START_AT, StartDate="2026-10-23 21:00"))
    assert step.cbpi.step.allowed == {}
    assert len(step.cbpi.offers()) == 1, step.cbpi.notes


def test_a_server_that_cannot_allow_it_is_no_problem(monkeypatch):
    Wall(NIGHT).install(monkeypatch, bs)
    step = make(READY_BY, steps=OldSteps(profile()))
    asyncio.run(step.on_start())
    assert step.target_start is not None and not step.arrived
    asyncio.run(step.NextStep())


# ------------------------------------------------- never too late by itself

def test_past_two_hours_it_never_starts_heating_by_itself(monkeypatch):
    step, handed = scenario_corrected_late(monkeypatch)
    assert not any(handed), handed
    offers = step.cbpi.offers()
    assert len(offers) == 1 and "more than 2 hours after" in offers[0], step.cbpi.notes
    assert "Long past" in step.summary, step.summary


def test_restarted_within_two_hours_of_ready_it_heats_at_once(monkeypatch):
    step = scenario_restarted_at(monkeypatch, READY - timedelta(minutes=20))
    assert step.arrived and step.auto_next, step.cbpi.notes
    assert step.cbpi.step.allowed.get("bs") == (two_hours_after(READY), NAME)


def test_restarted_more_than_two_hours_late_it_waits_for_next(monkeypatch):
    step = scenario_restarted_at(monkeypatch, READY + timedelta(hours=2, minutes=30))
    assert not step.arrived
    assert any("more than 2 hours after" in m for m in step.cbpi.offers()), step.cbpi.notes
    assert "bs" not in step.cbpi.step.allowed, "nothing to allow - the promise has lapsed"


# --------------------------------------------------------------- controls

def mutant(old, new):
    """brew_start.py with exactly one change, from a copy - never written back."""
    source = (ROOT / "cbpi4_BrewStart" / "brew_start.py").read_text(encoding="utf-8")
    assert source.count(old) == 1, "the mutation must match exactly once"
    path = Path(tempfile.mkdtemp()) / "brew_start_carry_on_mutant.py"
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("brew_start_carry_on_mutant", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_control_a_step_that_never_allows_itself(monkeypatch):
    m = mutant("        await self._allow_resume(self.id)\n", "")
    step = scenario_starts_waiting(monkeypatch, READY_BY, module=m)
    assert step.cbpi.step.allowed == {}


def test_control_a_hand_over_that_does_not_pass_it_on(monkeypatch):
    m = mutant("            await self._allow_resume(following.id)\n", "            pass\n")
    step, done = scenario_hands_over_when_the_time_comes(monkeypatch, module=m)
    assert done == [StepResult.NEXT] and "hlt-heat" not in step.cbpi.step.allowed


def test_control_a_run_loop_that_hands_over_without_it(monkeypatch):
    m = mutant("asyncio.create_task(self._hand_over())", "asyncio.create_task(self.next())")
    step, done = scenario_hands_over_when_the_time_comes(monkeypatch, module=m)
    assert done == [StepResult.NEXT] and "hlt-heat" not in step.cbpi.step.allowed


def test_control_a_wait_that_heats_however_late(monkeypatch):
    m = mutant("            if self._past_carrying_on(now):\n"
               "                await self._too_late(now)\n"
               "                return False\n", "")
    step, handed = scenario_corrected_late(monkeypatch, module=m)
    assert any(handed), "the copy without the rule hands over two hours late"


def test_control_a_restart_that_heats_however_late(monkeypatch):
    m = mutant("            if self._past_carrying_on(now):\n"
               "                await self._too_late(now)\n"
               "                return\n", "")
    step = scenario_restarted_at(monkeypatch, READY + timedelta(hours=2, minutes=30), module=m)
    assert step.arrived, step.cbpi.notes
