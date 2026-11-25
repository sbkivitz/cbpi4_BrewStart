"""BrewStart heats the strike water and holds it until the brewer's Next - and never moves on by itself.

Start at heats Heat_Kettle from the time given; Ready by early enough for the water to be hot by it; a Timing left
blank is Ready by. Either way BrewStart holds the water at Ready_Temp, asks for it to be moved and the hoses and pumps
connected, and only that Next (WaterMoved, or the screens' NEXT) starts the next step - MashIn on the Mash Tun, whose
logic runs the wort pump. Here, on the real step with the server faked as far as the step reaches into it, through a
whole night and morning at 1x and 60x on a simulation's clock:

- with Timing Start at, blank or Ready by; AutoNext Yes, No or not saved; a time ahead, or one already passed when
  the brew starts (nothing heats until Heat now);
- after a restart while it heats, and after a power cut while it holds;
- after change_settings and change_time, waiting and heating;

the step never hands over, the server is never asked to start the next step, BrewStart switches no actor at all and
no kettle but Heat_Kettle; Start at begins heating at its time, never before; Ready by begins early enough. Then the
brewer's Next moves it on, once, with Heat_Kettle's AUTO off first.
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
from cbpi.api import clock  # noqa: E402
from cbpi.api.step import StepResult  # noqa: E402
from test_heats_its_kettle import READY_BY, Server, Wall, make, settle  # noqa: E402

NIGHT = datetime(2026, 10, 23, 22, 0, 0)
READY = datetime(2026, 10, 24, 7, 0, 0)
ASK = "Hot at 152.0 \u00b0F. Move it to the Mash Tun, connect the hoses, then NEXT."


class HandClock(clock.TimeSource):
    """The computer's clock, moved by hand: what the simulation's clock runs from."""

    def __init__(self, at):
        self.t = at.timestamp()
        self.m = 50_000.0

    def now(self):
        return self.t

    def monotonic(self):
        return self.m

    def advance(self, seconds):
        self.t += seconds
        self.m += seconds

    async def sleep(self, seconds):
        await asyncio.sleep(0)


class Night:
    """A brew night on a simulation's clock at `rate`: brewing(minutes) moves it on by that much brewing time."""

    def __init__(self, rate):
        self.rate = rate
        self.hand = HandClock(NIGHT)
        self.replaced = clock.set_source(clock.ScaledTimeSource(rate, reference=self.hand))

    def brewing(self, minutes):
        self.hand.advance(minutes * 60 / self.rate)

    def now(self):
        return bs.brewing_now()

    def close(self):
        clock.set_source(self.replaced)


@pytest.fixture(params=[1.0, 60.0], ids=["1x", "60x"])
def night(request):
    n = Night(request.param)
    yield n
    n.close()


def props_for(timing, auto_next, when):
    props = dict(READY_BY)
    if timing is None:
        props.pop("Timing", None)
    else:
        props["Timing"] = timing
    if auto_next is not None:
        props["AutoNext"] = auto_next
    if when == "passed":
        props.update(StartDate="2026-10-23", StartTime="21:00")
    return props


def started(props, server=None):
    server = server or Server(hlt=60.0)
    server, step = make(props=props, server=server)
    asyncio.run(step.on_start())
    step.running = True                           # as CBPiStep.start() leaves it
    return server, step


def look(step, night, minutes, every=5):
    """The step's own look at the clock, every `every` brewing minutes: what each said about handing over."""
    async def go():
        said = []
        for _ in range(int(minutes / every)):
            night.brewing(every)
            said.append(await step._tick())
        return said
    return asyncio.run(go())


def nothing_moved_on(server, step, handed):
    assert not any(handed), "BrewStart handed over by itself"
    assert getattr(step, "_handover", None) is None and step.cancel_reason != StepResult.NEXT
    assert server.actor.calls == [], "BrewStart switched an actor"
    assert {call[1] for call in server.kettle.calls} <= {"hlt"}, server.kettle.calls
    assert server.kettle.auto("mt") == (False, 0), "the Mash Tun's logic, and its wort pump, came on"


def heat_and_hold(server, step, night):
    """From the time asked for: heating, then the water at Ready_Temp, held and asked about, for hours."""
    handed = look(step, night, 12 * 60)
    if getattr(step, "waits_for_heat_now", False):
        assert server.kettle.calls == [], "a time already passed heated the water by itself"
        asyncio.run(step.HeatNow())               # the brewer, there, presses Heat now
    assert step.heating and server.kettle.auto("hlt") == (True, 152.0)
    server.water(152.0)
    handed += look(step, night, 6 * 60)
    assert [n.message for n in server.questions() if n.message == ASK] == [ASK], [n.message for n in server.notes]
    return handed


@pytest.mark.parametrize("when", ["ahead", "passed"])
@pytest.mark.parametrize("auto_next", ["Yes", "No", None])
@pytest.mark.parametrize("timing", ["Start at", "", None, "Ready by"])
def test_it_heats_and_holds_and_never_moves_on(night, timing, auto_next, when):
    server, step = started(props_for(timing, auto_next, when))
    if when == "ahead":
        assert step.target_start is not None and not step.heating
    handed = heat_and_hold(server, step, night)
    nothing_moved_on(server, step, handed)


@pytest.mark.parametrize("timing", ["Start at", "Ready by"])
def test_start_at_heats_from_its_time_and_ready_by_early_enough(night, timing):
    server, step = started(props_for(timing, "Yes", "ahead"))
    began = None
    while began is None and night.now() < READY + timedelta(hours=1):
        night.brewing(1)
        assert asyncio.run(step._tick()) is False
        if step.heating:
            began = night.now()
    if timing == "Start at":
        assert READY <= began < READY + timedelta(minutes=2), began
    else:
        needed = timedelta(minutes=step.plan.heat_minutes)
        assert began <= READY - needed, (began, needed)


@pytest.mark.parametrize("timing", ["Start at", "Ready by"])
def test_after_a_restart_while_heating_and_a_power_cut_while_holding_it_never_moves_on(night, timing):
    server, step = started(props_for(timing, "Yes", "ahead"))
    handed = look(step, night, 9 * 60 + 30)
    assert step.heating
    _, again = make(props=dict(step.props), server=server)    # restarted while heating
    asyncio.run(again.on_start())
    again.running = True
    assert again.heating
    server.water(152.0)
    handed += look(again, night, 30)
    assert again.asked
    _, after_cut = make(props=dict(again.props), server=server)    # the power back while it holds
    asyncio.run(after_cut.on_start())
    after_cut.running = True
    handed += look(after_cut, night, 4 * 60)
    assert after_cut.heating and after_cut.asked
    nothing_moved_on(server, after_cut, handed)


@pytest.mark.parametrize("timing", ["Start at", "Ready by"])
def test_after_change_settings_and_change_time_it_never_moves_on(night, timing):
    server, step = started(props_for(timing, "Yes", "ahead"))
    handed = look(step, night, 60)
    asyncio.run(step.change_settings(Volume=18, AutoNext="Yes", Timing=timing))
    asyncio.run(step.change_time(Mode="Date", StartDate="2026-10-24", StartTime="07:30"))
    handed += look(step, night, 8 * 60 + 45)   # 07:45
    assert step.heating
    asyncio.run(step.change_settings(Ready_Temp=150, Timing="Ready by" if timing == "Start at" else "Start at"))
    asyncio.run(step.change_time(Mode="Date", StartDate="2026-10-24", StartTime="09:00"))
    server.water(150.0)
    handed += look(step, night, 6 * 60)
    assert step.asked
    nothing_moved_on(server, step, handed)


@pytest.mark.parametrize("timing", ["Start at", "Ready by", ""])
def test_only_the_brewers_next_moves_it_on_with_the_hlt_off_first(monkeypatch, timing):
    """The real run loop, through the night to hours after the time, then the question's Next."""
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    done = []
    server, step = make(props=props_for(timing, "Yes", "ahead"), done=done)
    step._done_callback = lambda s, result: done.append((result, server.kettle.auto("hlt")[0]))

    async def go():
        await step.start()
        await asyncio.sleep(0.03)
        wall.at = READY + timedelta(seconds=1)
        await settle(lambda: server.kettle.auto("hlt") == (True, 152))
        server.water(152.0)
        await settle(lambda: server.questions())
        for later in (30, 120, 600):
            wall.at = READY + timedelta(minutes=later)
            await asyncio.sleep(0.03)
        before = list(done)
        question = server.questions()[-1]
        await question.action[0].method()
        await settle(lambda: done)
        return before, question

    before, question = asyncio.run(go())
    assert before == [], "it moved on by itself"
    assert (question.message, question.labels()) == (ASK, ["Next Step"])
    assert done == [(StepResult.NEXT, False)], "Next didn't move it on, or the HLT's AUTO was still on"
    assert server.actor.calls == []
