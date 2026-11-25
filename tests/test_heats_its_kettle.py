"""BrewStart 1.3.0.post1: Ready by heats the HLT itself, holds the water and asks for it to be moved.

The brewer's flow: all the strike and sparge water heated in the HLT; once it is hot, CraftBeerPi holds it and asks
him to move the strike water to the Mash Tun; only his Next starts the Mash Tun's step, and its pump. Up to
1.2.0.post10 a Ready by BrewStart handed over by itself at the heating start. Before a MashIn on the Mash Tun - Test
Recipe v5 - that started the Mash Tun's step at once: its pump on in an empty tun, the HLT heated to the mash-in
temperature instead of BrewStart's Ready_Temp.

On the real step, with the server faked as far as the step reaches into it, these check that:

- at the heating start Heat_Kettle's target is Ready_Temp and its AUTO on, and the step stays: it never moves on by
  itself, and never switches an actor, the Mash Tun's pump included;
- a ready time too soon for the heating heats at once;
- once the water reads Ready_Temp it holds it and asks, once: the step's name, "Move the strike water to the Mash
  Tun, then press Next", one action, Next Step, kept until answered;
- Next - the question's, or the screen's - switches Heat_Kettle's AUTO off before the next step starts, and so does
  Stop;
- the ready time coming first is said once, with the reading, and the heating carries on;
- a stall warns, as MashInStep's heat watch does;
- after a restart it carries on heating, or holding, and never asks twice for the same water;
- a recipe made for an older BrewStart, with a strike water step on the HLT next, is handed over to as before: no
  heating of BrewStart's own, and no question;
- the strike water helper's USE and BACK TO reach it (change_target, forget_wait);
- a time already passed offers Heat now, not Next Step;
- the status lines while heating or holding never start with "Start" nor say "heating from": Brew Day reads a
  waiting step's heating start from those words.

Each fails on 1.2.0.post10 (fd78e9e).
"""

import asyncio
import re
import sys
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cbpi")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cbpi4_BrewStart.brew_start as bs  # noqa: E402
from cbpi.api.dataclasses import NotificationType  # noqa: E402
from cbpi.api.step import StepResult, StepState  # noqa: E402
from cbpi4_BrewStart.strings import MESSAGES  # noqa: E402
from test_step_clock import Kettle, Kettles  # noqa: E402

REAL_DATETIME = datetime
NIGHT = REAL_DATETIME(2026, 10, 23, 22, 0, 0)          # Friday night
READY = REAL_DATETIME(2026, 10, 24, 7, 0, 0)            # hot water by Saturday 07:00
NAME = "BrewStart"

READY_BY = {"Mode": "Date", "StartDate": "2026-10-24", "StartTime": "07:00", "Timing": "Ready by",
            "Heat_Kettle": "hlt", "Ready_Temp": 152, "Volume": 15, "Volume_Unit": "gal",
            "Heater_Watts": 5346, "NotifyBefore": 0}

#: Brew Day's reading of a waiting BrewStart's heating start (screen.js), which the status lines
#: while heating or holding must never give.
STARTS = re.compile(r"(\d{1,2}:\d{2})\s*·")


class Note:
    def __init__(self, title, message, type, action, keep):
        self.title, self.message, self.type, self.action, self.keep = title, message, type, action or [], keep

    def labels(self):
        return [a.label for a in self.action]


class Actors:
    """Any call on the actor controller, recorded: BrewStart must make none."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.calls.append((name, args))
        return call


class Steps:
    """The server's step controller, as far as BrewStart reaches into it."""

    def __init__(self, profile):
        self.profile = profile
        self.allowed = {}
        self.kept = {}
        self.withdrawn = []
        self.saves = 0

    def find_by_id(self, step_id):
        return next((s for s in self.profile if s.id == step_id), None)

    async def save(self):
        self.saves += 1

    def push_udpate(self):
        pass

    async def allow_resume(self, step_id, until, by=None):
        self.allowed[step_id] = (until, by)
        return True

    def keep_step_prompt(self, step_id, kind, notification_id, while_paused=False):
        self.kept[(step_id, kind)] = notification_id

    def withdraw_step_prompt(self, step_id, kind):
        self.withdrawn.append((step_id, kind))
        self.kept.pop((step_id, kind), None)

    async def stop(self):
        pass


class Server:
    def __init__(self, hlt=60.0):
        self.notes = []
        self.readings = {"hlt-probe": {"value": hlt, "age": 1, "max_age": 30},
                         "mt-probe": {"value": 64.0, "age": 1, "max_age": 30}}
        self.kettle = Kettles({"hlt": Kettle("HLT", "hlt-probe"), "mt": Kettle("Mash Tun", "mt-probe")})
        self.sensor = types.SimpleNamespace(get_sensor_value=lambda i: self.readings.get(i))
        settings = {"TEMP_UNIT": "F"}
        self.config = types.SimpleNamespace(get=lambda k, default=None: settings.get(k, default))
        self.actor = Actors()
        self.step = None

    def notify(self, title, message, type=None, action=None, keep=False, **kwargs):
        self.notes.append(Note(title, message, type, action, keep))
        return "n{}".format(len(self.notes))

    def water(self, value):
        self.readings["hlt-probe"]["value"] = value

    def questions(self):
        return [n for n in self.notes if n.action]

    def said(self, text):
        return [n for n in self.notes if text in n.message]


class Wall:
    """The wall clock, which the test moves."""

    def __init__(self, at):
        self.at = at

    def install(self, monkeypatch, module=bs):
        wall = self

        class Moved(REAL_DATETIME):
            @classmethod
            def now(cls, tz=None):
                return wall.at
        monkeypatch.setattr(module, "datetime", Moved)


def item(step_id, step_type, kettle, status=StepState.INITIAL):
    return types.SimpleNamespace(id=step_id, name=step_id, type=step_type, status=status,
                                 props={"Kettle": kettle}, instance=None)


def recipe_v5():
    """BrewStart, then MashIn on the Mash Tun: BrewStart is the strike water step."""
    return [item("mash-in", "MashInStep", "mt"), item("rest", "MashStep", "mt")]


def rig_page_9b():
    """A recipe made for an older BrewStart: a strike water step on the HLT next."""
    return [item("strike", "MashInStep", "hlt"), item("mash-in", "MashInStep", "mt"), item("rest", "MashStep", "mt")]


def make(after=recipe_v5, props=READY_BY, server=None, module=bs, done=None, step_id="bs"):
    server = server or Server()
    steps = after() if callable(after) else after
    own = types.SimpleNamespace(id=step_id, name=NAME, type="BrewStart", status=StepState.ACTIVE, props=None,
                                instance=None)
    server.step = Steps([own] + steps)
    on_done = (lambda s, r: done.append(r)) if done is not None else (lambda s, r: None)
    step = module.BrewStart(server, step_id, NAME, dict(props), on_done)
    step.TICK_S = 0.01
    own.props, own.instance = step.props, step
    return server, step


def heating(monkeypatch, water=60.0, **kwargs):
    """Started the night before, its heating start come: (server, step, wall)."""
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    server, step = make(server=Server(hlt=water), **kwargs)
    asyncio.run(step.on_start())
    step.running = True                           # as CBPiStep.start() leaves it
    wall.at = step.target_start + timedelta(seconds=1)
    assert asyncio.run(step._tick(wall.at, 100000.0)) is False
    return server, step, wall


def tick(step, wall, minutes=0):
    wall.at = wall.at + timedelta(minutes=minutes)
    return asyncio.run(step._tick(wall.at, 100000.0 + minutes * 60))


async def settle(condition, timeout=3.0):
    began = time.perf_counter()
    while not condition():
        assert time.perf_counter() - began < timeout, "timed out"
        await asyncio.sleep(0.005)


def restart(step, server, wall, module=bs):
    """The server back after a power cut: a new instance from what the step saved, started again."""
    _, again = make(server=server, props=dict(step.props), module=module)
    asyncio.run(again.on_start())
    again.running = True
    return again


# ----------------------------------------------------------- heating

def test_at_the_heating_start_it_heats_the_hlt_and_stays(monkeypatch):
    """The real run loop, from the night before to hours after the ready time."""
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    done = []
    server, step = make(done=done)

    async def go():
        await step.start()
        await asyncio.sleep(0.05)                 # waiting, overnight
        assert server.kettle.calls == []
        wall.at = step.target_start + timedelta(seconds=1)
        await settle(lambda: server.kettle.auto("hlt") == (True, 152))
        for later in (0, 60, 180):                # still heating at the ready time, and hours after it
            wall.at = READY + timedelta(minutes=later)
            await asyncio.sleep(0.05)
        seen = list(done), step.summary
        await step.stop()
        return seen

    handed_over, summary = asyncio.run(go())
    assert handed_over == [], "it moved on by itself: the Mash Tun's step would have started, its pump on"
    assert server.actor.calls == [], "BrewStart switched an actor"
    assert server.kettle.calls[:2] == [("target", "hlt", 152.0), ("on", "hlt")]
    assert step.props[bs.BrewStart.HEATING_PROP] is True
    assert summary == "Heating to 152.0 °F · late for Sat Oct 24, 07:00"
    started = [n for n in server.notes if n.type == NotificationType.SUCCESS]
    assert [n.message for n in started] == ["Time to heat - the water should be ready by Sat Oct 24, 07:00."]


def test_a_ready_time_too_soon_for_the_heating_heats_at_once(monkeypatch):
    Wall(REAL_DATETIME(2026, 10, 24, 6, 40)).install(monkeypatch)
    server, step = make()
    asyncio.run(step.on_start())
    assert server.kettle.auto("hlt") == (True, 152)
    assert asyncio.run(step._tick(REAL_DATETIME(2026, 10, 24, 6, 41), 1.0)) is False
    assert [n.message for n in server.notes if n.message.startswith("Heating has to start now:")], \
        [n.message for n in server.notes]


def test_it_heats_whatever_autonext_an_older_form_saved(monkeypatch):
    server, step, _ = heating(monkeypatch, props=dict(READY_BY, AutoNext="Yes"))
    assert server.kettle.auto("hlt") == (True, 152) and step.heating is True


# ----------------------------------------------------------- holding and asking

def test_at_ready_temp_it_holds_and_asks_once(monkeypatch):
    server, step, wall = heating(monkeypatch)
    server.water(140.0)
    assert tick(step, wall, 10) is False and server.questions() == []
    assert step.summary == "Heating to 152.0 °F · ready by Sat Oct 24, 07:00"
    server.water(151.6)                           # within CraftBeerPi's 0.5 °F
    assert tick(step, wall, 10) is False
    asked = server.questions()
    assert len(asked) == 1, [n.message for n in server.notes]
    question = asked[0]
    assert (question.title, question.message) == (NAME, "Strike water hot and holding at 152.0 \u00b0F. When you've moved the strike water to the Mash Tun and connected the hoses and pumps for recirculating, press NEXT.")
    assert question.labels() == ["Next Step"] and question.keep is True
    assert server.step.kept == {("bs", "water"): "n{}".format(server.notes.index(question) + 1)}
    assert step.props[bs.BrewStart.ASKED_PROP] is True
    assert step.summary == "Holding 152.0 °F · move the strike water"
    for minutes in (1, 30, 300):                  # holding, never on by itself, never asked again
        server.water(150.0 if minutes == 30 else 152.2)
        assert tick(step, wall, minutes) is False
    assert len(server.questions()) == 1
    assert server.kettle.auto("hlt") == (True, 152)


def lifecycle(monkeypatch, then):
    """The real start(), heated to Ready_Temp and asked; `then(step, server)` moves it on. Returns what the next
    step would find as the server starts it - the done callback, and the HLT's AUTO and target then."""
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    found = []
    server, step = make()
    step._done_callback = lambda s, result: found.append((result, server.kettle.auto("hlt")))

    async def go():
        await step.start()
        await asyncio.sleep(0.03)
        wall.at = step.target_start + timedelta(seconds=1)
        await settle(lambda: getattr(step, "heating", False))
        server.water(152.0)
        await settle(lambda: server.questions())
        await then(step, server)
        await settle(lambda: found)
    asyncio.run(go())
    return server, step, found


def test_next_on_the_question_switches_the_hlt_off_before_the_next_step_starts(monkeypatch):
    async def answer(step, server):
        await server.questions()[0].action[0].method()
    server, step, found = lifecycle(monkeypatch, answer)
    assert found == [(StepResult.NEXT, (False, 152))], "the next step started with the HLT's AUTO still on"
    assert server.kettle.calls[-1] == ("off", "hlt")
    assert server.actor.calls == []


def test_the_screens_next_does_the_same(monkeypatch):
    async def screen_next(step, server):
        await step.next()                         # StepController.next
    _, _, found = lifecycle(monkeypatch, screen_next)
    assert found == [(StepResult.NEXT, (False, 152))]


def test_an_old_button_does_nothing(monkeypatch):
    server, step, wall = heating(monkeypatch)
    server.water(152.0)
    tick(step, wall, 1)
    step.running = False                          # stopped since
    asyncio.run(server.questions()[0].action[0].method())
    assert server.kettle.auto("hlt") == (True, 152) and step.cancel_reason is None


def test_next_while_it_waits_switches_an_hlt_left_on_by_hand_off(monkeypatch):
    """Next skips the wait: the Mash Tun's logic is about to drive the HLT's element."""
    Wall(NIGHT).install(monkeypatch)
    found = []
    server, step = make()
    step._done_callback = lambda s, result: found.append((result, server.kettle.auto("hlt")))
    asyncio.run(server.kettle.start("hlt"))       # AUTO on by hand

    async def go():
        await step.start()
        await asyncio.sleep(0.03)
        await step.next()
        await settle(lambda: found)
    asyncio.run(go())
    assert found == [(StepResult.NEXT, (False, 0))]


def test_stop_switches_the_hlt_off_and_start_heats_again(monkeypatch):
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    server, step = make()

    async def go():
        await step.start()
        await asyncio.sleep(0.03)
        await step.stop()                         # waiting: the HLT isn't BrewStart's yet
        assert server.kettle.calls == []
        await step.start()
        await asyncio.sleep(0.03)
        wall.at = step.target_start + timedelta(seconds=1)
        await settle(lambda: server.kettle.auto("hlt") == (True, 152))
        await step.stop()
        stopped = server.kettle.auto("hlt")
        notes = len(server.notes)
        await step.start()                        # back to heating, saying nothing new
        await settle(lambda: server.kettle.auto("hlt") == (True, 152))
        again = len(server.notes) - notes
        await step.stop()
        return stopped, again
    stopped, said = asyncio.run(go())
    assert stopped == (False, 152)
    assert said == 0


# ----------------------------------------------------------- late, stalled

def test_water_late_is_said_once_with_the_reading(monkeypatch):
    server, step, wall = heating(monkeypatch)
    server.water(140.0)
    wall.at = READY - timedelta(minutes=1)
    tick(step, wall)
    assert server.said("isn't ready") == []
    tick(step, wall, 2)
    tick(step, wall, 1)
    late = server.said("isn't ready")
    assert [(n.message, n.type) for n in late] == [(
        "The water isn't ready by Sat Oct 24, 07:00: the HLT reads 140.0 °F, short of 152.0 °F. BrewStart carries "
        "on heating it, and asks you to move it once it's there.", NotificationType.WARNING)]
    assert step.summary == "Heating to 152.0 °F · late for Sat Oct 24, 07:00"
    assert server.kettle.auto("hlt") == (True, 152) and server.questions() == []


def test_a_stall_warns_as_the_mash_in_does(monkeypatch):
    server, step, wall = heating(monkeypatch)
    step.HEAT_STALL_WINDOW = 0
    server.water(100.0)
    tick(step, wall, 1)
    tick(step, wall, 1)
    stalled = [n for n in server.notes if n.title == "Heating is not working"]
    assert len(stalled) == 1 and stalled[0].labels() == ["Keep waiting", "Stop the profile"], \
        [n.message for n in server.notes]
    assert server.step.kept.get(("bs", "heat")) is not None


# ----------------------------------------------------------- after a restart

def test_a_restart_while_heating_carries_on_heating(monkeypatch):
    server, step, wall = heating(monkeypatch)
    wall.at = READY - timedelta(minutes=10)
    server.kettle.kettles["hlt"].instance = None  # the server restarted: every AUTO off
    server.notes.clear()
    again = restart(step, server, wall)
    assert server.kettle.auto("hlt") == (True, 152) and again.heating is True
    assert server.step.allowed["bs"][0] == (READY + timedelta(hours=2)).timestamp()
    assert server.notes == [], "nothing new to say: the server says it carried on"
    server.water(152.0)
    assert tick(again, wall, 1) is False
    assert len(server.questions()) == 1


def test_a_restart_while_holding_holds_with_no_second_question(monkeypatch):
    server, step, wall = heating(monkeypatch)
    server.water(152.0)
    tick(step, wall, 1)
    assert len(server.questions()) == 1
    server.kettle.kettles["hlt"].instance = None
    server.notes.clear()
    again = restart(step, server, wall)
    for minutes in (0, 1, 10):
        assert tick(again, wall, minutes) is False
    assert server.questions() == [], "asked twice for the same water"
    assert again.summary == "Holding 152.0 °F · move the strike water"
    assert server.kettle.auto("hlt") == (True, 152)


# ----------------------------------------------------------- a recipe for an older BrewStart

def test_an_older_recipe_hands_over_to_its_strike_water_step(monkeypatch, after=rig_page_9b):
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    done = []
    server, step = make(after=after, done=done)

    async def go():
        await step.start()
        await asyncio.sleep(0.03)
        wall.at = step.target_start + timedelta(seconds=1)
        await settle(lambda: done)
    asyncio.run(go())
    assert done == [StepResult.NEXT]
    assert server.kettle.calls == [], "BrewStart heated the HLT as well as its strike water step"
    assert server.questions() == []
    following = server.step.profile[1].id         # the step the server starts next, allowed to carry on
    assert server.step.allowed.get(following) == ((READY + timedelta(hours=2)).timestamp(), NAME)


def test_with_a_step_between_it_heats_and_holds_the_water_itself(monkeypatch):
    """Only a strike water step right after it is handed over to: a step between might switch a pump on or move on
    by itself. Here BrewStart heats the HLT, holds it, asks, and never moves on."""
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    done = []
    server, step = make(after=lambda: [item("salts", "NotificationStep", "")] + rig_page_9b(), done=done)

    async def go():
        await step.start()
        await asyncio.sleep(0.03)
        wall.at = step.target_start + timedelta(seconds=1)
        await settle(lambda: server.kettle.auto("hlt") == (True, 152))
        server.water(152.0)
        await settle(lambda: server.questions())
        wall.at = READY + timedelta(hours=3)
        await asyncio.sleep(0.05)
        handed = list(done)
        await step.stop()
        return handed
    assert asyncio.run(go()) == [], "it moved on by itself"
    assert server.actor.calls == []


# ----------------------------------------------------------- the strike water helper

def test_use_while_it_waits_changes_only_ready_temp(monkeypatch):
    Wall(NIGHT).install(monkeypatch)
    server, step = make()
    asyncio.run(step.on_start())
    assert asyncio.run(step.change_target(150.4)) is False
    assert step.props["Ready_Temp"] == 150.4 and server.kettle.calls == []


def test_use_while_heating_moves_the_hlt_target_at_once(monkeypatch):
    server, step, wall = heating(monkeypatch)
    assert asyncio.run(step.change_target(155.0)) is False
    assert server.kettle.auto("hlt") == (True, 155.0) and step.props["Ready_Temp"] == 155.0
    assert step.summary == "Heating to 155.0 °F · ready by Sat Oct 24, 07:00"


def test_use_after_it_asked_takes_the_question_down_and_asks_at_the_new_temperature(monkeypatch):
    server, step, wall = heating(monkeypatch)
    server.water(152.0)
    tick(step, wall, 1)
    assert asyncio.run(step.change_target(156.0)) is True
    assert server.step.withdrawn == [("bs", "water")]
    assert step.props[bs.BrewStart.ASKED_PROP] is False and server.kettle.auto("hlt") == (True, 156.0)
    tick(step, wall, 1)
    assert len(server.questions()) == 1, "asked again before the water read the new temperature"
    server.water(155.8)
    tick(step, wall, 1)
    assert len(server.questions()) == 2


def test_forget_wait_on_a_stopped_step_that_had_asked(monkeypatch):
    server, step, wall = heating(monkeypatch)
    server.water(152.0)
    tick(step, wall, 1)
    step.running = False
    assert step.forget_wait() is True and step.props[bs.BrewStart.ASKED_PROP] is False
    assert step.forget_wait() is False


# ----------------------------------------------------------- a time already passed

def test_a_passed_time_offers_heat_now_not_next_step(monkeypatch):
    Wall(NIGHT).install(monkeypatch)
    server, step = make(props=dict(READY_BY, Mode="Today", StartTime="07:00"))
    asyncio.run(step.on_start())
    step.running = True
    offered = server.questions()
    assert [n.labels() for n in offered] == [["Heat now"]], [n.message for n in server.notes]
    assert offered[0].message.startswith("Fri Oct 23, 07:00 has already passed, so nothing heats by itself.")
    assert server.kettle.calls == []
    asyncio.run(offered[0].action[0].method())
    assert server.kettle.auto("hlt") == (True, 152) and step.heating is True
    assert asyncio.run(step._tick(NIGHT + timedelta(seconds=1), 1.0)) is False


def test_long_after_the_ready_time_it_offers_heat_now(monkeypatch):
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    server, step = make()
    asyncio.run(step.on_start())
    wall.at = READY + timedelta(hours=2, minutes=30)
    again = restart(step, server, wall)
    assert [n.labels() for n in server.questions()] == [["Heat now"]]
    assert "more than 2 hours after" in server.questions()[0].message
    assert server.kettle.calls == [] and again.heating is False


# ----------------------------------------------------------- the words

def test_the_status_lines_never_read_as_a_heating_start(monkeypatch):
    server, step, wall = heating(monkeypatch)
    lines = [step.summary]
    server.water(140.0)
    wall.at = READY + timedelta(minutes=1)
    tick(step, wall)
    lines.append(step.summary)
    server.water(152.0)
    tick(step, wall, 1)
    lines.append(step.summary)
    assert lines == ["Heating to 152.0 °F · ready by Sat Oct 24, 07:00",
                     "Heating to 152.0 °F · late for Sat Oct 24, 07:00",
                     "Holding 152.0 °F · move the strike water"]
    for line in lines:
        assert not line.startswith("Start") and "heating from" not in line and not STARTS.search(line), line


def test_the_new_words_are_in_german_too():
    new = ("default_mash_tun", "summary_heating", "summary_heating_late", "summary_water_ready",
           "notify_water_ready", "notify_water_late", "notify_ready_time_changed", "notify_cannot_heat",
           "notify_start_time_changed", "notify_start_at_scheduled", "notify_start_at_reached", "summary_heating_now",
           "action_heat_now", "notify_heat_now", "notify_passed_today_heat_now", "notify_passed_heat_now",
           "notify_too_late_heat_now", "change_waiting_heat_now")
    for key in new:
        assert key in MESSAGES["en"] and key in MESSAGES["de"] and MESSAGES["de"][key] != MESSAGES["en"][key], key
    assert MESSAGES["en"]["notify_water_ready"].format(to="Mash Tun", target="130.7 \u00b0F") == (
        "Strike water hot and holding at 130.7 \u00b0F. When you've moved the strike water to the Mash Tun and "
        "connected the hoses and pumps for recirculating, press NEXT.")
