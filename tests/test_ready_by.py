"""Ready by: when heating must start for the water to be ready on time.

The heat-up model is checked against numbers from a measured rig, not against
itself. A model of a 20 gal HLT measured on 9-10 Oct 2026 - 5346 W element,
237,686 J/K, 9 W/K of losses with its pump running, a probe 65 s behind the
water - driven by PIDBoil's controller, took this long to bring the water to
within half a degree of the target:

    15 gal, 60 -> 170 F    50.5 min
    15 gal, 50 -> 170 F    54.6 min
    10 gal, 60 -> 170 F    33.7 min

HEAT_EFFICIENCY must reproduce all three to within a minute. The plain "water
at full power" sum is the negative control: over five minutes short on the
15 gal case, which on a brew morning is water that is not ready.
"""

from datetime import datetime, timedelta

import pytest

from cbpi4_BrewStart.schedule import (
    ASSUMED_COLD_C,
    DEFAULT_MARGIN_MIN,
    HEAT_EFFICIENCY,
    L_PER_GAL,
    campden_tablets,
    efficiency_fraction,
    format_tablets,
    format_temp,
    heat_up_minutes,
    litres,
    margin_minutes,
    plan_ready_by,
    to_unit,
    volume_given,
    volume_label,
)

WATTS = 5346
READY = datetime(2026, 10, 24, 7, 0)

MEASURED_MODEL = [
    ((15, 60, 170), 50.5),
    ((15, 50, 170), 54.6),
    ((10, 60, 170), 33.7),
]


@pytest.mark.parametrize("case,model_minutes", MEASURED_MODEL)
def test_heat_up_matches_the_measured_rig_model(case, model_minutes):
    gal, start, target = case
    got = heat_up_minutes(gal, "gal", start, target, "F", WATTS)
    assert abs(got - model_minutes) <= 1.0, (case, got, model_minutes)


def test_control_water_alone_at_full_power_is_short():
    got = heat_up_minutes(15, "gal", 60, 170, "F", WATTS, efficiency=1.0)
    assert 50.5 - got >= 5.0, got


def test_celsius_and_litres_give_the_same_answer():
    in_f = heat_up_minutes(15, "gal", 60, 170, "F", WATTS)
    in_c = heat_up_minutes(15 * L_PER_GAL, "L", (60 - 32) * 5 / 9,
                           (170 - 32) * 5 / 9, "C", WATTS)
    assert in_f == pytest.approx(in_c)


def test_a_blank_volume_unit_follows_the_temperature_unit():
    assert litres(10, "", "F") == pytest.approx(10 * L_PER_GAL)
    assert litres(10, None, "C") == pytest.approx(10)
    assert litres(10, "gallons", "C") == pytest.approx(10 * L_PER_GAL)


def test_water_already_hot_needs_no_heating():
    assert heat_up_minutes(15, "gal", 170, 170, "F", WATTS) == 0.0
    assert heat_up_minutes(15, "gal", 175, 170, "F", WATTS) == 0.0


@pytest.mark.parametrize("bad", [None, "", "abc", 0, -5, float("nan"), float("inf")])
def test_volume_and_watts_must_be_real_positive_numbers(bad):
    with pytest.raises(ValueError):
        heat_up_minutes(bad, "gal", 60, 170, "F", WATTS)
    with pytest.raises(ValueError):
        heat_up_minutes(15, "gal", 60, 170, "F", bad)


def test_efficiency_takes_a_fraction_or_a_percentage():
    assert efficiency_fraction(None) == HEAT_EFFICIENCY
    assert efficiency_fraction("") == HEAT_EFFICIENCY
    assert efficiency_fraction(0.8) == pytest.approx(0.8)
    assert efficiency_fraction(80) == pytest.approx(0.8)
    assert efficiency_fraction("75") == pytest.approx(0.75)


@pytest.mark.parametrize("bad", ["abc", 150, 0, 0.05, -10, float("nan")])
def test_impossible_efficiency_falls_back_to_the_measured_one(bad):
    assert efficiency_fraction(bad) == HEAT_EFFICIENCY


def test_lower_efficiency_means_an_earlier_start():
    calibrated = heat_up_minutes(15, "gal", 60, 170, "F", WATTS)
    slower = heat_up_minutes(15, "gal", 60, 170, "F", WATTS, efficiency=75)
    assert slower == pytest.approx(calibrated * 90 / 75)


def test_margin():
    assert margin_minutes(None) == DEFAULT_MARGIN_MIN
    assert margin_minutes("") == DEFAULT_MARGIN_MIN
    assert margin_minutes("x") == DEFAULT_MARGIN_MIN
    assert margin_minutes("5") == 5
    assert margin_minutes(-3) == 0


def test_the_plan_works_back_from_the_ready_time():
    plan = plan_ready_by(READY, 170, WATTS, "F", 15, 60, volume_unit="gal")
    assert plan.margin_minutes == DEFAULT_MARGIN_MIN
    assert plan.start == READY - timedelta(minutes=plan.heat_minutes + 10)
    assert plan.lead_minutes == pytest.approx(plan.heat_minutes + 10)
    assert 49 < plan.heat_minutes < 51
    assert plan.strike_minutes == 0
    assert not plan.assumed


def test_strike_water_adds_its_own_heat_up():
    alone = plan_ready_by(READY, 163, WATTS, "F", 15, 60, volume_unit="gal")
    both = plan_ready_by(READY, 163, WATTS, "F", 15, 60,
                         strike_volume=8, strike_temp=62, volume_unit="gal")
    extra = heat_up_minutes(8, "gal", 62, 163, "F", WATTS)
    assert both.strike_minutes == pytest.approx(extra)
    assert both.heat_minutes == pytest.approx(alone.heat_minutes + extra)
    assert both.start == alone.start - timedelta(minutes=extra)


def test_strike_water_without_its_own_reading_uses_the_heated_kettles():
    plan = plan_ready_by(READY, 163, WATTS, "F", 15, 58,
                         strike_volume=8, strike_temp=None, volume_unit="gal")
    assert plan.strike_minutes == pytest.approx(
        heat_up_minutes(8, "gal", 58, 163, "F", WATTS))
    assert not plan.assumed


@pytest.mark.parametrize("none", [None, "", 0, "0"])
def test_no_strike_water(none):
    plan = plan_ready_by(READY, 163, WATTS, "F", 15, 60,
                         strike_volume=none, volume_unit="gal")
    assert plan.strike_minutes == 0


def test_no_reading_plans_from_cold_and_says_so():
    cold = plan_ready_by(READY, 170, WATTS, "F", 15, None, volume_unit="gal")
    warm = plan_ready_by(READY, 170, WATTS, "F", 15, 60, volume_unit="gal")
    assert cold.assumed
    assert cold.water_temp == pytest.approx(to_unit(ASSUMED_COLD_C, "F"))
    assert cold.start < warm.start  # earlier, never later


@pytest.mark.parametrize("bad", [None, "", "hot", float("nan")])
def test_ready_temp_is_required(bad):
    with pytest.raises(ValueError):
        plan_ready_by(READY, bad, WATTS, "F", 15, 60)


@pytest.mark.parametrize("gal,tablets,text", [
    (2, 0.25, "¼"),
    (8, 0.5, "½"),
    (10, 0.5, "½"),
    (15, 0.75, "¾"),
    (20, 1.0, "1"),
    (21, 1.25, "1¼"),
    (40, 2.0, "2"),
])
def test_campden_one_tablet_per_twenty_gallons_rounded_up(gal, tablets, text):
    got = campden_tablets(gal * L_PER_GAL)
    assert got == tablets
    assert format_tablets(got) == text


def test_campden_in_litres():
    assert campden_tablets(75) == 1.0
    assert campden_tablets(30) == 0.5


def test_labels():
    assert format_temp(152.04, "F") == "152.0 °F"
    assert format_temp(66.7, "C") == "66.7 °C"
    assert volume_label(8, "gal", "F") == "8 gal"
    assert volume_label(30, "", "C") == "30 L"
    assert volume_given(8) and not volume_given("") and not volume_given(0)
