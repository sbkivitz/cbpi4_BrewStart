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

And in a simulation "the wall clock" is the simulation's: the server's one
clock, which runs SIM_TIME_SCALE times as fast. 1.2.0.post2, 4f360ae, read the
PC's clock there - planning on the PC's date and waiting real minutes for a
start a few simulated minutes away - and is the control for that.
"""

import asyncio
import importlib.util
import subprocess
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cbpi")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cbpi4_BrewStart.brew_start as bs  # noqa: E402
from cbpi.api import clock  # noqa: E402
from cbpi.api.step import StepResult  # noqa: E402
from cbpi4_BrewStart.schedule import plan_ready_by  # noqa: E402

CONTROL = "6ef8098"
POST2 = "4f360ae"
REAL_DATETIME = datetime


class Kettle:
    def __init__(self, name, sensor):
        self.name = name
        self.sensor = sensor


class Cbpi:
    """The parts of the server the step touches."""

    def __init__(self, unit="F", readings=None, kettles=None, settings=None):
        self.notes = []
        self.saves = 0
        self.readings = readings or {}
        kettles = kettles or {}
        settings = dict(settings or {}, TEMP_UNIT=unit)
        self.config = types.SimpleNamespace(
            get=lambda k, default=None: settings.get(k, default))

        async def save():
            self.saves += 1

        self.step = types.SimpleNamespace(push_udpate=lambda: None, save=save)
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
    base = {"Mode": "DateTime", "AutoNext": "Yes", "NotifyBefore": 0}
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


def test_a_time_already_past_when_the_brew_starts_waits_for_next(monkeypatch):
    """Until 1.2.0.post4 it handed over at once - and on a Ready by profile the
    next step heats. A time over before the brew was started is a mistake (an
    old date, Today meant for tomorrow), so it waits, whatever AutoNext says."""
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = Cbpi()
    step = step_with({"StartDate": "2026-10-23 21:00"}, cbpi)

    async def go():
        await step.on_start()
        return [await step._tick(NIGHT + timedelta(minutes=m), 1.0 + m * 60)
                for m in range(30)]

    assert not any(asyncio.run(go()))
    offers = [m for m, action in cbpi.notes if action]
    assert len(offers) == 1 and "has already passed, so nothing starts by itself" in offers[0], \
        cbpi.notes
    assert bs.BrewStart.PLANNED_PROP not in step.props, "a moment never acted on is not kept"


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


#: Scott's way (29 Oct): all the strike and sparge water heated together in
#: the HLT. Volume is all of it.
HERMS = {"StartDate": "2026-10-24 07:00", "Timing": "Ready by",
         "Heat_Kettle": "hlt", "Ready_Temp": 163, "Volume": 23, "Volume_Unit": "gal",
         "Heater_Watts": 5346}

#: Settings an older form asked for and saved, which no longer change the plan.
NO_LONGER_ASKED = {"Strike_Kettle": "mt", "Strike_Volume": 8, "Efficiency_Pct": 50,
                   "Margin_Minutes": 60, "Language": "Deutsch"}


def test_ready_by_plans_all_the_water_from_the_hlt(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = hlt_and_tun()
    step = step_with(HERMS, cbpi)
    asyncio.run(step.on_start())
    expected = plan_ready_by(REAL_DATETIME(2026, 10, 24, 7, 0), 163, 5346, "F", 23, 64.0,
                             volume_unit="gal")
    assert step.target_start == expected.start
    assert len(cbpi.said("heating starts about Sat Oct 24")) == 1, cbpi.notes
    assert not cbpi.said("strike water"), cbpi.notes


def test_settings_the_form_no_longer_asks_for_change_nothing(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    plain, older = step_with(HERMS, hlt_and_tun()), step_with(dict(HERMS, **NO_LONGER_ASKED),
                                                              hlt_and_tun())
    asyncio.run(plain.on_start())
    asyncio.run(older.on_start())
    assert older.target_start == plain.target_start
    assert older.language == "en"


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


def test_campden_reminder_comes_when_the_step_is_saved(monkeypatch):
    """Scott, 29 Oct: remind "when setting the BrewStart setting - or just after
    clicking okay/save". The dose is for all the water, and the brew's start
    says nothing more about it."""
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    quiet = hlt_and_tun()
    asyncio.run(step_with(HERMS, quiet).on_props_saved())
    assert not quiet.said("Campden")

    asked = hlt_and_tun()
    step = step_with(dict(HERMS, Campden_Reminder="Yes"), asked)
    asyncio.run(step.on_props_saved())
    reminders = asked.said("Campden")
    assert len(reminders) == 1 and "1¼ tablet(s) for 23 gal of brewing water" in reminders[0], \
        asked.notes

    asyncio.run(step.on_start())
    assert len(asked.said("Campden")) == 1, "the brew's start repeated it"

    blank = hlt_and_tun()
    asyncio.run(step_with(dict(HERMS, Campden_Reminder="Yes", Volume=""), blank)
                .on_props_saved())
    assert len(blank.said("water volume is blank")) == 1, blank.notes


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


def load_control(commit=CONTROL):
    try:
        source = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{commit}:cbpi4_BrewStart/brew_start.py"],
            capture_output=True, text=True, encoding="utf-8", check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"git history with {commit} not available")
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


# ----------------------------------------------------------- the brewing clock
#
# Scott, of the simulator: "60x should give same results 60x faster." A
# simulation runs the server's one clock (cbpi.api.clock) at SIM_TIME_SCALE, and
# the water heats and every other step's timer counts by it.

#: A day on the simulation's clock, weeks from the PC's - as on Scott's 8000 sim
#: after a day at 60x, its charts stamped 18 Dec on 27 Oct.
SIM_DAY = (REAL_DATETIME.now() + timedelta(days=52)).replace(
    hour=17, minute=0, second=0, microsecond=0)
TONIGHT = dict(HERMS, Mode="Today", StartDate="", StartTime="19:00")
AN_HOUR_ON = {"StartDate": (SIM_DAY + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")}


class HandClock(clock.TimeSource):
    """A simulation's clock that the test moves by hand."""

    rate = 60.0

    def __init__(self, at):
        self.t = at.timestamp()
        self.m = 10_000.0

    def now(self):
        return self.t

    def monotonic(self):
        return self.m

    def advance(self, seconds):
        self.t += seconds
        self.m += seconds

    async def sleep(self, seconds):
        await asyncio.sleep(0)


@pytest.fixture
def simulation():
    """Run one test on a simulation's clock, and put the real one back."""
    replaced = []

    def install(source):
        replaced.append(clock.set_source(source))
        return source

    yield install
    if replaced:
        clock.set_source(replaced[0])


def look_for_an_hour(step, sim):
    """Two simulated minutes a look, for an hour and a look more."""
    async def go():
        await step.on_start()
        looks = []
        for _ in range(31):
            sim.advance(120)
            looks.append(await step._tick())
        return looks
    return asyncio.run(go())


def test_a_rig_asks_the_wall_clock_itself(monkeypatch):
    """On a rig the brewing clock is real time, and nothing about the step changes."""
    assert isinstance(clock.get_source(), clock.RealTimeSource)
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    assert bs.brewing_now() == NIGHT
    before = time.monotonic()
    assert before <= bs.brewing_monotonic() <= time.monotonic()
    monkeypatch.setattr(bs, "brewing_clock", None)  # upstream CraftBeerPi has none
    assert bs.brewing_now() == NIGHT


def test_ready_by_is_planned_on_the_simulations_clock(simulation):
    simulation(HandClock(SIM_DAY))
    step = step_with(TONIGHT, hlt_and_tun())
    asyncio.run(step.on_start())
    ready = SIM_DAY.replace(hour=19)
    expected = plan_ready_by(ready, 163, 5346, "F", 23, 64.0, volume_unit="gal")
    assert step.ready_at == ready
    assert step.target_start == expected.start
    assert step.wall_clock_remaining() == int((expected.start - SIM_DAY).total_seconds())


def test_control_post2_plans_on_the_pcs_date(simulation):
    simulation(HandClock(SIM_DAY))
    step = step_with(TONIGHT, hlt_and_tun(), module=load_control(POST2))
    asyncio.run(step.on_start())
    assert step.ready_at.date() != SIM_DAY.date()


def test_the_start_comes_when_the_simulations_clock_reaches_it(simulation):
    sim = simulation(HandClock(SIM_DAY))
    cbpi = Cbpi()
    looks = look_for_an_hour(step_with(AN_HOUR_ON, cbpi), sim)
    assert looks[:29] == [False] * 29
    assert looks[29] is True
    assert len(cbpi.said("start time reached")) == 1, cbpi.notes
    assert not cbpi.said("System clock changed"), cbpi.notes


def test_control_post2_never_reaches_a_simulated_start(simulation):
    sim = simulation(HandClock(SIM_DAY))
    looks = look_for_an_hour(step_with(AN_HOUR_ON, Cbpi(), module=load_control(POST2)), sim)
    assert not any(looks)


def test_control_half_a_clock_reports_a_change_at_every_look(simulation, monkeypatch):
    """The time of day from the simulation, intervals from the PC: a flood."""
    monkeypatch.setattr(bs, "brewing_monotonic", time.monotonic)
    sim = simulation(HandClock(SIM_DAY))
    cbpi = Cbpi()
    look_for_an_hour(step_with(AN_HOUR_ON, cbpi), sim)
    assert len(cbpi.said("System clock changed")) > 1, cbpi.notes


def hand_over_at_60x(module, simulation, seconds=2.5):
    """Start the step for real on a clock running 60x, 90 simulated seconds ahead.

    Returns (result, simulated seconds after the start) for each hand-over.
    """
    sim = simulation(clock.ScaledTimeSource(60))
    start = (REAL_DATETIME.fromtimestamp(sim.now())
             + timedelta(seconds=90)).replace(microsecond=0)
    props = {"Mode": "DateTime", "AutoNext": "Yes", "NotifyBefore": 0,
             "Language": "English", "StartDate": start.strftime("%Y-%m-%d %H:%M:%S")}
    handed = []
    step = module.BrewStart(Cbpi(), "s1", "BrewStart", props,
                            lambda s, r: handed.append((r, sim.now() - start.timestamp())))

    async def go():
        await step.start()
        await asyncio.sleep(seconds)
        before_cleanup = list(handed)  # cancelling a waiting step reports too
        for task in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
            task.cancel()
        return before_cleanup

    return asyncio.run(go())


def test_the_real_loop_lands_on_the_start_at_60x(simulation):
    handed = hand_over_at_60x(bs, simulation)
    assert [r for r, _ in handed] == [StepResult.NEXT], handed
    assert -1.5 <= handed[0][1] <= 5, f"{handed[0][1]:+.1f} simulated s from the start"


def test_control_post2_is_still_waiting_at_60x(simulation):
    assert hand_over_at_60x(load_control(POST2), simulation) == []


def test_control_a_whole_look_late_without_the_short_last_pause(simulation, monkeypatch):
    monkeypatch.setattr(bs.BrewStart, "_pause", lambda self: self.TICK_S)
    handed = hand_over_at_60x(bs, simulation)
    assert handed and handed[0][1] > 20, handed


# ----------------------------------------------- daylight saving, on the real step
#
# US daylight saving ends at 02:00 on Sunday 1 Nov 2026: the clock on the wall
# goes from 01:59:59 back to 01:00:00, and the hour after 01:00 happens twice.
# A brewer setting up on Saturday night for Sunday morning waits across it.
# These drive the real on_start() and _tick() through that night. They
# replace test_wall_clock.py's checks, which ran a copy of
# wall_clock_remaining and compared it with the same subtraction (test-bench
# audit, 29 Oct, finding 7).

SAT_NIGHT = REAL_DATETIME(2026, 10, 31, 22, 0, 0)
SUN_SIX = REAL_DATETIME(2026, 11, 1, 6, 0, 0)


class WallClock:
    """What datetime.now() and time.monotonic() say on a Pi in Pacific time.

    `elapsed` is real seconds since `start`. The wall clock follows it until
    `fall_back_after` (02:00 PDT, four hours after 22:00), when it reads an
    hour less - 01:00 PST - and carries on from there; `jump`, when given, is
    added from `jump_after` on instead, for a clock stepped by hand or by NTP.
    Monotonic time never repeats and never jumps.
    """

    def __init__(self, start=SAT_NIGHT, fall_back_after=4 * 3600, jump=0, jump_after=None):
        self.start, self.fall_back_after = start, fall_back_after
        self.jump, self.jump_after = jump, jump_after
        self.elapsed = 0.0

    def now(self):
        shown = self.elapsed
        if self.fall_back_after is not None and self.elapsed >= self.fall_back_after:
            shown -= 3600
        if self.jump_after is not None and self.elapsed >= self.jump_after:
            shown += self.jump
        return self.start + timedelta(seconds=shown)

    def monotonic(self):
        return 5000.0 + self.elapsed


def on_the_wall(monkeypatch, wall, module=bs):
    monkeypatch.setattr(module, "brewing_now", wall.now)
    monkeypatch.setattr(module, "brewing_monotonic", wall.monotonic)


def through_the_night(step, wall, every=30, until=10 * 3600):
    """The real on_start(), then the real _tick() every `every` real seconds.

    Returns (real seconds, the wall clock) at the first hand-over, or
    (None, None).
    """
    async def go():
        await step.on_start()
        while wall.elapsed <= until:
            if await step._tick():
                return wall.elapsed, wall.now()
            wall.elapsed += every
        return None, None
    return asyncio.run(go())


def test_start_at_six_tomorrow_across_the_fall_back_starts_at_six_on_the_wall(monkeypatch):
    wall = WallClock()
    on_the_wall(monkeypatch, wall)
    cbpi = Cbpi()
    step = step_with({"Mode": "Tomorrow", "StartTime": "06:00", "Timing": "Start at"}, cbpi)
    real, shown = through_the_night(step, wall)
    assert shown is not None, cbpi.notes
    assert SUN_SIX <= shown < SUN_SIX + timedelta(seconds=30), shown
    # Nine real hours from 22:00 PDT to 06:00 PST - not the eight on the face.
    assert 9 * 3600 <= real < 9 * 3600 + 30, real / 3600
    assert step.target_start == SUN_SIX
    told = cbpi.said("System clock changed")
    assert len(told) == 1 and "06:00" in told[0], cbpi.notes


def test_ready_by_six_tomorrow_across_the_fall_back_heats_by_the_wall(monkeypatch):
    wall = WallClock()
    on_the_wall(monkeypatch, wall)
    cbpi = hlt_and_tun(hlt=60.0)
    props = {k: v for k, v in HERMS.items() if k != "StartDate"}
    props.update(Mode="Tomorrow", StartTime="06:00", Ready_Temp=152, Volume=15)
    step = step_with(props, cbpi)
    real, shown = through_the_night(step, wall)
    plan = plan_ready_by(SUN_SIX, 152, 5346, "F", 15, 60.0, volume_unit="gal")
    assert plan.start > SAT_NIGHT + timedelta(hours=4)    # after the repeated hour
    assert shown is not None, cbpi.notes
    assert plan.start <= shown < plan.start + timedelta(seconds=30), (shown, plan.start)
    # The real time it took to get there includes the repeated hour.
    assert abs(real - ((plan.start - SAT_NIGHT).total_seconds() + 3600)) < 30
    assert step.ready_at == SUN_SIX


@pytest.mark.parametrize("jump, reported", [
    (0, False), (1, False), (30, False), (59, False), (60, False), (-59, False),
    (61, True), (-61, True), (3600, True), (-3600, True)])
def test_only_a_clock_moved_more_than_a_minute_is_reported(monkeypatch, jump, reported):
    wall = WallClock(start=NIGHT, fall_back_after=None, jump=jump, jump_after=10)
    on_the_wall(monkeypatch, wall)
    cbpi = Cbpi()
    step = step_with({"StartDate": "2026-10-24 06:00"}, cbpi)
    real, shown = through_the_night(step, wall, every=1, until=20)
    assert shown is None
    assert len(cbpi.said("System clock changed")) == (1 if reported else 0), cbpi.notes
    assert step.target_start == REAL_DATETIME(2026, 10, 24, 6, 0)


def test_a_clock_stepped_past_the_start_hands_over_once(monkeypatch):
    wall = WallClock(start=NIGHT, fall_back_after=None, jump=9 * 3600, jump_after=10)
    on_the_wall(monkeypatch, wall)
    cbpi = Cbpi()
    step = step_with({"StartDate": "2026-10-24 06:00"}, cbpi)
    real, shown = through_the_night(step, wall, every=1, until=20)
    assert real == 10, real
    assert len(cbpi.said("start time reached")) == 1, cbpi.notes


#: A step that follows a clock change with its target - the arithmetic of a
#: countdown - which a fall-back would make start an hour early by the wall.
FOLLOWS_THE_CLOCK = ("        remaining = max(0, self.wall_clock_remaining(now))\n",
                     "        self.target_start += timedelta(seconds=jump)\n"
                     "        remaining = max(0, self.wall_clock_remaining(now))\n")


def load_mutant(old, new):
    """brew_start.py with exactly one change, from a copy - the source is never written."""
    source = (ROOT / "cbpi4_BrewStart" / "brew_start.py").read_text(encoding="utf-8")
    assert source.count(old) == 1, "the mutation must match exactly once"
    path = Path(tempfile.mkdtemp()) / "brew_start_mutant.py"
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("brew_start_mutant", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_control_a_step_that_follows_the_clock_back_starts_an_hour_early(monkeypatch):
    mutant = load_mutant(*FOLLOWS_THE_CLOCK)
    wall = WallClock()
    on_the_wall(monkeypatch, wall, module=mutant)
    step = step_with({"Mode": "Tomorrow", "StartTime": "06:00", "Timing": "Start at"},
                     Cbpi(), module=mutant)
    real, shown = through_the_night(step, wall)
    assert shown is not None and shown < SUN_SIX - timedelta(minutes=30), shown
