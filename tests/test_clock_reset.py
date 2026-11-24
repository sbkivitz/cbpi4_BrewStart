"""BrewStart 1.3.0.post4: a simulator's change of speed leaves the heating, and the question, alone.

From core 4.7.7.post63 a change of SIM_TIME_SCALE sets CraftBeerPi's clock back to the computer's time
(ClockController.reset). StepController.clock_moved moves the steps' times with it, then replan_after_clock_reset
stops, resets and starts again every BrewStart without a clock_moved of its own, so that it plans again from its
settings. Up to 1.3.0.post3 that took a Ready by heating the HLT for one waiting for its time: Stop switched the HLT's
AUTO off, reset() forgot it was heating and had asked, and Start planned afresh - the water left to cool until a
second heating start, and asked about a second time.

On the real step, with the core's own StepController.clock_moved and replan_after_clock_reset, these check that:
- heating, it carries on: the HLT's AUTO and target untouched, nothing new said;
- holding, its question stays up and isn't asked again;
- its ready time is worked out again from its settings, as START does: Today and Tomorrow counted from the clock as it
  now is, and until when it may carry on after a restart with it; one that has passed is late, and said so, and one
  still to come is "ready by" again after it was late;
- stopped while heating, Start goes back to the heating, as it does without a reset;
- waiting, it plans again from its settings, as CraftBeerPi did;
- the stall watch's window moves with the clock.
All but the last two fail on 1.3.0.post3 (93f5edd).
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cbpi4_BrewStart.brew_start as bs  # noqa: E402
from cbpi.api.dataclasses import NotificationType  # noqa: E402
from cbpi.api.step import StepState  # noqa: E402
from test_heats_its_kettle import NIGHT, READY, READY_BY, Wall, make, settle  # noqa: E402

try:
    from cbpi.controller.step_controller import StepController
except ImportError:  # pragma: no cover - a core without the controller importable here
    StepController = None

needs_reset = pytest.mark.skipif(
    StepController is None or not hasattr(StepController, "replan_after_clock_reset"),
    reason="a core before 4.7.7.post63 never sets its clock back")

HOUR = 3600
DAY = 24 * HOUR


def install(monkeypatch, at):
    """The wall clock the test moves, for BrewStart and for the core's clock both, as they are one clock on a
    simulator: the stall watch (CBPiStep.note_heat_progress) reads cbpi.api.clock.now()."""
    from cbpi.api import clock as core_clock
    wall = Wall(at)
    wall.install(monkeypatch)
    monkeypatch.setattr(core_clock, "now", lambda: wall.at.timestamp())
    return wall


async def clock_reset(server, wall, seconds):
    """What CraftBeerPi does as a simulator's speed changes: its clock jumps `seconds`, the steps' times move with it
    (StepController.clock_moved), then each BrewStart without a clock_moved of its own plans again
    (replan_after_clock_reset) - the core's own code, on the step controller faked as far as they reach."""
    wall.at = wall.at + timedelta(seconds=seconds)
    steps = types.SimpleNamespace(
        profile=server.step.profile, basic_data={}, CLOCK_PROPS=StepController.CLOCK_PROPS,
        BREWSTART_TYPE=StepController.BREWSTART_TYPE, _moved=StepController._moved,
        LOADED_AT_KEY=StepController.LOADED_AT_KEY, MASH_PH_REMINDER_KEY=StepController.MASH_PH_REMINDER_KEY)
    StepController.clock_moved(steps, seconds)
    await StepController.replan_after_clock_reset(steps)
    await asyncio.sleep(0.05)


async def heat(step, server, wall):
    """The real start(), the night before, to its heating start."""
    await step.start()
    await asyncio.sleep(0.03)
    wall.at = step.target_start + timedelta(seconds=1)
    await settle(lambda: server.kettle.auto("hlt") == (True, 152))
    await asyncio.sleep(0.03)


@needs_reset
def test_heating_carries_on_through_a_clock_reset(monkeypatch):
    wall = install(monkeypatch, NIGHT)
    server, step = make()

    async def go():
        await heat(step, server, wall)
        calls, notes = len(server.kettle.calls), len(server.notes)
        await clock_reset(server, wall, -HOUR)
        seen = (server.kettle.calls[calls:], [n.message for n in server.notes[notes:]], step.summary,
                step.props.get(bs.BrewStart.HEATING_PROP), server.kettle.auto("hlt"))
        await step.stop()
        return seen
    calls, said, summary, heating, auto = asyncio.run(go())
    assert calls == [], "the HLT's AUTO switched: the water left to cool"
    assert said == [], "said something new: it was planned afresh"
    assert heating is True and auto == (True, 152)
    assert summary == "Heating to 152.0 °F · ready by Sat Oct 24, 07:00"


@needs_reset
def test_holding_keeps_its_question_through_a_clock_reset(monkeypatch):
    wall = install(monkeypatch, NIGHT)
    server, step = make()

    async def go():
        await heat(step, server, wall)
        server.water(152.0)
        await settle(lambda: server.questions())
        calls = len(server.kettle.calls)
        await clock_reset(server, wall, -HOUR)
        for minutes in (1, 10):
            wall.at += timedelta(minutes=minutes)
            await asyncio.sleep(0.03)
        seen = (server.kettle.calls[calls:], list(server.step.withdrawn), dict(server.step.kept), step.summary,
                step.props.get(bs.BrewStart.ASKED_PROP))
        await step.stop()
        return seen
    calls, withdrawn, kept, summary, asked = asyncio.run(go())
    assert len(server.questions()) == 1, "asked twice for the same water"
    assert calls == [] and withdrawn == [], "the HLT switched, or the question taken down"
    assert ("bs", "water") in kept and asked is True
    assert summary == "Holding 152.0 °F · move the strike water"


@needs_reset
def test_its_ready_time_is_worked_out_again_from_its_settings(monkeypatch):
    """Tomorrow, from Friday night, is Saturday; set back to Thursday morning, Tomorrow is Friday."""
    wall = install(monkeypatch, NIGHT)
    server, step = make(props=dict(READY_BY, Mode="Tomorrow", StartDate=""))

    async def go():
        await heat(step, server, wall)
        assert step.ready_at == READY
        await clock_reset(server, wall, -2 * DAY)
        seen = step.ready_at, step.summary, dict(server.step.allowed), step.props.get(bs.BrewStart.PLANNED_PROP)
        await step.stop()
        return seen
    ready, summary, allowed, planned = asyncio.run(go())
    friday = READY - timedelta(days=1)
    assert ready == friday
    assert summary == "Heating to 152.0 °F · ready by Fri Oct 23, 07:00"
    assert allowed["bs"][0] == (friday + timedelta(hours=2)).timestamp(), "carries on after a restart by the old time"
    assert str(planned).endswith("|" + friday.isoformat()), "a restart would recall the old time"
    assert server.kettle.auto("hlt") == (False, 152)


@needs_reset
def test_a_ready_time_passed_on_the_clock_as_it_now_is_is_late(monkeypatch):
    """Today 07:00 heating at Saturday 06:40; set back to Friday 23:00, Today 07:00 has passed."""
    wall = install(monkeypatch, datetime(2026, 10, 24, 6, 40))
    server, step = make(props=dict(READY_BY, Mode="Today", StartDate=""))

    async def go():
        await step.start()
        await settle(lambda: server.kettle.auto("hlt") == (True, 152))   # too soon for the heating: at once
        server.water(140.0)
        await clock_reset(server, wall, -(7 * HOUR + 40 * 60))
        await settle(lambda: server.said("isn't ready"))
        seen = [(n.message, n.type) for n in server.said("isn't ready")], step.summary, server.kettle.auto("hlt")
        await step.stop()
        return seen
    late, summary, auto = asyncio.run(go())
    assert late == [(
        "The water isn't ready by Fri Oct 23, 07:00: the HLT reads 140.0 °F, short of 152.0 °F. BrewStart carries "
        "on heating it, and asks you to move it once it's there.", NotificationType.WARNING)]
    assert summary == "Heating to 152.0 °F · late for Fri Oct 23, 07:00"
    assert auto == (True, 152)
    assert not server.said("has already passed")


@needs_reset
def test_late_before_a_reset_to_a_later_time_is_ready_by_again(monkeypatch):
    """Late for Saturday 07:00; set back to Thursday, Tomorrow is Friday 07:00, still to come."""
    wall = install(monkeypatch, NIGHT)
    server, step = make(props=dict(READY_BY, Mode="Tomorrow", StartDate=""))

    async def go():
        await heat(step, server, wall)
        server.water(140.0)
        wall.at = READY + timedelta(minutes=1)
        await settle(lambda: server.said("isn't ready"))
        late = step.summary
        await clock_reset(server, wall, -2 * DAY)
        await asyncio.sleep(0.05)
        seen = late, step.summary
        await step.stop()
        return seen
    late, summary = asyncio.run(go())
    assert late == "Heating to 152.0 °F · late for Sat Oct 24, 07:00"
    assert summary == "Heating to 152.0 °F · ready by Fri Oct 23, 07:00"


@needs_reset
def test_stopped_while_heating_start_goes_back_to_the_heating(monkeypatch):
    wall = install(monkeypatch, NIGHT)
    server, step = make()

    async def go():
        await heat(step, server, wall)
        await step.stop()
        server.step.profile[0].status = StepState.STOP
        stopped = server.kettle.auto("hlt")
        await clock_reset(server, wall, -HOUR)
        server.step.profile[0].status = StepState.ACTIVE
        notes = len(server.notes)
        await step.start()
        await settle(lambda: server.kettle.auto("hlt") == (True, 152))
        seen = stopped, [n.message for n in server.notes[notes:]], step.heating
        await step.stop()
        return seen
    stopped, said, heating = asyncio.run(go())
    assert stopped == (False, 152)
    assert heating is True and said == [], "Start waited for its time again, rather than heating"


@needs_reset
def test_waiting_it_plans_again_from_its_settings(monkeypatch):
    """As CraftBeerPi does for a BrewStart without a clock_moved of its own: Tomorrow counted from the clock as it
    now is, nothing heated, and no word of a system clock changed."""
    wall = install(monkeypatch, NIGHT)
    server, step = make(props=dict(READY_BY, Mode="Tomorrow", StartDate=""))

    async def go():
        await step.start()
        await asyncio.sleep(0.03)
        assert step.ready_at == READY
        await clock_reset(server, wall, -2 * DAY)
        await settle(lambda: step.ready_at == READY - timedelta(days=2))
        await asyncio.sleep(0.05)
        seen = step.summary, step.running
        await step.stop()
        return seen
    summary, running = asyncio.run(go())
    assert running is True and summary.startswith("Ready Thu Oct 22, 07:00 · heating from Thu Oct 22, ")
    assert server.kettle.calls == []
    assert not [n for n in server.notes if "clock" in n.message.lower()], [n.message for n in server.notes]


@needs_reset
def test_the_stall_watch_moves_with_the_clock(monkeypatch):
    wall = install(monkeypatch, NIGHT)
    server, step = make()
    step._stall_anchor_value, step._stall_anchor_at = 100.0, 5000.0
    step.clock_moved(-HOUR)
    assert step._stall_anchor_at == 5000.0 - HOUR
