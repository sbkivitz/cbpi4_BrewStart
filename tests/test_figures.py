"""BrewStart 1.3.0.post3: what CraftBeerPi's Figures page needs from BrewStart.

B1, plan with a learned efficiency: the server setting BREWSTART_EFFICIENCY, in %, blank for the built-in 90 %.
USE on Figures sets it from past brews and BACK TO blanks it. BrewStart registers it, keeps a saved value, plans
with it - a waiting step within a minute of a change - and says so wherever it says how long the water takes.

B2, keep what it planned and what happened: rows in CraftBeerPi's logs (cbpi.log.log_control_data) as the heating
starts - brewstart.planned_min, .ready_by, .litres, .watts, .efficiency - and brewstart.ready, the reading, the first
time the water reads Ready_Temp.

Each fails on 1.3.0.post2 (aee37e6).
"""

import asyncio
import re
import sys
import types
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("cbpi")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cbpi4_BrewStart.brew_start as bs  # noqa: E402
from cbpi.api.config import ConfigType  # noqa: E402
from test_heats_its_kettle import (NIGHT, READY, READY_BY, REAL_DATETIME, Server, Wall, item, make,  # noqa: E402
                                   restart, rig_page_9b, tick)

DESCRIPTION = ("The share of the HLT element's power that heats the water, in %. Blank: the built-in 90 %. "
               "USE on Figures sets it from past brews.")
L_PER_GAL = 3.785411784


def settings(server, **values):
    """The server's settings as the core reads them: blank is the default, as ConfigController.get has it."""
    values.setdefault("TEMP_UNIT", "F")

    def get(name, default=None):
        value = values.get(name)
        return default if value is None or value == "" else value
    server.config = types.SimpleNamespace(get=get, values=values)
    return values


def recorded(server):
    rows = []
    server.log = types.SimpleNamespace(log_control_data=lambda series, value: rows.append((series, value)))
    return rows


def waiting(monkeypatch, efficiency=None, language=None, server=None, **kwargs):
    """Started the night before, waiting for its heating start: (server, step, wall)."""
    wall = Wall(NIGHT)
    wall.install(monkeypatch)
    server = server or Server()
    values = settings(server)
    if efficiency is not None:
        values["BREWSTART_EFFICIENCY"] = efficiency
    if language is not None:
        values["BREWSTART_LANGUAGE"] = language
    server, step = make(server=server, **kwargs)
    asyncio.run(step.on_start())
    step.running = True
    return server, step, wall


def heat_now(step, wall):
    """Its heating start come."""
    wall.at = step.target_start + timedelta(seconds=1)
    return asyncio.run(step._tick(wall.at, 100000.0))


# ----------------------------------------------------------- B1: the setting

class Config:
    """ConfigController, as far as BrewStartSettings reaches into it."""

    def __init__(self, **saved):
        self.cache = {}
        self.added = []
        for name, (value, description) in saved.items():
            self.cache[name] = types.SimpleNamespace(value=value, description=description, type=ConfigType.NUMBER)

    def get(self, name, default=None):
        entry = self.cache.get(name)
        return default if entry is None or entry.value in (None, "") else entry.value

    async def add(self, name, value, type, description, source="craftbeerpi", options=None):
        self.added.append(dict(name=name, value=value, type=type, description=description, source=source))
        self.cache[name] = types.SimpleNamespace(value=value, description=description, type=type)


def register(config):
    from cbpi4_BrewStart.settings import BrewStartSettings

    async def go():
        await BrewStartSettings(types.SimpleNamespace(config=config))._task
    asyncio.run(go())
    return [a for a in config.added if a["name"] == "BREWSTART_EFFICIENCY"]


def test_the_efficiency_setting_is_added_blank_with_its_description():
    added = register(Config())
    assert added == [dict(name="BREWSTART_EFFICIENCY", value="", type=ConfigType.NUMBER, description=DESCRIPTION,
                          source="cbpi4_BrewStart")]


def test_a_saved_efficiency_is_kept_and_not_written_again():
    assert register(Config(BREWSTART_EFFICIENCY=(85, DESCRIPTION))) == [], "written again on every start"
    added = register(Config(BREWSTART_EFFICIENCY=(85, "an older version's words")))
    assert [(a["value"], a["description"]) for a in added] == [(85, DESCRIPTION)], "the saved 85 was lost"


def test_the_setting_is_described_in_brewstarts_language():
    config = Config()
    config.cache["BREWSTART_LANGUAGE"] = types.SimpleNamespace(value="Deutsch", description="", type=None)
    added = register(config)
    assert added[0]["description"] == bs.t("setting_efficiency", "de") != DESCRIPTION


# ----------------------------------------------------------- B1: planning with it

def test_it_plans_with_the_setting(monkeypatch):
    _, built_in, _ = waiting(monkeypatch)
    _, learned, _ = waiting(monkeypatch, efficiency="85")
    _, fraction, _ = waiting(monkeypatch, efficiency=0.85)
    assert learned.plan.heat_minutes == pytest.approx(built_in.plan.heat_minutes * 0.90 / 0.85)
    assert fraction.plan.heat_minutes == pytest.approx(learned.plan.heat_minutes)
    assert learned.target_start < built_in.target_start


@pytest.mark.parametrize("unusable", ["", "abc", "0", "150", "5"])
def test_an_unusable_setting_plans_with_the_built_in(monkeypatch, unusable):
    _, built_in, _ = waiting(monkeypatch)
    server, step, _ = waiting(monkeypatch, efficiency=unusable)
    assert step.plan.heat_minutes == pytest.approx(built_in.plan.heat_minutes)
    assert "from past brews" not in server.notes[0].message


def test_a_waiting_step_plans_again_with_a_new_figure_within_a_minute(monkeypatch):
    server, step, wall = waiting(monkeypatch)
    before, built_in = step.target_start, step.plan.heat_minutes
    server.config.values["BREWSTART_EFFICIENCY"] = 80          # USE on Figures
    wall.at = NIGHT + timedelta(minutes=2)
    asyncio.run(step._tick(wall.at, step._planned_at + step.REPLAN_INTERVAL_S))
    assert step.plan.heat_minutes == pytest.approx(built_in * 0.90 / 0.80)
    assert step.target_start == READY - timedelta(minutes=step.plan.heat_minutes + 10)
    assert step.target_start < before - timedelta(minutes=3), (before, step.target_start)
    server.config.values["BREWSTART_EFFICIENCY"] = ""          # BACK TO: the built-in again
    asyncio.run(step._tick(wall.at, step._planned_at + step.REPLAN_INTERVAL_S))
    assert abs((step.target_start - before).total_seconds()) < 1


def test_the_plan_says_the_efficiency_from_past_brews(monkeypatch):
    server, step, _ = waiting(monkeypatch, efficiency="85")
    planned = server.notes[0].message
    assert re.search(r"takes about \d+ min, at 85 % from past brews, plus 10 min spare\.$", planned), planned
    server, _, _ = waiting(monkeypatch)
    assert re.search(r"takes about \d+ min, plus 10 min spare\.$", server.notes[0].message)
    server, _, _ = waiting(monkeypatch, efficiency="87.5", language="Deutsch")
    assert re.search(r"dauert ca\. \d+ min, bei 87\.5 % aus früheren Suden, plus 10 min Reserve\.$",
                     server.notes[0].message), server.notes[0].message


def test_heating_late_says_it_too(monkeypatch):
    Wall(REAL_DATETIME(2026, 10, 24, 6, 40)).install(monkeypatch)
    server = Server()
    settings(server, BREWSTART_EFFICIENCY="85")
    server, step = make(server=server)
    asyncio.run(step.on_start())
    late = [n.message for n in server.notes if n.message.startswith("Heating has to start now:")]
    assert len(late) == 1 and re.search(r"needs about \d+ min, at 85 % from past brews, so the water", late[0]), late


def test_saving_the_form_plans_with_it_and_finds_nothing_wrong(monkeypatch):
    Wall(NIGHT).install(monkeypatch)
    server = Server()
    settings(server, BREWSTART_EFFICIENCY="85")
    _, step = make(server=server)
    seen = []
    real = bs.plan_ready_by

    def plan_ready_by(*args, **kwargs):
        seen.append(kwargs.get("efficiency"))
        return real(*args, **kwargs)
    monkeypatch.setattr(bs, "plan_ready_by", plan_ready_by)
    assert step._settings_problem(NIGHT) is None
    assert seen == ["85"]


# ----------------------------------------------------------- B2: the record

def test_as_the_heating_starts_it_records_its_plan(monkeypatch):
    server, step, wall = waiting(monkeypatch, efficiency="85")
    rows = recorded(server)
    assert heat_now(step, wall) is False and step.heating is True
    series = dict(rows)
    assert [s for s, _ in rows] == ["brewstart.planned_min", "brewstart.ready_by", "brewstart.litres",
                                    "brewstart.watts", "brewstart.efficiency"]
    assert series["brewstart.planned_min"] == pytest.approx(step.plan.heat_minutes, abs=0.01)
    assert series["brewstart.ready_by"] == pytest.approx(READY.timestamp())
    assert series["brewstart.litres"] == pytest.approx(15 * L_PER_GAL, abs=0.001)
    assert series["brewstart.watts"] == 5346.0
    assert series["brewstart.efficiency"] == 0.85
    rows.clear()
    for _ in range(3):
        tick(step, wall, 1)
    assert rows == [], "recorded again while heating"


def test_the_built_in_is_recorded_as_090(monkeypatch):
    server, step, wall = waiting(monkeypatch)
    rows = recorded(server)
    heat_now(step, wall)
    assert dict(rows)["brewstart.efficiency"] == 0.9


def test_the_first_time_the_water_reads_ready_temp_its_reading_is_recorded(monkeypatch):
    server, step, wall = waiting(monkeypatch)
    rows = recorded(server)
    heat_now(step, wall)
    rows.clear()
    server.water(140.0)
    tick(step, wall, 5)
    assert rows == []
    server.water(151.7)
    tick(step, wall, 5)
    assert rows == [("brewstart.ready", 151.7)]
    for _ in range(3):
        tick(step, wall, 1)
    asyncio.run(step.change_target(155.0))                      # USE while holding: asked again at 155
    server.water(155.0)
    tick(step, wall, 1)
    assert len(server.questions()) == 2 and rows == [("brewstart.ready", 151.7)], rows


def test_after_a_restart_nothing_is_recorded_twice(monkeypatch):
    server, step, wall = waiting(monkeypatch)
    rows = recorded(server)
    heat_now(step, wall)
    server.water(152.0)
    tick(step, wall, 1)
    assert [s for s, _ in rows].count("brewstart.ready") == 1
    rows.clear()
    server.kettle.kettles["hlt"].instance = None
    again = restart(step, server, wall)
    for minutes in (0, 1, 5):
        tick(again, wall, minutes)
    assert rows == [], rows


def test_heat_now_records_the_plan_from_the_water_then(monkeypatch):
    Wall(NIGHT).install(monkeypatch)
    server = Server(hlt=60.0)
    settings(server)
    server, step = make(server=server, props=dict(READY_BY, Mode="Today", StartTime="07:00"))
    asyncio.run(step.on_start())
    step.running = True
    rows = recorded(server)
    server.water(100.0)                                         # warmer by the time Heat now is pressed
    asyncio.run(server.questions()[0].action[0].method())
    planned = dict(rows)["brewstart.planned_min"]
    assert planned == pytest.approx(step.plan.heat_minutes * (152 - 100) / (152 - 60), abs=0.01)


def test_before_a_strike_water_step_on_the_hlt_the_plan_is_recorded_at_the_handover(monkeypatch):
    server, step, wall = waiting(monkeypatch, after=rig_page_9b)
    rows = recorded(server)
    heat_now(step, wall)
    assert [s for s, _ in rows] == ["brewstart.planned_min", "brewstart.ready_by", "brewstart.litres",
                                    "brewstart.watts", "brewstart.efficiency"]
    assert server.kettle.calls == []


def test_start_at_records_nothing(monkeypatch):
    server, step, wall = waiting(monkeypatch, props={"Mode": "Date", "StartDate": "2026-10-24",
                                                     "StartTime": "07:00", "Timing": "Start at",
                                                     "NotifyBefore": 0},
                                 after=lambda: [item("mash-in", "MashInStep", "mt")])
    rows = recorded(server)
    heat_now(step, wall)
    assert rows == []


def test_the_record_reaches_the_disk_soon(monkeypatch):
    """CraftBeerPi's CSV log batches a series' rows until its next row, or a minute: one row a brew would wait in
    memory until something read it, or be lost to a power cut."""
    server, step, wall = waiting(monkeypatch)
    rows = recorded(server)
    flushed = []

    async def flush(name):
        flushed.append(name)
    server.log._flush_datalogger = flush

    async def go():
        wall.at = step.target_start + timedelta(seconds=1)
        await step._tick(wall.at, 100000.0)
        await asyncio.sleep(2.3)
    asyncio.run(go())
    assert rows and sorted(flushed) == sorted(s for s, _ in rows)


def test_a_core_without_the_record_brews_on(monkeypatch):
    server, step, wall = waiting(monkeypatch)
    server.log = types.SimpleNamespace()                        # no log_control_data
    heat_now(step, wall)
    server.water(152.0)
    tick(step, wall, 1)
    assert step.heating is True and len(server.questions()) == 1

    def broken(series, value):
        raise RuntimeError("disk full")
    server2, step2, wall2 = waiting(monkeypatch)
    server2.log = types.SimpleNamespace(log_control_data=broken)
    heat_now(step2, wall2)
    assert step2.heating is True
