"""BrewStart's change_settings: any of its settings, also while it waits for its time, heats or holds the water.

CraftBeerPi refuses a running step's settings (PUT /step2/{id} answers 409), and a BrewStart waiting for its time, or
heating the HLT, is running. Brew Day's BrewStart box and the Brew Steps page offer every setting until BrewStart has
finished, through the action change_settings (POST /step2/action/{id}). On the real step, with the server faked as far
as the step reaches into it, these check that:

- the action is declared with every setting of the form, and no defaults that could stand in for one not sent;
- waiting, it plans again as change_time does: a new water volume, temperature, element or vessel moves the heating
  start at once, the moment planned stands unless the day or the time changed, Timing between Ready by and Start at
  plans the other way, and too little time left for the heating starts it now;
- heating, Ready_Temp moves the vessel's target at once; the water, its unit and the element change the estimate; a
  new time only moves the ready time, in change_time's words; a new vessel moves the heating - the old one's AUTO off
  before the new one's on - and one whose AUTO won't go on is refused with the old one heating again; Start at stops
  BrewStart's heating and makes it wait for its time, or for Next Step once that has passed, never moving on by itself;
- holding, a new temperature or vessel takes the question down, and asks again once the water is there;
- before START, the settings are saved for it, with the Campden dose as a saved form gives it;
- stopped, the change goes into the stopped step, nothing heats until START, and START plans from the new settings;
- the live step is changed in place, never rebuilt or swapped: no new BrewStart is built, the
  profile keeps the same step and settings, Stop through the profile still reaches it and switches the HLT off, the
  run's own state stays, and nothing a reset cleared comes back;
- the water is never less than the recipe's strike water, with the strike water box's minimum and words;
- every refusal changes nothing, tells every screen why as an info, says what to do instead, in English and German;
- the strike water helper looks again at once;
- the same brewing-clock times give the same results at 1x and 60x, on a simulation's clock weeks from the PC's.
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
from cbpi.api.dataclasses import NotificationType  # noqa: E402
from cbpi.api.decorator import resolve_action  # noqa: E402
from cbpi.api.step import StepResult, StepState  # noqa: E402
from cbpi4_BrewStart.strings import MESSAGES  # noqa: E402
from test_heats_its_kettle import (NIGHT, READY, READY_BY, Server, Wall, heating,  # noqa: E402
                                   make, settle, tick)
from test_step_clock import Kettle  # noqa: E402

START_AT = dict(READY_BY, Timing="Start at", StartTime="06:00")
#: The recipe's strike water as its import keeps it: 19.873 L, 5.25 gal.
STRIKE_L = 5.25 * 3.785411784


class Strike:
    """CraftBeerPi's strike water helper, as far as BrewStart reaches into it: update() looks again."""

    def __init__(self):
        self.looks = []
        self.step = None

    def update(self):
        self.looks.append(dict(self.step.props))


def with_parts(server, step, strike_l=None):
    """A boil kettle beside the HLT and the Mash Tun, the strike water helper, and the recipe's strike water."""
    bk = Kettle("Boil Kettle", "bk-probe")
    bk.id = "bk"
    server.kettle.kettles["bk"] = bk
    server.readings["bk-probe"] = {"value": 70.0, "age": 1, "max_age": 30}
    server.kettle.kettles["dry"] = Kettle("Spare", None)
    server.kettle.kettles["dry"].id = "dry"
    server.strike = Strike()
    server.strike.step = step
    server.step.basic_data = {"mash_numbers": {"infuse_l": strike_l}} if strike_l else {}
    return server, step


def waiting(monkeypatch, props=READY_BY, at=NIGHT, strike_l=None):
    """START pressed at `at`: planned, and waiting for its time. (server, step, wall)."""
    wall = Wall(at)
    wall.install(monkeypatch)
    server, step = make(props=props)
    with_parts(server, step, strike_l)
    asyncio.run(step.on_start())
    step.running = True                           # as CBPiStep.start() leaves it
    server.notes.clear()
    return server, step, wall


def heating_with_parts(monkeypatch, **kwargs):
    server, step, wall = heating(monkeypatch, **kwargs)
    with_parts(server, step)
    server.notes.clear()
    return server, step, wall


def change(step, **settings):
    return asyncio.run(step.change_settings(**settings))


def state(step, server):
    return (dict(step.props), getattr(step, "target_start", None), getattr(step, "asked_for", None),
            getattr(step, "heating", None), getattr(step, "asked", None), list(server.kettle.calls),
            {k: server.kettle.auto(k) for k in server.kettle.kettles})


def refused(server, step, **settings):
    """The change refused: nothing changed, every screen told why as an info, and the words raised."""
    before = state(step, server)
    looks = len(server.strike.looks)
    with pytest.raises(ValueError) as raised:
        change(step, **settings)
    assert state(step, server) == before, "a refused change changed something"
    last = server.notes[-1]
    assert (last.title, last.message, last.type) == (step.name, str(raised.value), NotificationType.INFO)
    assert len(server.strike.looks) == looks, "the strike water helper was told of a change that wasn't made"
    return str(raised.value)


def said(server):
    return [n.message for n in server.notes]


# ----------------------------------------------------------- declared

def test_the_action_is_declared_with_every_setting_and_no_defaults():
    declared = bs.BrewStart.change_settings
    assert getattr(declared, "action", False) is True and declared.key == "Change settings"
    labels = [p.label for p in declared.parameters]
    assert labels == list(bs.SETTINGS) == [p.label for p in bs.step_properties()]
    assert all(getattr(p, "default_value", None) is None for p in declared.parameters), \
        "a default would stand in for a setting not sent"
    assert resolve_action(make()[1], "change_settings") is not None
    german = bs.change_settings_parameters("de", "F")
    assert [p.display_label for p in german][:4] == ["Zeitpunkt", "Tag", "Datum", "Uhrzeit"]


def test_its_words_are_in_german_too():
    keys = [k for k in MESSAGES["en"] if k.startswith("settings_") or k in (
        "action_change_settings", "notify_time_was", "notify_heating_estimate", "notify_holding",
        "notify_heating_moved", "notify_heating_moved_now", "notify_heating_estimate_now", "notify_heating_plain")]
    assert len(keys) == 30
    missing = [k for k in keys if k not in MESSAGES["de"]]
    assert missing == []
    assert MESSAGES["de"]["settings_too_little"] != MESSAGES["en"]["settings_too_little"]


# ----------------------------------------------------------- waiting

def test_waiting_more_water_plans_the_heating_earlier_from_the_same_moment(monkeypatch):
    """Mode Tomorrow, set the evening before: after midnight a new volume doesn't work Tomorrow out again."""
    server, step, wall = waiting(monkeypatch, dict(READY_BY, Mode="Tomorrow", StartDate=""))
    first = step.target_start
    wall.at = NIGHT + timedelta(hours=2, minutes=30)
    change(step, Volume=30)
    assert step.props["Volume"] == 30.0
    assert step.asked_for == step.ready_at == READY, "the moment planned moved"
    assert step.target_start < first - timedelta(minutes=30) and step.target_start == step.plan.start
    assert said(server) == ["Settings changed. Ready by Sat Oct 24, 07:00: heating starts about "
                            + step._at(step.target_start) + ". 60.0 °F to 152.0 °F takes about "
                            + str(round(step.plan.heat_minutes)) + " min, plus 10 min spare."]
    assert server.notes[0].type == NotificationType.INFO
    assert server.step.saves >= 1 and step.props[bs.BrewStart.PLANNED_PROP].endswith(READY.isoformat())
    assert len(server.strike.looks) == 1 and server.strike.looks[0]["Volume"] == 30.0
    assert step.summary.startswith("Ready Sat Oct 24, 07:00 · heating from ")


@pytest.mark.parametrize("setting, value, earlier", [("Ready_Temp", 170, True), ("Heater_Watts", 2500, True),
                                                     ("Heat_Kettle", "bk", False)])
def test_waiting_each_heating_setting_plans_again_at_once(monkeypatch, setting, value, earlier):
    server, step, _ = waiting(monkeypatch)
    first = step.target_start
    change(step, **{setting: value})
    assert step.props[setting] == value
    assert (step.target_start < first) if earlier else (step.target_start > first), "not planned again"
    if setting == "Heat_Kettle":
        assert step.plan.water_temp == 70.0, "not planned from the new vessel's probe"
    assert server.kettle.calls == [], "waiting, nothing heats"


def test_waiting_a_new_time_is_change_times_words(monkeypatch):
    server, step, _ = waiting(monkeypatch)
    change(step, StartTime="06:30", Volume=16)
    assert step.asked_for == READY - timedelta(minutes=30)
    assert said(server)[0].startswith("Time changed, was Sat Oct 24, 07:00. Ready by Sat Oct 24, 06:30")


def test_waiting_ready_by_to_start_at_and_back(monkeypatch):
    """Start at heats from its time; Ready by early enough to be hot by it. Both heat and hold."""
    server, step, wall = waiting(monkeypatch)
    change(step, Timing="Start at")
    assert step.plan is None and step.ready_by is False and step.target_start == step.asked_for == READY
    assert said(server)[-1] == ("Settings changed. Heating starts at Sat Oct 24, 07:00: the HLT heats to 152.0 \u00b0F "
                                "and holds it until you press Next.")
    change(step, Timing="Ready by")
    assert step.plan is not None and step.target_start == step.plan.start < READY
    wall.at = step.target_start + timedelta(seconds=1)
    assert tick(step, wall) is False and server.kettle.auto("hlt") == (True, 152), "it didn't heat at its time"


def test_waiting_start_at_heats_from_its_time_whatever_autonext_says(monkeypatch):
    """AutoNext, which older screens send, is taken and ignored: Start at heats at its time, never before, and holds
    the water; nothing moves it on but Next."""
    server, step, wall = waiting(monkeypatch, START_AT)
    change(step, AutoNext="Yes", NotifyBefore=20)
    assert "AutoNext" not in step.props and step.notify_before_seconds == 1200
    wall.at = step.target_start - timedelta(minutes=10)
    assert tick(step, wall) is False and server.kettle.calls == [], "it heated before its time"
    assert said(server)[-1] == "Heating starts in 10m 00s"
    wall.at = step.target_start + timedelta(seconds=1)
    assert tick(step, wall) is False and step.heating and server.kettle.auto("hlt") == (True, 152)
    server.water(152.0)
    assert tick(step, wall, 5) is False and [n.message for n in server.questions()] == ["Strike water hot and holding at 152.0 \u00b0F. When you've moved the strike water to the Mash Tun and connected the hoses and pumps for recirculating, press NEXT."]
    assert tick(step, wall, 120) is False, "it moved on by itself"


def test_waiting_too_little_time_for_more_water_starts_the_heating_now(monkeypatch):
    server, step, _ = waiting(monkeypatch)
    change(step, Volume=200)
    assert step.heating is True and server.kettle.auto("hlt") == (True, 152)
    assert [m for m in said(server) if m.startswith("Heating has to start now: from 60.0 °F it needs about ")]


def test_waiting_the_campden_dose_for_new_water(monkeypatch):
    server, step, _ = waiting(monkeypatch, dict(READY_BY, Campden_Reminder="Yes"))
    change(step, Volume=25)
    assert "Campden: 1¼ tablet(s) for 25 gal of brewing water" in said(server)[-1]
    change(step, Grain_Temp=68)
    assert not [m for m in said(server)[-1:] if m.startswith("Campden")], "a grain change gave the dose again"
    change(step, Grain_Temp="")
    assert step.props["Grain_Temp"] == ""


def test_the_same_settings_sent_again_change_nothing(monkeypatch):
    server, step, _ = waiting(monkeypatch)
    saves = server.step.saves
    change(step, **{k: v for k, v in READY_BY.items()}, Campden_Reminder="", AutoNext="")
    assert server.notes == [] and server.step.saves == saves and server.strike.looks == []


def test_a_choice_from_an_older_form_sent_back_as_saved_is_no_change(monkeypatch):
    server, step, _ = waiting(monkeypatch, dict(READY_BY, Mode="DateTime"))
    change(step, Mode="DateTime", Volume=16)
    assert step.props["Mode"] == "DateTime" and step.props["Volume"] == 16.0
    assert said(server)[-1].startswith("Settings changed. Ready by Sat Oct 24, 07:00")


def test_waiting_for_heat_now_a_new_time_plans_again_and_anything_else_waits_on(monkeypatch):
    server, step, wall = waiting(monkeypatch, dict(READY_BY, StartDate="2026-10-23", StartTime="21:00"))
    assert step.target_start is None and step.waits_for_heat_now
    change(step, Volume=16)
    assert step.target_start is None and said(server)[-1] == "Settings changed."
    change(step, Mode="Tomorrow", StartTime="07:00")
    assert step.asked_for == READY and step.target_start == step.plan.start and not step.waits_for_heat_now
    wall.at = step.target_start + timedelta(seconds=1)
    assert tick(step, wall) is False and server.kettle.auto("hlt") == (True, 152)


# ----------------------------------------------------------- heating

def test_heating_a_new_temperature_moves_the_target_at_once(monkeypatch):
    server, step, _ = heating_with_parts(monkeypatch)
    change(step, Ready_Temp=165)
    assert server.kettle.auto("hlt") == (True, 165.0) and step.heating
    assert step.summary == "Heating to 165.0 °F · ready by Sat Oct 24, 07:00"
    assert said(server) == ["Settings changed. Ready by Sat Oct 24, 07:00: the HLT heats to 165.0 °F; from 60.0 °F "
                            "that takes about 48 min."]


def test_heating_the_water_and_element_change_the_estimate_only(monkeypatch):
    server, step, _ = heating_with_parts(monkeypatch)
    calls = list(server.kettle.calls)
    change(step, Volume=30, Volume_Unit="gal", Heater_Watts=4000)
    assert server.kettle.calls == calls, "the heating was touched"
    assert said(server)[-1].endswith("the HLT heats to 152.0 °F; from 60.0 °F that takes about 112 min.")
    assert server.strike.looks[-1]["Volume"] == 30.0


def test_heating_a_new_time_only_moves_the_ready_time_in_change_times_words(monkeypatch):
    server, step, _ = heating_with_parts(monkeypatch)
    calls = list(server.kettle.calls)
    change(step, StartTime="07:30")
    assert step.ready_at == step.asked_for == READY + timedelta(minutes=30) and server.kettle.calls == calls
    assert said(server) == ["Time changed, was Sat Oct 24, 07:00. Ready by Sat Oct 24, 07:30: the HLT carries on "
                            "heating to 152.0 °F."]
    assert server.step.allowed["bs"][0] == (READY + timedelta(hours=2, minutes=30)).timestamp()


def test_heating_a_new_vessel_moves_the_heating_old_off_first(monkeypatch):
    server, step, wall = heating_with_parts(monkeypatch)
    server.kettle.calls.clear()
    change(step, Heat_Kettle="bk")
    assert server.kettle.calls == [("off", "hlt"), ("target", "bk", 152.0), ("on", "bk")], \
        "the old vessel's AUTO must go off before the new one's goes on"
    assert server.kettle.auto("hlt")[0] is False and server.kettle.auto("bk") == (True, 152.0)
    assert said(server) == ["Settings changed. Heating moved from the HLT to the Boil Kettle: the HLT's AUTO is off, "
                            "and the Boil Kettle heats to 152.0 °F, ready by Sat Oct 24, 07:00."]
    server.readings["bk-probe"]["value"] = 152.0
    tick(step, wall, 5)
    assert [n.message for n in server.questions()] == ["Strike water hot and holding at 152.0 \u00b0F. When you've moved the strike water to the Mash Tun and connected the hoses and pumps for recirculating, press NEXT."]


def test_heating_a_vessel_whose_auto_wont_go_on_is_refused_and_the_old_one_heats_again(monkeypatch):
    server, step, _ = heating_with_parts(monkeypatch)
    plain_start = server.kettle.start

    async def stuck(kettle_id):
        if kettle_id == "bk":
            server.kettle.calls.append(("on", kettle_id))
            return
        await plain_start(kettle_id)
    monkeypatch.setattr(server.kettle, "start", stuck)
    with pytest.raises(ValueError) as raised:
        change(step, Heat_Kettle="bk", Ready_Temp=160)
    assert str(raised.value) == ("BrewStart couldn't switch the Boil Kettle's AUTO on, so the heating stays on the "
                                 "HLT. Check the Boil Kettle's logic on the Hardware page.")
    assert (step.props["Heat_Kettle"], step.props["Ready_Temp"]) == ("hlt", 152)
    assert server.kettle.auto("hlt") == (True, 152.0) and server.kettle.auto("bk")[0] is False
    assert server.notes[-1].type == NotificationType.INFO and server.strike.looks == []


def test_heating_start_at_keeps_heating_and_never_moves_on(monkeypatch):
    """Start at heats too: changing Timing while the water heats leaves the heating as it is, with no ready time to
    be late for; nothing moves it on but Next."""
    server, step, wall = heating_with_parts(monkeypatch)
    calls = list(server.kettle.calls)
    change(step, Timing="Start at")
    assert server.kettle.calls == calls and server.kettle.auto("hlt") == (True, 152), "the heating was touched"
    assert step.heating is True and step.ready_at is None and step.props[bs.BrewStart.HEATING_PROP] is True
    assert step.summary == "Heating to 152.0 °F"
    assert said(server) == ["Settings changed. The HLT heats to 152.0 °F; from 60.0 °F that takes about 42 min."]
    wall.at = READY + timedelta(hours=1)
    assert tick(step, wall) is False and not server.said("isn't ready by"), "late for a ready time it hasn't got"


# ----------------------------------------------------------- holding

def held(monkeypatch):
    server, step, wall = heating_with_parts(monkeypatch)
    server.water(152.0)
    tick(step, wall, 5)
    assert step.asked and server.step.kept
    server.notes.clear()
    return server, step, wall


def test_holding_a_new_temperature_takes_the_question_down_and_asks_there(monkeypatch):
    server, step, wall = held(monkeypatch)
    change(step, Ready_Temp=160)
    assert server.step.withdrawn == [("bs", "water")] and step.asked is False
    assert server.kettle.auto("hlt") == (True, 160.0)
    tick(step, wall, 1)
    assert server.questions() == []
    server.water(160.0)
    tick(step, wall, 5)
    assert len(server.questions()) == 1


def test_holding_other_settings_leave_the_question(monkeypatch):
    server, step, _ = held(monkeypatch)
    change(step, Volume=16)
    assert server.step.withdrawn == [] and step.asked is True
    assert said(server) == ["Settings changed. The HLT holds the water at 152.0 °F."]


def test_holding_start_at_keeps_holding_and_asking(monkeypatch):
    server, step, wall = held(monkeypatch)
    change(step, Timing="Start at")
    assert server.step.withdrawn == [] and step.asked is True and server.kettle.auto("hlt") == (True, 152)
    assert step.props[bs.BrewStart.ASKED_PROP] is True
    assert tick(step, wall, 60) is False, "it moved on by itself"


# ----------------------------------------------------------- not started

def test_not_started_the_settings_are_saved_for_start(monkeypatch):
    Wall(NIGHT).install(monkeypatch)
    server, step = make(props=dict(READY_BY, Campden_Reminder="Yes"))
    with_parts(server, step)
    server.step.profile[0].status = StepState.INITIAL
    change(step, Volume=20, Ready_Temp=165, Heat_Kettle="bk")
    assert (step.props["Volume"], step.props["Ready_Temp"], step.props["Heat_Kettle"]) == (20.0, 165.0, "bk")
    assert said(server) == ["Campden: 1 tablet(s) for 20 gal of brewing water - 1 per 20 gal / 75 L. Crush it and "
                            "stir it in; it clears chlorine and chloramine within minutes, in cold water or hot."]
    assert server.kettle.calls == [] and server.strike.looks
    asyncio.run(step.on_start())
    assert step.plan.target_temp == 165.0 and step.plan.water_temp == 70.0


def test_stopped_after_it_asked_a_new_vessel_takes_the_question_down(monkeypatch):
    server, step, wall = held(monkeypatch)
    step.running = False
    asyncio.run(step.on_stop())
    server.step.profile[0].status = StepState.STOP
    change(step, Heat_Kettle="bk")
    assert step.props[bs.BrewStart.ASKED_PROP] is False and step.props[bs.BrewStart.HEATING_PROP] is True


# ----------------------------------------------------------- the water

NB = "\u00a0"


def hlt_on_hardware(server, min_water=None, capacity=None):
    """The kettles as the Hardware page lists them, so core's find_hlt finds the HLT, with its own least water and
    capacity (gal, on this °F brewery)."""
    for kettle in server.kettle.kettles.values():
        kettle.type, kettle.heater = None, None
    hlt = server.kettle.kettles["hlt"]
    hlt.min_water, hlt.capacity = min_water, capacity
    server.kettle.data = list(server.kettle.kettles.values())


def test_never_less_water_than_the_strike_water_and_the_hlts_least(monkeypatch):
    # No HLT the box can find on the Hardware page: its cautious 3 gal.
    server, step, _ = waiting(monkeypatch, strike_l=STRIKE_L)
    assert refused(server, step, Volume=8.24) == (
        f"BrewStart's settings didn't change: HLT water 8.24{NB}gal would leave 2.99{NB}gal in the HLT after the "
        f"5.25{NB}gal of mash water. The HLT needs at least 3.00{NB}gal over its element and coil (a cautious "
        f"3{NB}gal, as the Hardware page gives neither its least water nor its capacity), so the HLT water has to be "
        f"at least 8.25{NB}gal.")
    assert refused(server, step, Volume=5) == (
        f"BrewStart's settings didn't change: HLT water 5.00{NB}gal is less than the 5.25{NB}gal of mash water. The "
        f"HLT needs at least 3.00{NB}gal over its element and coil (a cautious 3{NB}gal, as the Hardware page gives "
        f"neither its least water nor its capacity), so the HLT water has to be at least 8.25{NB}gal.")
    assert refused(server, step, Volume_Unit="L").endswith(
        f"(a cautious 11.4{NB}L, as the Hardware page gives neither its least water nor its capacity), so the HLT "
        f"water has to be at least 31.27{NB}L.")
    change(step, Volume=8.25)
    assert step.props["Volume"] == 8.25


def test_the_hlts_own_least_water_from_the_hardware_page(monkeypatch):
    server, step, _ = waiting(monkeypatch, strike_l=STRIKE_L)
    hlt_on_hardware(server, min_water=4, capacity=20)
    assert refused(server, step, Volume=9.24) == (
        f"BrewStart's settings didn't change: HLT water 9.24{NB}gal would leave 3.99{NB}gal in the HLT after the "
        f"5.25{NB}gal of mash water. The HLT needs at least 4.00{NB}gal over its element and coil (its least water on "
        f"the Hardware page), so the HLT water has to be at least 9.25{NB}gal.")
    change(step, Volume=9.25)
    assert step.props["Volume"] == 9.25


def test_the_hlts_least_water_from_its_capacity_in_either_unit(monkeypatch):
    server, step, _ = waiting(monkeypatch, strike_l=STRIKE_L)
    hlt_on_hardware(server, capacity=20)
    assert refused(server, step, Volume=8) == (
        f"BrewStart's settings didn't change: HLT water 8.00{NB}gal would leave 2.75{NB}gal in the HLT after the "
        f"5.25{NB}gal of mash water. The HLT needs at least 3.00{NB}gal over its element and coil (15% of its "
        f"20{NB}gal capacity, until its least water is set on the Hardware page), so the HLT water has to be at "
        f"least 8.25{NB}gal.")
    # In litres: its 20 gal are 75.71 L, 15 % of them 11.36 L, and the strike water 19.87 L.
    assert refused(server, step, Volume=31.22, Volume_Unit="L") == (
        f"BrewStart's settings didn't change: HLT water 31.22{NB}L would leave 11.35{NB}L in the HLT after the "
        f"19.87{NB}L of mash water. The HLT needs at least 11.36{NB}L over its element and coil (15% of its "
        f"75.71{NB}L capacity, until its least water is set on the Hardware page), so the HLT water has to be at "
        f"least 31.23{NB}L.")
    change(step, Volume=31.23, Volume_Unit="L")
    assert (step.props["Volume"], step.props["Volume_Unit"]) == (31.23, "L")


def test_on_a_core_without_the_hlts_least_water_the_strike_water_alone(monkeypatch):
    monkeypatch.setattr(bs, "_core_least_water", None)
    server, step, _ = waiting(monkeypatch, strike_l=STRIKE_L)
    assert refused(server, step, Volume=5) == ("BrewStart's settings didn't change: the HLT holds the strike water "
                                               "too, so at least 5.25 gal.")
    change(step, Volume=5.25)
    assert step.props["Volume"] == 5.25


def test_any_water_without_the_recipes_strike_water(monkeypatch):
    server, step, _ = waiting(monkeypatch)
    change(step, Volume=1)
    assert step.props["Volume"] == 1.0


def test_too_little_water_in_german(monkeypatch):
    server, step, _ = waiting(monkeypatch, strike_l=STRIKE_L)
    hlt_on_hardware(server, min_water=4)
    monkeypatch.setattr(bs, "configured_language", lambda cbpi: "de")
    assert refused(server, step, Volume=9) == (
        f"Die Einstellungen von BrewStart bleiben: HLT-Wasser 9.00{NB}gal ließe nach den 5.25{NB}gal Einmaischwasser "
        f"3.75{NB}gal im HLT. Der HLT braucht mindestens 4.00{NB}gal über Heizstab und Spirale (seine "
        f"Mindestwassermenge auf der Seite Hardware), also muss das HLT-Wasser mindestens 9.25{NB}gal sein.")
    hlt_on_hardware(server, capacity=20)
    assert "(15 % seines Fassungsvermögens von 20\u00a0gal, bis seine Mindestwassermenge auf der Seite Hardware " \
           "eingetragen ist)" in refused(server, step, Volume=8)


# ----------------------------------------------------------- refused

@pytest.mark.parametrize("settings, words", [
    (dict(Strike_Volume=5), "BrewStart's settings didn't change: it has no setting called 'Strike_Volume'. "
                            "Leave it out."),
    (dict(_brewstart_heating=True), "BrewStart's settings didn't change: it has no setting called "
                                    "'_brewstart_heating'. Leave it out."),
    (dict(Timing="Soon"), "BrewStart's settings didn't change: Timing can't be 'Soon'. Choose Start at or Ready by."),
    (dict(Mode="Daily"), "BrewStart's settings didn't change: Day can't be 'Daily'. Choose Today, Tomorrow or Date."),
    (dict(Volume="lots"), "BrewStart's settings didn't change: Water volume must be a number, not 'lots'. Type a "
                          "number."),
    (dict(Heater_Watts=0), "BrewStart's settings didn't change: Element power must be more than 0. Type a number "
                           "above 0."),
    (dict(NotifyBefore=-5), "BrewStart's settings didn't change: Heads-up can't be below 0. Type 0 for none."),
    (dict(Ready_Temp=250), "BrewStart's settings didn't change: water boils at 212.0 °F, so it can't be heated to "
                           "250.0 °F. Choose a lower temperature."),
    (dict(Heat_Kettle="nope"), "BrewStart's settings didn't change: there's no vessel 'nope' on the Hardware page. "
                               "Choose one that is there."),
    (dict(Heat_Kettle="dry"), "BrewStart's settings didn't change: Ready by needs a Kettle that has a sensor"),
    (dict(Ready_Temp=""), "BrewStart's settings didn't change: Ready by needs the Temperature the water must reach"),
    (dict(StartTime="25:99"), "BrewStart's settings didn't change: Invalid start time '25:99' (expected HH:MM)"),
    (dict(Mode="Today", StartTime="21:00", Volume=20), "Fri Oct 23, 21:00 has already passed, so BrewStart's "
                                                       "settings didn't change. For tomorrow morning, choose "
                                                       "Tomorrow."),
    (dict(StartDate="2026-10-22"), "Thu Oct 22, 07:00 has already passed, so BrewStart's settings didn't change. "
                                   "Choose a later day or time."),
])
def test_refused_waiting_with_nothing_changed(monkeypatch, settings, words):
    server, step, _ = waiting(monkeypatch)
    assert refused(server, step, **settings) == words


def test_refused_heating_with_the_heating_untouched(monkeypatch):
    server, step, _ = heating_with_parts(monkeypatch)
    assert refused(server, step, Heat_Kettle="", Volume=20).endswith("Ready by needs a Kettle that has a sensor")
    assert refused(server, step, Timing="Soon", StartTime="07:30").endswith("Choose Start at or Ready by.")
    assert server.kettle.auto("hlt") == (True, 152)


def test_a_start_at_heating_takes_a_new_time_and_heats_on(monkeypatch):
    server, step, wall = waiting(monkeypatch, dict(START_AT, Campden_Reminder="Yes"))
    wall.at = step.target_start + timedelta(seconds=1)
    assert tick(step, wall) is False and step.heating
    calls = list(server.kettle.calls)
    change(step, StartTime="08:00")
    assert server.kettle.calls == calls and step.heating and step.asked_for.hour == 8
    assert said(server)[-1] == "Time changed, was Sat Oct 24, 06:00. The HLT carries on heating to 152.0 °F."
    change(step, Volume=12)
    assert step.props["Volume"] == 12.0 and said(server)[-1].startswith("Campden:")


def test_refused_once_it_has_finished(monkeypatch):
    Wall(NIGHT).install(monkeypatch)
    server, step = make()
    with_parts(server, step)
    server.step.profile[0].status = StepState.DONE
    assert refused(server, step, Volume=20) == ("BrewStart has finished, so its settings can't change now. To brew "
                                                "again, reset the brew steps first.")


def test_refused_in_german(monkeypatch):
    server, step, _ = waiting(monkeypatch)
    monkeypatch.setattr(bs, "configured_language", lambda cbpi: "de")
    assert refused(server, step, Timing="Bald") == ("Die Einstellungen von BrewStart bleiben: Zeitpunkt kann nicht "
                                                    "'Bald' sein. Start at oder Ready by wählen.")


# ----------------------------------------------------------- the live step, never a copy

def constructions(monkeypatch):
    """Every BrewStart built from here on: a change of settings must build none."""
    built = []
    plain = bs.BrewStart.__init__

    def counted(self, *args, **kwargs):
        built.append(self)
        plain(self, *args, **kwargs)
    monkeypatch.setattr(bs.BrewStart, "__init__", counted)
    return built


def test_waiting_the_live_step_is_changed_in_place_and_stop_still_reaches_it(monkeypatch):
    """A running step rebuilt and swapped in would leave the old copy running outside the profile, out of Stop's reach.
    Here a Start at that moves on by itself at its time: a copy left running would move the brew on."""
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    done = []
    server, step = make(props=dict(START_AT, StartTime="07:00"), done=done)
    with_parts(server, step)
    built = constructions(monkeypatch)

    async def go():
        await step.start()
        await asyncio.sleep(0.05)
        live = server.step.find_by_id("bs")
        planned = step.props[bs.BrewStart.PLANNED_PROP]
        saves = server.step.saves
        await live.instance.change_settings(Volume=20, NotifyBefore=5)
        seen = dict(built=list(built), same=live.instance is step, props=live.props is step.props,
                    volume=live.props["Volume"], saved=server.step.saves > saves,
                    planned=live.props[bs.BrewStart.PLANNED_PROP] == planned, running=step.running)
        await server.step.find_by_id("bs").instance.stop()      # what StepController.stop does
        wall.at = READY + timedelta(minutes=1)                  # its time passes, stopped
        await asyncio.sleep(0.05)
        return seen
    seen = asyncio.run(go())
    assert seen == dict(built=[], same=True, props=True, volume=20.0, saved=True, planned=True, running=True), seen
    assert step.running is False and step.task.done(), "Stop didn't reach the running step"
    assert done == [StepResult.STOP], "a step left running moved the brew on after Stop"


def test_heating_the_live_step_is_changed_in_place_and_stop_switches_the_hlt_off(monkeypatch):
    """While the HLT heats, Stop through the profile still reaches the step that heats, and its AUTO goes off; the
    run's own state - heating, the water late said once, the moment kept - stays as the run left it."""
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    done = []
    server, step = make(done=done)
    with_parts(server, step)
    built = constructions(monkeypatch)

    async def go():
        await step.start()
        await asyncio.sleep(0.05)
        wall.at = step.target_start + timedelta(seconds=1)
        await settle(lambda: server.kettle.auto("hlt") == (True, 152))
        wall.at = READY + timedelta(minutes=1)                  # late: said once
        await settle(lambda: step.late_said)
        late = [n.message for n in server.notes if n.message.startswith("The water isn't ready by")]
        live = server.step.find_by_id("bs")
        kept = {k: step.props.get(k) for k in (bs.BrewStart.PLANNED_PROP, bs.BrewStart.HEATING_PROP)}
        await live.instance.change_settings(Volume=20, Ready_Temp=160)
        await asyncio.sleep(0.05)
        seen = dict(built=list(built), same=live.instance is step, props=live.props is step.props,
                    heating=step.heating, late=step.late_said, auto=server.kettle.auto("hlt"),
                    kept=kept == {k: step.props.get(k) for k in kept}, said_late=len(late) == 1 and len(
                        [n for n in server.notes if n.message.startswith("The water isn't ready by")]) == 1)
        await server.step.find_by_id("bs").instance.stop()
        server.water(160.0)                                     # a copy left heating would ask now
        await asyncio.sleep(0.05)
        return seen
    seen = asyncio.run(go())
    assert seen == dict(built=[], same=True, props=True, heating=True, late=True, auto=(True, 160.0), kept=True,
                        said_late=True), seen
    assert step.running is False and step.task.done(), "Stop didn't reach the step that heats"
    assert server.kettle.auto("hlt")[0] is False, "the HLT's AUTO stayed on after Stop"
    assert server.questions() == [] and done == [StepResult.STOP]


# ----------------------------------------------------------- stopped

def test_stopped_while_it_waited_a_change_is_taken_in_place_and_start_plans_from_it(monkeypatch):
    """STOP, change, START: the change goes into the stopped step itself, nothing runs while it is stopped, and START
    plans from the new settings - from the moment it kept when the day and the time didn't change, even after
    midnight for a Tomorrow brew."""
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    done = []
    server, step = make(props=dict(READY_BY, Mode="Tomorrow", StartDate=""), done=done)
    with_parts(server, step)
    built = constructions(monkeypatch)
    live = server.step.find_by_id("bs")

    async def go():
        await live.instance.start()
        await asyncio.sleep(0.05)
        first = step.target_start
        await live.instance.stop()
        live.status = StepState.STOP                            # as StepController.stop leaves it
        planned = step.props[bs.BrewStart.PLANNED_PROP]
        wall.at = NIGHT + timedelta(hours=2, minutes=30)        # after midnight
        await live.instance.change_settings(Volume=30, Ready_Temp=160)
        seen = dict(built=list(built), same=live.instance is step, props=live.props is step.props,
                    saved=(live.props["Volume"], live.props["Ready_Temp"]), planned=step.props.get(
                        bs.BrewStart.PLANNED_PROP) == planned, running=step.running, calls=list(server.kettle.calls))
        await live.instance.start()
        await asyncio.sleep(0.05)
        seen.update(asked_for=step.asked_for, earlier=step.target_start < first - timedelta(minutes=30),
                    target=step.plan.target_temp, waiting=step.running and not step.heating)
        await live.instance.stop()
        return seen
    seen = asyncio.run(go())
    assert seen == dict(built=[], same=True, props=True, saved=(30.0, 160.0), planned=True, running=False, calls=[],
                        asked_for=READY, earlier=True, target=160.0, waiting=True), seen
    assert StepResult.NEXT not in done


def test_stopped_a_new_time_is_worked_out_at_start(monkeypatch):
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    server, step = make()
    with_parts(server, step)
    asyncio.run(step.on_start())
    server.step.profile[0].status = StepState.STOP              # stopped: running False
    change(step, StartTime="06:30")
    asyncio.run(step.on_start())
    assert step.asked_for == READY - timedelta(minutes=30)
    assert refused(server, step, Mode="Today", StartTime="21:00").startswith("Fri Oct 23, 21:00 has already passed")


def test_stopped_while_heating_nothing_heats_until_start_then_the_new_vessel_and_temperature(monkeypatch):
    server, step, wall = heating_with_parts(monkeypatch)
    live = server.step.find_by_id("bs")

    async def stop_change_start():
        await live.instance.stop()                               # no task: on_stop as the run loop's end calls it
        await step.on_stop()
        live.status = StepState.STOP
        calls = list(server.kettle.calls)
        await live.instance.change_settings(Heat_Kettle="bk", Ready_Temp=160)
        stopped = dict(calls=server.kettle.calls == calls, hlt=server.kettle.auto("hlt")[0],
                       bk=server.kettle.auto("bk")[0], mark=step.props.get(bs.BrewStart.HEATING_PROP))
        live.status = StepState.ACTIVE
        await live.instance.on_start()
        return stopped
    stopped = asyncio.run(stop_change_start())
    assert stopped == dict(calls=True, hlt=False, bk=False, mark=True), "something heated while it was stopped"
    assert step.heating is True and server.kettle.auto("bk") == (True, 160.0) and not server.kettle.auto("hlt")[0]


def test_after_a_reset_a_change_brings_nothing_back(monkeypatch):
    """What a reset cleared stays cleared. The step held the water, was stopped and reset: the instance still
    remembers it heated, but a change of settings goes by the step's state, not by what the instance remembers."""
    server, step, wall = held(monkeypatch)
    step.running = False
    asyncio.run(step.on_stop())
    asyncio.run(step.reset())
    server.step.profile[0].status = StepState.INITIAL
    assert not [k for k in step.props if str(k).startswith("_")]
    calls = list(server.kettle.calls)
    change(step, Ready_Temp=160, Heat_Kettle="bk", Volume=20)
    assert not [k for k in step.props if str(k).startswith("_")], "a change brought back what the reset cleared"
    assert server.kettle.calls == calls and not server.kettle.auto("bk")[0] and not server.kettle.auto("hlt")[0]
    wall.at = NIGHT                                            # the next evening's START
    asyncio.run(step.on_start())
    assert step.heating is False and step.target_start is not None, "START went back to heating, not to the wait"


# ----------------------------------------------------------- any speed

SIM_DAY = (datetime.now() + timedelta(days=52)).replace(hour=21, minute=0, second=0, microsecond=0)


class HandClock(clock.TimeSource):
    """The computer's clock, moved by hand: what a simulation's clock runs from."""

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


def at_speed(rate):
    """A brew evening on a simulation's clock at `rate`, weeks from the PC's: waiting, then each change at the same
    brewing-clock times. What it did, in brewing-clock terms."""
    hand = HandClock(SIM_DAY)
    replaced = clock.set_source(clock.ScaledTimeSource(rate, reference=hand))
    try:
        def brewing(minutes):
            hand.advance(minutes * 60 / rate)

        server, step = make(props=dict(READY_BY, Mode="Tomorrow", StartDate=""))
        with_parts(server, step)
        seen = []

        async def go():
            await step.on_start()
            step.running = True
            brewing(30)
            await step.change_settings(Volume=20, StartTime="06:30")
            seen.append((step.asked_for, step.target_start))
            brewing((step.target_start - bs.brewing_now()).total_seconds() / 60 + 1)
            await step._tick()
            seen.append((step.heating, server.kettle.auto("hlt")))
            brewing(20)
            await step.change_settings(Ready_Temp=160, Mode="Today", StartTime="07:00")
            seen.append((step.ready_at, server.kettle.auto("hlt"), step.summary))
            await step.change_settings(Timing="Start at")
            seen.append((step.asked_for, server.kettle.auto("hlt")[0], step.heating))
        asyncio.run(go())
        return seen, [n.message for n in server.notes]
    finally:
        clock.set_source(replaced)


def test_the_same_at_1x_and_60x_by_the_brewing_clock():
    once, said_once = at_speed(1.0)
    fast, said_fast = at_speed(60.0)
    assert once == fast and said_once == said_fast
    tomorrow = (SIM_DAY + timedelta(days=1)).replace(hour=6, minute=30)
    assert once[0][0] == tomorrow, "Tomorrow wasn't counted from the brewing clock"
    assert once[1] == (True, (True, 152.0))
    assert once[2][0] == tomorrow.replace(hour=7, minute=0) and once[2][1] == (True, 160.0)
    assert once[3] == (tomorrow.replace(hour=7, minute=0), True, True), "Start at stopped the heating"
