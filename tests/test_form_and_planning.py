"""BrewStart's form, and when it starts - 1.2.0.post4, Scott, 29 Oct 2026.

"If my language is English, I should not see other languages." "Use the OS or
browser's native date/time selectors." "Setting mode for 'Today' and then
asking for a StartDate doesn't make sense. Options should include Today,
Tomorrow, Date. Not daily." Strike_Kettle and Strike_Volume not needed - all
the strike and sparge water is heated together in the HLT; don't ask for
Efficiency_Pct or Margin_Minutes; the Campden reminder when the step is saved.

Two things the requests imply, tested here too:

- Today and Tomorrow mean nothing on their own. After a power cut the server
  asks "Resume brew?", and a step that worked Tomorrow out again at 05:30 on
  brew day would wait until the day after. So the moment worked out is kept
  with the step, and a resumed step waits for that.
- Today with a time already passed - chosen in the evening for the next
  morning, as Today meant until now - must not start heating at once.

Negative controls: 1.2.0.post3 (4c14a92), and the kept moment taken away.
"""

import asyncio
import json
import re
import types

import pytest

pytest.importorskip("cbpi")

from cbpi.api import Property  # noqa: E402

import cbpi4_BrewStart.brew_start as bs  # noqa: E402
from cbpi4_BrewStart.step_status_actor import status_actor_properties  # noqa: E402
from test_step_clock import (HERMS, NIGHT, REAL_DATETIME, Cbpi, frozen,  # noqa: E402
                             hlt_and_tun, load_control, step_with)

POST3 = "4c14a92"
PLANNED = bs.BrewStart.PLANNED_PROP
GERMAN = re.compile(r"[äöüßÄÖÜ]|\b(und|oder|der|die|das|nur|bei|wenn|Sprache|Wasser|"
                    r"Kessel|Minuten|Heizstab|Einmaisch\w*)\b", re.IGNORECASE)


def labels(props):
    return [p.label for p in props]


def by_label(props):
    return {p.label: p for p in props}


# ----------------------------------------------------------- the form

def test_the_form_asks_only_for_what_applies_to_scotts_way():
    assert labels(bs.step_properties("en")) == [
        "Timing", "Mode", "StartDate", "StartTime", "Heat_Kettle", "Ready_Temp",
        "Volume", "Volume_Unit", "Heater_Watts", "Campden_Reminder", "NotifyBefore",
        "AutoNext"]


def test_mode_offers_today_tomorrow_and_date_not_daily():
    assert by_label(bs.step_properties("en"))["Mode"].options == ["Today", "Tomorrow", "Date"]


def test_an_english_form_has_no_other_language():
    for p in bs.step_properties("en") + status_actor_properties("en"):
        assert p.description or p.label == "StartDate", p.label
        for text in (p.description, p.display_label):
            assert not GERMAN.search(text), (p.label, text)


def test_a_german_form_is_german_throughout():
    english = by_label(bs.step_properties("en"))
    for p in bs.step_properties("de"):
        assert p.description != english[p.label].description or not p.description, p.label
        assert p.display_label != english[p.label].display_label, p.label
    assert labels(bs.step_properties("de")) == labels(bs.step_properties("en"))


def test_fields_are_shown_only_where_they_apply():
    form = by_label(bs.step_properties("en"))
    assert form["StartDate"].show_if == {"Mode": ["Date", "DateTime"]}
    for label in ("Heat_Kettle", "Ready_Temp", "Heater_Watts"):
        assert form[label].show_if == {"Timing": "Ready by"}, label
    for label in ("Volume", "Volume_Unit"):  # the Campden dose needs the volume too
        assert form[label].show_if == [{"Timing": "Ready by"}, {"Campden_Reminder": "Yes"}]
    # Ready by always goes on by itself, so the choice is only offered for Start at.
    assert form["AutoNext"].show_if == {"Timing": ["", "Start at"]}
    for label in ("Mode", "StartTime", "Timing", "Campden_Reminder", "NotifyBefore"):
        assert getattr(form[label], "show_if", None) is None, label


def test_the_server_describes_pickers_and_rules_to_the_interface():
    """Through the server's own parser, as /step2/ hands it to the browser."""
    from cbpi.controller.plugin_controller import PluginController

    parser = PluginController.__new__(PluginController)
    form = type("Form", (), {"cbpi_parameters": bs.step_properties("en")})
    described = {d["label"]: d for d in parser._parse_step_props(form, "BrewStart")["properties"]}
    assert described["StartDate"]["type"] == "date"
    assert described["StartTime"]["type"] == "time"
    assert described["StartDate"]["show_if"] == {"Mode": ["Date", "DateTime"]}
    assert described["Heat_Kettle"]["show_if"] == {"Timing": "Ready by"}
    assert "show_if" not in described["Mode"]
    assert "Format:" not in described["StartDate"]["description"]
    assert (described["StartTime"]["display_label"], described["Mode"]["display_label"]) == \
        ("Time", "Day")
    assert described["Mode"]["default_value"] == "Today"
    assert described["Heater_Watts"]["unit"] == "W"


def test_without_the_servers_pickers_the_fields_say_their_format(monkeypatch):
    """An upstream server has no Property.Date: a text field, format spelled out."""
    monkeypatch.setattr(bs, "DateProperty", Property.Text)
    monkeypatch.setattr(bs, "TimeProperty", Property.Text)
    monkeypatch.setattr(bs, "NATIVE_PICKERS", False)
    form = by_label(bs.step_properties("en"))
    assert type(form["StartDate"]) is Property.Text
    assert "YYYY-MM-DD" in form["StartDate"].description
    assert "HH:MM" in form["StartTime"].description


def test_the_status_actors_form():
    form = by_label(status_actor_properties("en"))
    assert labels(form.values()) == ["StepKind", "MatchMode", "MatchValue"]
    assert form["MatchMode"].show_if == form["MatchValue"].show_if == {"StepKind": "Custom"}
    assert getattr(form["StepKind"], "show_if", None) is None
    assert [p.display_label for p in form.values()] == ["Step kind", "Match by", "Match text"]


# ----------------------------------------------------------- after Apple's design principles
#
# Scott sent https://developer.apple.com/design/human-interface-guidelines/
# design-principles on 29 Oct, looking at this form: "choose exactly the words
# you need to label a control", "help people recover from mistakes", "provide
# clear feedback".

def test_every_field_is_named_in_words():
    for p in bs.step_properties("en") + status_actor_properties("en"):
        assert p.display_label, p.label
        assert "_" not in p.display_label and not re.search(r"[a-z][A-Z]", p.display_label), \
            (p.label, p.display_label)
    assert [p.display_label for p in bs.step_properties("en")] == [
        "Timing", "Day", "Date", "Time", "Kettle", "Temperature", "Water volume",
        "Volume unit", "Element power", "Campden reminder", "Heads-up", "Go on by itself"]


def test_numbers_carry_their_units():
    fahrenheit = by_label(bs.step_properties("en", "F"))
    assert (fahrenheit["Ready_Temp"].unit, fahrenheit["Heater_Watts"].unit,
            fahrenheit["NotifyBefore"].unit) == ("°F", "W", "min")
    assert by_label(bs.step_properties("en"))["Ready_Temp"].unit == "°C"


def test_a_new_step_starts_from_the_defaults_the_step_uses_for_a_blank():
    form = by_label(bs.step_properties("en"))
    assert {k: form[k].default_value for k in ("Timing", "Mode", "Campden_Reminder",
                                               "AutoNext")} == \
        {"Timing": "Start at", "Mode": "Today", "Campden_Reminder": "No", "AutoNext": "Yes"}


def test_ready_by_always_goes_on_whatever_autonext_says(monkeypatch):
    """With AutoNext No it waited for a click at the heating start, so the water
    was never heated; the rig page carried a bold warning instead."""
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = hlt_and_tun()
    step = step_with(dict(HERMS, AutoNext="No"), cbpi)

    async def go():
        await step.on_start()
        return await step._tick(step.target_start, 1.0)

    assert asyncio.run(go()) is True
    assert not [m for m, action in cbpi.notes if action], cbpi.notes


def test_control_post3_ready_by_with_autonext_no_never_heated(monkeypatch):
    old = load_control(POST3)
    monkeypatch.setattr(old, "datetime", frozen(NIGHT))
    step = step_with(dict(HERMS, AutoNext="No"), hlt_and_tun(), module=old)

    async def go():
        await step.on_start()
        return await step._tick(step.target_start, 1.0)

    assert asyncio.run(go()) is False


def saved(monkeypatch, props, at=NIGHT, cbpi=None):
    monkeypatch.setattr(bs, "datetime", frozen(at))
    cbpi = cbpi or hlt_and_tun()
    asyncio.run(step_with(props, cbpi).on_props_saved())
    return cbpi


def test_saving_today_for_a_time_already_gone_says_so_at_once(monkeypatch):
    cbpi = saved(monkeypatch, {"Mode": "Today", "StartTime": "07:00"})
    assert cbpi.said("Saved, but Fri Oct 23, 07:00 has already passed. For tomorrow, "
                     "set Day to Tomorrow."), cbpi.notes


def test_saving_a_date_already_gone_says_so(monkeypatch):
    cbpi = saved(monkeypatch, {"Mode": "Date", "StartDate": "2026-10-20", "StartTime": "07:00"})
    assert cbpi.said("Saved, but Tue Oct 20, 07:00 has already passed. Choose a later date"), \
        cbpi.notes


def test_saving_ready_by_without_what_it_needs_says_what(monkeypatch):
    for props, says in (
            (dict(HERMS, Heat_Kettle=""), "Ready by needs a Kettle"),
            (dict(HERMS, Heater_Watts=""), "Ready by needs the Element power"),
            (dict(HERMS, Volume=0), "Ready by needs the Water volume"),
            ({"Mode": "Tomorrow", "StartTime": ""}, "The time is blank")):
        cbpi = saved(monkeypatch, props)
        assert cbpi.said("Saved, but this step cannot start as set: " + says), cbpi.notes


def test_saving_good_settings_says_nothing_more(monkeypatch):
    cbpi = saved(monkeypatch, dict(HERMS, Mode="Tomorrow", StartTime="07:00"))
    assert cbpi.notes == [], cbpi.notes


def test_setup_builds_the_forms_in_the_servers_language(monkeypatch):
    import cbpi4_BrewStart as plugin
    from cbpi4_BrewStart.step_status_actor import StepStatusActor

    monkeypatch.setattr(bs.BrewStart, "cbpi_parameters", bs.BrewStart.cbpi_parameters)
    monkeypatch.setattr(StepStatusActor, "cbpi_parameters", StepStatusActor.cbpi_parameters)
    registered = {}
    cbpi = types.SimpleNamespace(
        config=types.SimpleNamespace(
            get=lambda k, d=None: "Deutsch" if k == bs.LANGUAGE_SETTING else d),
        plugin=types.SimpleNamespace(register=lambda name, cls: registered.setdefault(name, cls)))
    plugin.setup(cbpi)
    assert {"BrewStart", "BrewStartSettings", "StepStatusActor"} <= set(registered)
    german = by_label(registered["BrewStart"].cbpi_parameters)
    assert german["StartTime"].description == bs.t("prop_start_time", "de")
    assert by_label(registered["StepStatusActor"].cbpi_parameters)["StepKind"].description \
        == bs.t("actor_step_kind", "de")


def test_the_language_setting_is_added_once_in_english():
    from cbpi.api.config import ConfigType
    from cbpi4_BrewStart.settings import BrewStartSettings

    added = []

    class Config:
        def __init__(self, value):
            self.value = value

        def get(self, name, default=None):
            return self.value if name == bs.LANGUAGE_SETTING else default

        async def add(self, name, value, type, description, source, options):
            added.append(dict(name=name, value=value, type=type, description=description,
                              options=options))

    async def start(config):
        await BrewStartSettings(types.SimpleNamespace(config=config))._task

    asyncio.run(start(Config(None)))
    assert len(added) == 1
    assert added[0]["name"] == "BREWSTART_LANGUAGE" and added[0]["value"] == "English"
    assert added[0]["type"] == ConfigType.SELECT
    assert [o["value"] for o in added[0]["options"]] == ["English", "Deutsch"]
    assert not GERMAN.search(added[0]["description"])
    asyncio.run(start(Config("Deutsch")))
    assert len(added) == 1, "a brewer's choice was overwritten"


# ----------------------------------------------------------- when

def ticks(step, start, minutes):
    async def go():
        await step.on_start()
        return [await step._tick(start + bs.timedelta(minutes=m), 1.0 + m * 60)
                for m in range(minutes)]
    return asyncio.run(go())


def test_tomorrow_is_the_next_morning(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = Cbpi()
    step = step_with({"Mode": "Tomorrow", "StartTime": "07:00"}, cbpi)
    asyncio.run(step.on_start())
    assert step.target_start == REAL_DATETIME(2026, 10, 24, 7, 0)
    assert cbpi.said("Brew start scheduled for Sat Oct 24, 07:00"), cbpi.notes


def test_a_date_and_time_from_the_pickers(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    step = step_with({"Mode": "Date", "StartDate": "2026-10-25", "StartTime": "06:30"}, Cbpi())
    asyncio.run(step.on_start())
    assert step.target_start == REAL_DATETIME(2026, 10, 25, 6, 30)


def test_today_chosen_in_the_evening_for_the_morning_does_not_heat_now(monkeypatch):
    """What Today meant until now. It would hand over at once - on a Ready by
    profile, to the step that heats - so it waits, and says what to change."""
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = hlt_and_tun()
    step = step_with(dict(HERMS, Mode="Today", StartDate="", StartTime="07:00"), cbpi)
    assert not any(ticks(step, NIGHT, 60))
    offers = [m for m, action in cbpi.notes if action]
    assert len(offers) == 1, cbpi.notes
    assert "Fri Oct 23, 07:00 has already passed" in offers[0]
    assert "set Day to Tomorrow" in offers[0]
    assert step.summary == "Fri Oct 23, 07:00 has passed - waiting for Next Step"


def test_a_blank_start_time_never_starts(monkeypatch):
    """Until 1.2.0.post4 a blank StartTime quietly meant 06:00."""
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = Cbpi()
    step = step_with({"Mode": "Tomorrow", "StartTime": ""}, cbpi)
    assert not any(ticks(step, NIGHT, 12 * 60))
    offers = [m for m, action in cbpi.notes if action]
    assert len(offers) == 1 and "The time is blank" in offers[0], cbpi.notes


def test_messages_follow_the_servers_language(monkeypatch):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = Cbpi(settings={"BREWSTART_LANGUAGE": "Deutsch"})
    asyncio.run(step_with({"Mode": "Tomorrow", "StartTime": "07:00"}, cbpi).on_start())
    assert cbpi.said("Braustart geplant für 24.10.2026 07:00"), cbpi.notes


# ----------------------------------------------------------- remembering

def restart(step, cbpi, step_id=None):
    """The server back after a power cut: a new instance of the step, built
    from what it saved to step_data.json - JSON there and back."""
    props = json.loads(json.dumps(dict(step.props)))
    return bs.BrewStart(cbpi, step_id or step.id, step.name, props, lambda s, r: None)


def started_tonight(monkeypatch, props, cbpi=None):
    monkeypatch.setattr(bs, "datetime", frozen(NIGHT))
    cbpi = cbpi or Cbpi()
    step = step_with(props, cbpi)
    asyncio.run(step.on_start())
    return step, cbpi


TOMORROW_7 = {"Mode": "Tomorrow", "StartTime": "07:00"}


def test_the_moment_is_kept_and_saved(monkeypatch):
    step, cbpi = started_tonight(monkeypatch, TOMORROW_7)
    assert step.props[PLANNED] == "s1|Tomorrow|07:00||2026-10-24T07:00:00"
    assert cbpi.saves == 1


def test_resumed_at_dawn_it_waits_for_the_same_morning(monkeypatch):
    step, cbpi = started_tonight(monkeypatch, TOMORROW_7)
    monkeypatch.setattr(bs, "datetime", frozen(REAL_DATETIME(2026, 10, 24, 5, 30)))
    again = restart(step, cbpi)
    asyncio.run(again.on_start())
    assert again.target_start == REAL_DATETIME(2026, 10, 24, 7, 0)


def test_control_without_the_kept_moment_it_waits_a_day_too_long(monkeypatch):
    step, cbpi = started_tonight(monkeypatch, TOMORROW_7)
    del step.props[PLANNED]
    monkeypatch.setattr(bs, "datetime", frozen(REAL_DATETIME(2026, 10, 24, 5, 30)))
    again = restart(step, cbpi)
    asyncio.run(again.on_start())
    assert again.target_start == REAL_DATETIME(2026, 10, 25, 7, 0)


def test_resumed_after_its_time_it_goes_on(monkeypatch):
    """The brewer pressed Resume after the time: set up properly, so it goes on."""
    step, cbpi = started_tonight(monkeypatch, TOMORROW_7)
    late = REAL_DATETIME(2026, 10, 24, 7, 30)
    monkeypatch.setattr(bs, "datetime", frozen(late))
    again = restart(step, cbpi)
    assert ticks(again, late, 1) == [True]
    assert not cbpi.said("nothing starts by itself"), cbpi.notes


def test_ready_by_resumed_too_late_to_be_on_time_heats_at_once(monkeypatch):
    step, cbpi = started_tonight(monkeypatch, dict(HERMS, **TOMORROW_7), hlt_and_tun())
    late = REAL_DATETIME(2026, 10, 24, 6, 30)
    monkeypatch.setattr(bs, "datetime", frozen(late))
    again = restart(step, cbpi)
    assert ticks(again, late, 1) == [True]
    assert again.ready_at == REAL_DATETIME(2026, 10, 24, 7, 0)
    assert cbpi.said("Heating has to start now"), cbpi.notes


def test_reset_forgets_the_moment(monkeypatch):
    step, cbpi = started_tonight(monkeypatch, TOMORROW_7)
    asyncio.run(step.reset())
    assert PLANNED not in step.props
    next_evening = REAL_DATETIME(2026, 10, 24, 22, 0)
    monkeypatch.setattr(bs, "datetime", frozen(next_evening))
    again = restart(step, cbpi)
    asyncio.run(again.on_start())
    assert again.target_start == REAL_DATETIME(2026, 10, 25, 7, 0)


def test_changed_settings_are_worked_out_afresh(monkeypatch):
    step, cbpi = started_tonight(monkeypatch, TOMORROW_7)
    step.props["StartTime"] = "06:30"
    later = REAL_DATETIME(2026, 10, 23, 23, 0)
    monkeypatch.setattr(bs, "datetime", frozen(later))
    again = restart(step, cbpi)
    asyncio.run(again.on_start())
    assert again.target_start == REAL_DATETIME(2026, 10, 24, 6, 30)


def test_a_moment_copied_into_another_step_is_not_its_own(monkeypatch):
    """Saved to the recipe book with the profile and loaded back as a new step
    the next evening: taken as its own, this morning's 07:00 would count as a
    brew resumed late - and go on at once."""
    step, cbpi = started_tonight(monkeypatch, TOMORROW_7)
    next_evening = REAL_DATETIME(2026, 10, 24, 22, 0)
    monkeypatch.setattr(bs, "datetime", frozen(next_evening))
    copy = restart(step, cbpi, step_id="s2")
    assert ticks(copy, next_evening, 1) == [False]
    assert copy.target_start == REAL_DATETIME(2026, 10, 25, 7, 0)


def test_a_moment_from_days_ago_is_not_used(monkeypatch):
    step, cbpi = started_tonight(monkeypatch, TOMORROW_7)
    days_later = REAL_DATETIME(2026, 10, 26, 22, 0)
    monkeypatch.setattr(bs, "datetime", frozen(days_later))
    again = restart(step, cbpi)
    asyncio.run(again.on_start())
    assert again.target_start == REAL_DATETIME(2026, 10, 27, 7, 0)


# ----------------------------------------------------------- controls: 1.2.0.post3

def test_control_post3_form_was_bilingual_and_asked_for_more():
    old = load_control(POST3)
    form = old.BrewStart.cbpi_parameters
    assert any(GERMAN.search(p.description or "") for p in form)
    assert {"Strike_Kettle", "Strike_Volume", "Efficiency_Pct", "Margin_Minutes",
            "Language"} <= set(labels(form))
    assert by_label(form)["Mode"].options == ["Today", "DateTime", "Daily"]


def test_control_post3_handed_over_at_once_for_a_time_already_past(monkeypatch):
    old = load_control(POST3)
    monkeypatch.setattr(old, "datetime", frozen(NIGHT))
    step = step_with({"StartDate": "2026-10-23 21:00"}, Cbpi(), module=old)
    assert ticks(step, NIGHT, 1) == [True]


def test_control_post3_reminded_of_campden_when_the_brew_started_not_on_save(monkeypatch):
    old = load_control(POST3)
    monkeypatch.setattr(old, "datetime", frozen(NIGHT))
    cbpi = hlt_and_tun()
    step = step_with(dict(HERMS, Campden_Reminder="Yes"), cbpi, module=old)
    asyncio.run(step.on_props_saved())
    assert not cbpi.said("Campden"), cbpi.notes
    asyncio.run(step.on_start())
    assert cbpi.said("Campden"), cbpi.notes
