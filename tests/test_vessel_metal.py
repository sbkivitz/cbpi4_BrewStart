"""BrewStart 1.3.0.post5: the heating plan counts the HLT's own metal, from its figures on the Hardware page.

From CraftBeerPi 4.7.7.post65 each kettle may keep what it weighs empty and what it's made of (empty_weight,
material, specific_heat). Heat_Kettle's metal heats with its water, so a Ready by counts it, read as CraftBeerPi's
strike water model reads it: stainless steel 0.12 cal/g C, aluminium 0.22, other at its own specific heat, a weight
with no material taken as stainless steel. The rule is the brewer's: a figure only improves the plan. A weight that
is blank, 0 or less or unreadable, a material alone, a material it doesn't know, or other without a usable specific
heat gives nothing, and the plan is exactly as before - as on a core without the figures. As the heating starts, the
J/K counted goes into the record too (brewstart.steel_j_k), for Figures.

Those counting the metal fail on 1.3.0.post4.
"""

import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("cbpi")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cbpi4_BrewStart import schedule  # noqa: E402
from test_figures import heat_now, recorded, waiting  # noqa: E402
from test_heats_its_kettle import NIGHT, READY, Server, Wall, make  # noqa: E402

LB = 0.45359237
CAL = 4186.8
WATER_J_K = 15 * 3.785411784 * 0.995 * 4181.0        # 15 gal, as the plan counts it


def kettle_with(server, **figures):
    hlt = server.kettle.kettles["hlt"]
    for field, value in figures.items():
        setattr(hlt, field, value)
    return server


def plan(monkeypatch, **figures):
    Wall(NIGHT).install(monkeypatch)
    server, step = make(server=kettle_with(Server(), **figures))
    asyncio.run(step.on_start())
    return step.plan


@pytest.mark.parametrize("figures, kg, heat", [
    (dict(empty_weight=33, material="stainless_steel"), 33 * LB, 0.12),
    (dict(empty_weight=33, material="Stainless Steel"), 33 * LB, 0.12),
    (dict(empty_weight=33, material="aluminium"), 33 * LB, 0.22),
    (dict(empty_weight=33, material="other", specific_heat=0.09), 33 * LB, 0.09),
    (dict(empty_weight=33), 33 * LB, 0.12),
    (dict(empty_weight="33", material=" "), 33 * LB, 0.12),
], ids=["stainless", "words", "aluminium", "other", "no-material", "text-weight"])
def test_the_hlts_metal_is_counted_from_its_figures(monkeypatch, figures, kg, heat):
    assert schedule.vessel_j_k(figures.get("empty_weight"), figures.get("material"), figures.get("specific_heat"),
                               "F") == pytest.approx(kg * heat * CAL)
    plain, counted = plan(monkeypatch), plan(monkeypatch, **figures)
    assert counted.heat_minutes == pytest.approx(plain.heat_minutes * (WATER_J_K + kg * heat * CAL) / WATER_J_K,
                                                 rel=1e-9)
    assert counted.start < plain.start, "the heating starts earlier for the metal it heats too"


def test_on_a_celsius_brewery_the_weight_is_in_kilograms():
    assert schedule.vessel_j_k(15, "stainless_steel", None, "C") == pytest.approx(15 * 0.12 * CAL)


@pytest.mark.parametrize("figures", [
    dict(),
    dict(empty_weight=None, material=None, specific_heat=None, capacity=None),
    dict(empty_weight=0, material="stainless_steel"),
    dict(empty_weight=-33, material="stainless_steel"),
    dict(empty_weight="thirty", material="stainless_steel"),
    dict(empty_weight=float("nan"), material="stainless_steel"),
    dict(empty_weight=True, material="stainless_steel"),
    dict(material="aluminium"),
    dict(empty_weight=33, material="copper"),
    dict(empty_weight=33, material="other"),
    dict(empty_weight=33, material="other", specific_heat=0),
    dict(empty_weight=33, material="other", specific_heat=1.5),
], ids=["none", "all-blank", "zero", "negative", "text", "nan", "true", "material-alone", "unknown-material",
        "other-without-heat", "other-zero-heat", "other-above-water"])
def test_no_usable_figure_plans_exactly_as_before(monkeypatch, figures):
    assert plan(monkeypatch, **figures) == plan(monkeypatch)


def test_as_the_heating_starts_the_metal_counted_is_recorded(monkeypatch):
    server = kettle_with(Server(), empty_weight=33, material="stainless_steel")
    server, step, wall = waiting(monkeypatch, server=server)
    rows = recorded(server)
    heat_now(step, wall)
    series = dict(rows)
    assert series["brewstart.steel_j_k"] == pytest.approx(round(33 * LB * 0.12 * CAL, 1))
    assert series["brewstart.planned_min"] == pytest.approx(step._plan(READY).heat_minutes, abs=0.01)


def test_without_figures_the_record_is_as_before(monkeypatch):
    server, step, wall = waiting(monkeypatch)
    rows = recorded(server)
    heat_now(step, wall)
    assert [s for s, _ in rows] == ["brewstart.planned_min", "brewstart.ready_by", "brewstart.litres",
                                    "brewstart.watts", "brewstart.efficiency"]
