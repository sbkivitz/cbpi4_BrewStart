import asyncio
import inspect
import logging
import math
import time
from datetime import datetime, timedelta

from cbpi.api import *
from cbpi.api.dataclasses import NotificationAction, NotificationType
from cbpi.api.step import CBPiStep, StepResult, StepState

try:
    from cbpi.api import clock as brewing_clock
except ImportError:  # upstream CraftBeerPi: the wall clock is the only clock
    brewing_clock = None

try:
    from .schedule import (L_PER_GAL, LEGACY_DATE, MODE_DATE, MODE_TODAY, MODES,
                           campden_tablets, efficiency_fraction,
                           efficiency_setting, format_countdown, format_percent,
                           format_start, format_tablets, format_temp,
                           is_fahrenheit, litres, plan_ready_by, resolve_start,
                           vessel_j_k, volume_label)
    from .strings import DEFAULT_LANGUAGE, normalise_language, t
except ImportError:
    from cbpi4_BrewStart.schedule import (L_PER_GAL, LEGACY_DATE, MODE_DATE,
                                          MODE_TODAY, MODES, campden_tablets,
                                          efficiency_fraction,
                                          efficiency_setting,
                                          format_countdown, format_percent,
                                          format_start, format_tablets,
                                          format_temp, is_fahrenheit, litres,
                                          plan_ready_by, resolve_start,
                                          vessel_j_k, volume_label)
    from cbpi4_BrewStart.strings import DEFAULT_LANGUAGE, normalise_language, t

logger = logging.getLogger(__name__)

TIMING_START = "Start at"
TIMING_READY = "Ready by"

#: The server setting that picks the language of the step's form and messages.
LANGUAGE_SETTING = "BREWSTART_LANGUAGE"

#: The server setting with the share of the element's power that heats the
#: water, in %: blank is the built-in schedule.HEAT_EFFICIENCY. USE on
#: CraftBeerPi's Figures page sets it from past brews, and BACK TO blanks it.
EFFICIENCY_SETTING = "BREWSTART_EFFICIENCY"


def configured_language(cbpi):
    """The language chosen in the server's settings; English when unset."""
    try:
        value = cbpi.config.get(LANGUAGE_SETTING, None)
    except Exception:  # noqa: BLE001 - a server without settings: the default
        value = None
    return normalise_language(value)


try:
    from cbpi.api.timefmt import twelve_hour as _core_twelve_hour
except ImportError:  # a core before CLOCK_FORMAT (4.7.7.post60): no setting, so 24-hour
    _core_twelve_hour = None

try:
    # The least water the HLT keeps over its element and coil, and the HLT it is kept in, as CraftBeerPi's strike
    # water box counts them (4.7.7.post77): its own least water on the Hardware page, else a share of its capacity,
    # else a cautious figure.
    from cbpi.extension.hltpump import find_hlt as _core_find_hlt
    from cbpi.strike.vessels import LEAST_SHARE as _CORE_LEAST_SHARE
    from cbpi.strike.vessels import least_water as _core_least_water
except ImportError:  # an older core: the HLT keeps back only the strike water
    _core_find_hlt = _core_least_water = _CORE_LEAST_SHARE = None


def configured_twelve_hour(cbpi):
    """Whether CraftBeerPi's CLOCK_FORMAT shows times of day in 12-hour form
    ("7:00 AM"). Missing, blank or anything but "12-hour" is 24-hour, as on a
    core without the setting."""
    if _core_twelve_hour is not None:
        return _core_twelve_hour(cbpi)
    try:
        return cbpi.config.get("CLOCK_FORMAT", None) == "12-hour"
    except Exception:  # noqa: BLE001 - a server without settings: 24-hour
        return False


# The browser's own date and time pickers, on a server that has them (this
# fork from 4.7.7.post15); a text field with the format spelled out on one that
# does not.
DateProperty = getattr(Property, "Date", Property.Text)
TimeProperty = getattr(Property, "Time", Property.Text)
NATIVE_PICKERS = DateProperty is not Property.Text

#: Which settings each field applies to (cbpi.api.property.PropertyType). A
#: server or interface from before show_if shows every field, as before.
ONLY_FOR_DATE = {"Mode": [MODE_DATE, LEGACY_DATE]}


def _field(prop, name, show_if=None):
    """A property with the name the form shows, and when it shows.

    Set after construction: a server from before display_label and show_if
    takes no such arguments, and ignores the attributes - its form shows the
    saved settings' names, and every field, as it always did.
    """
    prop.display_label = name
    if show_if is not None:
        prop.show_if = show_if
    return prop


def degrees(temp_unit):
    return "°F" if str(temp_unit or "C").strip().upper().startswith("F") else "°C"


def step_properties(language=DEFAULT_LANGUAGE, temp_unit="C"):
    """The step's form, in one language and in words.

    Every description is in the server's language only, never English and
    German side by side. BrewStart does one thing: it heats the strike water
    in Heat_Kettle to Ready_Temp and holds it there until the brewer's Next -
    from the time given (Start at), or early enough to be hot by it (Ready
    by). So every heating field is always asked for, and there is no "Go on
    by itself": nothing moves on but Next. The date is asked for only for a
    Date. Strike_Kettle, Strike_Volume, Efficiency_Pct, Margin_Minutes,
    Language and AutoNext, saved by older forms, are not asked for and not
    read; see BrewStart._plan and configured_language.

    After Apple's design principles, the fields are named in words ("Time",
    not "StartTime" - the label stays the saved setting's name), numbers carry
    their units, and a new step starts from the defaults rather than blanks
    whose meaning is only in the help text.
    """
    def says(key):
        return t("prop_" + key, language)

    def name(key):
        return t("name_" + key, language)

    def with_format(text, fmt):
        if NATIVE_PICKERS:
            return text
        return (text + " " + says(fmt)).strip()

    return [
        # Ready by or Start at. A Timing left blank is Ready by.
        _field(Property.Select(label="Timing", options=[TIMING_START, TIMING_READY],
                               default_value=TIMING_READY, description=says("timing")),
               name("timing")),
        _field(Property.Select(label="Mode", options=list(MODES), default_value=MODE_TODAY,
                               description=says("mode")),
               name("mode")),
        _field(DateProperty(label="StartDate", configurable=True,
                            description=with_format(says("start_date"), "format_date")),
               name("start_date"), ONLY_FOR_DATE),
        _field(TimeProperty(label="StartTime", configurable=True,
                            description=with_format(says("start_time"), "format_time")),
               name("start_time")),
        _field(Property.Kettle(label="Heat_Kettle", description=says("heat_kettle")),
               name("heat_kettle")),
        _field(Property.Number(label="Ready_Temp", configurable=True, unit=degrees(temp_unit),
                               description=says("ready_temp")),
               name("ready_temp")),
        _field(Property.Number(label="Volume", configurable=True, description=says("volume")),
               name("volume")),
        _field(Property.Select(label="Volume_Unit", options=["gal", "L"],
                               description=says("volume_unit")),
               name("volume_unit")),
        _field(Property.Number(label="Grain_Temp", configurable=True, unit=degrees(temp_unit),
                               description=says("grain_temp")),
               name("grain_temp")),
        _field(Property.Number(label="Heater_Watts", configurable=True, unit="W",
                               description=says("heater_watts")),
               name("heater_watts")),
        _field(Property.Select(label="Campden_Reminder", options=["No", "Yes"],
                               default_value="No", description=says("campden")),
               name("campden")),
        _field(Property.Number(label="NotifyBefore", configurable=True, unit="min",
                               description=says("notify_before")),
               name("notify_before")),
    ]


def change_time_parameters(language=DEFAULT_LANGUAGE):
    """What the action change_time takes: the day and the time, named as the form names them. Not Timing, which
    can't change while the step waits, and not the water, which the strike water box changes."""
    return [
        _field(Property.Select(label="Mode", options=list(MODES), description=t("prop_change_mode", language)),
               t("name_mode", language)),
        _field(DateProperty(label="StartDate", configurable=True, description=t("prop_change_date", language)),
               t("name_start_date", language), ONLY_FOR_DATE),
        _field(TimeProperty(label="StartTime", configurable=True, description=t("prop_start_time", language)),
               t("name_start_time", language)),
    ]


#: What the action change_settings takes: every setting of the step's form, by its saved name.
SETTINGS = ("Timing", "Mode", "StartDate", "StartTime", "Heat_Kettle", "Ready_Temp", "Volume", "Volume_Unit",
            "Grain_Temp", "Heater_Watts", "Campden_Reminder", "NotifyBefore")
#: Settings older forms and screens still send, taken and ignored: nothing moves BrewStart on but Next.
IGNORED_SETTINGS = ("AutoNext",)
#: The settings that say when: a change to one is worked out from the moment it is made, as change_time does.
WHEN_SETTINGS = ("Mode", "StartDate", "StartTime")
#: The settings with choices, and the choices.
SETTING_CHOICES = {"Timing": (TIMING_START, TIMING_READY), "Mode": MODES, "Volume_Unit": ("gal", "L"),
                   "Campden_Reminder": ("No", "Yes")}
#: What a blank choice is read as (_read_props, campden_dose): Volume_Unit's depends on TEMP_UNIT.
BLANK_AS = {"Timing": TIMING_READY, "Mode": MODE_TODAY, "Campden_Reminder": "No"}
SETTING_NUMBERS = ("Ready_Temp", "Volume", "Grain_Temp", "Heater_Watts", "NotifyBefore")
#: The form's name of each setting (strings.py name_*), for the words of a refusal.
SETTING_NAMES = {"Timing": "timing", "Mode": "mode", "StartDate": "start_date", "StartTime": "start_time",
                 "Heat_Kettle": "heat_kettle", "Ready_Temp": "ready_temp", "Volume": "volume",
                 "Volume_Unit": "volume_unit", "Grain_Temp": "grain_temp", "Heater_Watts": "heater_watts",
                 "Campden_Reminder": "campden", "NotifyBefore": "notify_before"}
#: Where water boils, in °F and °C: a Ready_Temp there or above is never reached, and the element would run on.
BOILING = {"F": 212.0, "C": 100.0}


def change_settings_parameters(language=DEFAULT_LANGUAGE, temp_unit="C"):
    """What the action change_settings takes: the step form's settings, named, described and shown as the form
    shows them, but with no defaults - a setting not sent stays as it is, so no default may stand in for one."""
    params = step_properties(language, temp_unit)
    for prop in params:
        if hasattr(prop, "default_value"):
            prop.default_value = None
    return params


class _Refusal(Exception):
    """change_settings refused: the brewer's words, complete."""

    def __init__(self, words):
        super().__init__(words)
        self.words = words


def _simulation_clock():
    """The simulation's clock when one runs the server, else None.

    This fork's server has one clock for everything that measures time
    (cbpi.api.clock). On a rig its source is real time, and the step asks the
    wall clock itself. A simulation (cbpi4-simvessel) runs
    that clock SIM_TIME_SCALE times as fast, and the water heats and every
    other step's timer counts by it - so the step must read it too.
    """
    if brewing_clock is None:
        return None
    try:
        source = brewing_clock.get_source()
        if isinstance(source, brewing_clock.RealTimeSource):
            return None
    except AttributeError:  # a core whose clock predates sources: real time
        return None
    return source


def brewing_now():
    """The time of day on the clock the brew runs by."""
    source = _simulation_clock()
    return datetime.now() if source is None else datetime.fromtimestamp(source.now())


def brewing_monotonic():
    """Seconds for measuring intervals, on the same clock as brewing_now()."""
    source = _simulation_clock()
    return time.monotonic() if source is None else source.monotonic()


@parameters(step_properties())
class BrewStart(CBPiStep):
    """Heat the strike water at a set time and hold it until the brewer's Next.

    BrewStart heats Heat_Kettle to Ready_Temp and holds it there; once the
    water reads Ready_Temp it asks for the strike water to be moved and the
    hoses and pumps connected, and only the brewer's Next moves on - with
    Heat_Kettle's AUTO switched off first: on a HERMS the mash tun's logic
    drives the same element. It never moves on by itself, whatever the time,
    an AutoNext saved by an older form, a restart or a change of settings,
    and it never switches a pump or any actor: only Heat_Kettle's target and
    AUTO. Two ways to say when the heating begins:

    - Start at: at the time given.
    - Ready by: as early as it needs to for the water to be at Ready_Temp by
      the time given, worked out from the water's measured temperature, the
      volumes and the element's power (schedule.py). The plan is re-made once
      a minute, so water that cools overnight moves the start earlier rather
      than leaving the water short.

    A Timing left blank is Ready by. The one exception, before a MashInStep on
    Heat_Kettle right after it - a recipe made for an older BrewStart, whose
    strike water step heats the water, holds it and asks for it to be moved -
    it hands over to that step at the heating start, so the water isn't
    heated or asked about twice.

    Why the step's own loop looks at the wall clock, with no countdown:

    A Timer counts a pure duration and has no idea what time it is. Handed
    `target - datetime.now()` seconds once, it moves the start by as much as
    anything moves the system clock, silently - and a Raspberry Pi has no RTC:
    after a power cut it boots with a wrong clock, CraftBeerPi autostarts, the
    step resolves against that wrong `now()`, and NTP corrects the clock a
    minute later. A wait across a daylight saving change is out by an hour
    the same way.

    Nor can the Timer be rebuilt whenever it disagrees with the wall clock.
    The rebuild would run inside the old Timer's own update, and a Timer
    cannot be stopped from inside itself: stop() cancels the task and then
    awaits it, the await swallows the cancellation, and the old countdown runs
    on - re-detecting the same drift every second. With the real Timer, one
    100 s clock correction gives 24 "System clock changed" notifications, 8
    "start time reached", and a countdown still running after the step has
    finished.

    So nothing is rebuilt. run() compares the target with the wall clock every
    TICK_S and hands over when it is reached; a clock that jumps is reported
    once, and the target - an absolute local datetime - is simply still right.

    "The wall clock" is the clock the brew runs by: the PC's on a rig, the
    simulation's in a simulation (brewing_now). A step reading the PC's clock
    in a simulation at SIM_TIME_SCALE 60 would wait real minutes for a start a
    few simulated minutes away, while the water heats by the simulation's
    clock - and is hot long before the time it was asked for.

    When: Today, Tomorrow or a Date (schedule.resolve_start).
    The moment worked out is kept with the step (PLANNED_PROP), because Today
    and Tomorrow mean nothing on their own: a step that worked Tomorrow out
    again at 05:30 on brew day, after a restart, would wait until the day
    after. And a time that had already passed when the brew was started is
    never acted on by itself - an old Date, or Today chosen in the evening for
    the next morning, would otherwise start heating at once with nobody
    expecting it.

    After a power cut, the step allows the server to carry it on by itself,
    and the step it hands over to, until RESUME_GRACE after the time it was
    asked for (StepController.allow_resume, cbpi4 4.7.7.post21), so water
    promised for a time is still hot by then after an overnight cut.
    Every other step still waits for the brewer to answer "Resume brew?". And
    it never starts heating by itself later than that, however it got there -
    a Pi that booted with a clock hours behind and was corrected once the
    wait had resumed included: past RESUME_GRACE it waits for Next Step.
    """

    #: How far the wall clock may move against the monotonic clock between two
    #: looks before it counts as having been changed, and is reported. Loose
    #: enough to ignore scheduling jitter, tight enough that an NTP step
    #: correction or a daylight saving shift is caught on the next look.
    CLOCK_DRIFT_TOLERANCE_S = 60

    #: How often the step looks at the clock.
    TICK_S = 1.0

    #: How often a "Ready by" plan is re-made from the water's temperature.
    #: Overnight a kettle cools a few degrees, which moves the start by a few
    #: minutes; once a minute keeps up with that without chasing sensor noise.
    REPLAN_INTERVAL_S = 60

    #: Where the step keeps the moment it worked out: "<settings>|<ISO time>".
    #: Underscored like the core's _elapsed_seconds, so the form does not show
    #: it; cleared by reset(), so a new brew works it out afresh.
    PLANNED_PROP = "_brewstart_planned"

    #: A kept moment further in the past than this is from some earlier day,
    #: not from a brew interrupted on its way to it, and is worked out afresh.
    RECALL_WITHIN = timedelta(hours=24)

    #: How long after the time it was asked for - the ready time, or the start
    #: time - the step, and the step it hands over to, may carry on by
    #: themselves after a restart, and the step may still start heating by
    #: itself. Long enough to cover heating started late by a cut and a brewer
    #: who comes a little after the ready time; short enough that a server back
    #: in the afternoon does not heat water nobody is expecting any more.
    RESUME_GRACE = timedelta(hours=2)

    #: Kept with the step, underscored as PLANNED_PROP: Ready by has begun heating
    #: Heat_Kettle - its target set, its AUTO on - and, once the water read
    #: Ready_Temp, asked for it to be moved. So Stop and Start, or a restart, come
    #: back to the heating or to the question, never to a second wait for the
    #: time, and CraftBeerPi's strike water helper can tell the water is heating.
    #: Cleared by reset().
    HEATING_PROP = "_brewstart_heating"
    ASKED_PROP = "_brewstart_asked"

    #: The kind of the "move the water" question, for StepController.keep_step_prompt:
    #: one at a time, taken down when the step stops or ends.
    QUESTION_KIND = "water"

    #: Until on_start reads the server's setting.
    language = DEFAULT_LANGUAGE

    async def NextStep(self, **kwargs):
        await self._hand_over()

    async def WaterMoved(self, **kwargs):
        """Next Step on "Hot at ... Move it to the Mash Tun, connect the hoses, then NEXT.":
        the brewer has moved the water. Only this, or the screens' NEXT, moves a
        Ready by BrewStart on - on_stop switches Heat_Kettle's AUTO off before
        the next step starts - and the next step is the brewer's to carry on
        after a restart, so no permission is passed on. A button left from an
        earlier run does nothing."""
        if not self._is_current():
            logger.info("BrewStart: ignoring an old answer for %s", self.id)
            return
        await self.next()

    async def HeatNow(self, **kwargs):
        """Heat now, on a Ready by whose time had passed: the water heats as if
        its time had come. A button left from an earlier run does nothing."""
        if not self._is_current() or getattr(self, "heating", False):
            return
        self.late_said = True  # it has passed, and the brewer knows
        await self._start_heating(t("notify_heat_now", self.language,
                                    kettle=self._kettle_name(),
                                    target=format_temp(self._ready_temp() or 0, self._unit())))

    def _is_current(self):
        """This instance is the running step's, as the core's MashInStep checks."""
        step = getattr(getattr(self.cbpi, "step", None), "find_by_id", lambda _id: None)(self.id)
        return (step is not None and getattr(step, "instance", self) is self
                and getattr(step, "status", None) == StepState.ACTIVE and self.running is True)

    async def _hand_over(self):
        """Go on to the next step, passing on permission to carry on after a
        restart: the heating it starts is part of the same promise."""
        following = self._following_step()
        if following is not None:
            await self._allow_resume(following.id)
        await self.next()

    def _following_step(self):
        """The step the server starts when this one hands over, chosen as
        StepController.start() chooses it: a stopped step first, then the
        first not yet run."""
        profile = getattr(getattr(self.cbpi, "step", None), "profile", None) or []
        for wanted in (StepState.STOP, StepState.INITIAL):
            for item in profile:
                if getattr(item, "id", None) != self.id and \
                        getattr(item, "status", None) == wanted:
                    return item
        return None

    async def _allow_resume(self, step_id):
        """Let the server carry `step_id` on by itself after a restart, until
        RESUME_GRACE after the time asked for. False on a server that cannot:
        one older than cbpi4 4.7.7.post21 asks "Resume brew?" instead."""
        asked = getattr(self, "asked_for", None)
        allow = getattr(getattr(self.cbpi, "step", None), "allow_resume", None)
        if asked is None or allow is None:
            return False
        until = asked + self.RESUME_GRACE
        if until <= brewing_now():
            return False
        try:
            return bool(await allow(step_id, until.timestamp(), self.name))
        except Exception as exc:  # noqa: BLE001 - a courtesy; the step runs on
            logger.warning("BrewStart: could not arrange to carry on after a "
                           "restart - %s", exc)
            return False

    async def _allow_carrying_on(self):
        """This step may carry on by itself after a restart until two hours
        after the ready time now asked for: heating, or holding the water for
        the question."""
        return await self._allow_resume(self.id)

    def _past_carrying_on(self, now):
        """Too long after the time asked for to start heating by itself."""
        asked = getattr(self, "asked_for", None)
        return asked is not None and now > asked + self.RESUME_GRACE

    async def _too_late(self, now):
        """The time has come, but long after the time asked for: wait for Next.

        Reached after a power cut that outlasted the promise, or a clock
        corrected after the wait had resumed - a Raspberry Pi has no battery
        clock and boots with the time it last saved, hours behind after a long
        cut. Either way nobody is expecting heat now, so it is offered, not
        started.
        """
        self.target_start = None  # _tick: only Next moves on
        self.summary = t("summary_too_late", self.language,
                         asked=self._at(self.asked_for))
        await self.push_update()
        if self._heats_itself():
            # Next would move on with the water cold: offered as Heat now instead.
            self._offer_heat_now(t("notify_too_late_heat_now", self.language, now=self._at(now),
                                   asked=self._at(self.asked_for),
                                   hours=round(self.RESUME_GRACE.total_seconds() / 3600)))
            return
        self._offer_next(t("notify_too_late", self.language, now=self._at(now),
                           asked=self._at(self.asked_for),
                           hours=round(self.RESUME_GRACE.total_seconds() / 3600)),
                         NotificationType.WARNING, wrap=False)

    # ----------------------------------------------------------- messages

    def _at(self, moment):
        """A moment with its day, in BrewStart's language and CraftBeerPi's
        clock format: "Sat Nov 21, 7:00 AM", or "Sat Nov 21, 07:00"."""
        return format_start(moment, self.language, configured_twelve_hour(self.cbpi))

    def _unit(self):
        return self.get_config_value("TEMP_UNIT", "C")

    def _scheduled_message(self):
        if self.plan is None:
            return t("notify_start_at_scheduled", self.language, start=self._at(self.target_start),
                     kettle=self._kettle_name(), target=format_temp(self._ready_temp() or 0, self._unit()))
        unit = self._unit()
        key = ("notify_ready_scheduled_strike" if self.plan.strike_minutes > 0
               else "notify_ready_scheduled")
        return t(key, self.language,
                 ready=self._at(self.ready_at),
                 start=self._at(self.target_start),
                 water=format_temp(self.plan.water_temp, unit),
                 target=format_temp(self.plan.target_temp, unit),
                 heat=round(self.plan.heat_minutes),
                 strike=round(self.plan.strike_minutes),
                 margin=round(self.plan.margin_minutes),
                 efficiency=self._efficiency_said())

    def _due_message(self, now):
        """What to say when the time has already come as the step starts."""
        if self.plan is None:
            return self._reached_message()
        eta = now + timedelta(minutes=self.plan.heat_minutes)
        if eta <= self.ready_at:
            # Due only because of the spare minutes: still on time.
            return t("notify_ready_reached", self.language,
                     ready=self._at(self.ready_at))
        return t("notify_ready_late", self.language,
                 water=format_temp(self.plan.water_temp, self._unit()),
                 heat=round(self.plan.heat_minutes),
                 eta=self._at(eta), ready=self._at(self.ready_at),
                 efficiency=self._efficiency_said())

    def _efficiency_said(self):
        """", at 85 % from past brews" after how long the water takes, when
        BREWSTART_EFFICIENCY is set; nothing for the built-in."""
        fraction = efficiency_setting(self._efficiency())
        if fraction is None:
            return ""
        return t("efficiency_from_past_brews", self.language, pct=format_percent(fraction))

    def _reached_message(self):
        if self.plan is not None:
            return t("notify_ready_reached", self.language,
                     ready=self._at(self.ready_at))
        return t("notify_start_at_reached", self.language, kettle=self._kettle_name(),
                 target=format_temp(self._ready_temp() or 0, self._unit()))

    def _offer_next(self, message, kind=NotificationType.INFO, wrap=True):
        self.cbpi.notify(
            self.name,
            t("notify_next_step", self.language, message=message) if wrap else message,
            kind,
            action=[NotificationAction(
                t("action_next_step", self.language), self.NextStep)],
        )

    def _offer_heat_now(self, message):
        """A BrewStart that heats its kettle itself, whose time has passed: nothing
        heats by itself, and the button is Heat now, not Next Step - Next would
        move on to the mash tun with the water still cold."""
        self.waits_for_heat_now = True
        self.cbpi.notify(
            self.name, message, NotificationType.WARNING,
            action=[NotificationAction(t("action_heat_now", self.language), self.HeatNow)],
        )

    async def _arrive(self, message):
        """The time has come: BrewStart heats Heat_Kettle itself, holds it and asks
        - Start at and Ready by alike - and only the brewer's Next moves on. Before
        a MashInStep on Heat_Kettle right after it, it hands over to that step,
        which heats the water, holds it and asks (_hands_over)."""
        if self._heats_itself():
            await self._start_heating(message)
            return
        self._record_heating()
        self.arrived = True
        self.summary = t("summary_ready_reached", self.language)
        await self.push_update()
        self.cbpi.notify(self.name, message, NotificationType.SUCCESS)

    async def _already_passed(self, requested, mode):
        """The time asked for was over before the brew was started.

        A Date left from an earlier brew, or Today chosen in the evening for
        the next morning. Heating at once would heat the water with nobody
        expecting it. So nothing happens by itself: the brewer is told, and
        Heat now heats the water.
        """
        self.target_start = None  # _tick: only Next moves on
        self.summary = t("summary_passed", self.language, start=self._at(requested))
        await self.push_update()
        if self._heats_itself():
            key = "notify_passed_today_heat_now" if mode == MODE_TODAY else "notify_passed_heat_now"
            self._offer_heat_now(t(key, self.language, start=self._at(requested)))
            return
        key = "notify_passed_today" if mode == MODE_TODAY else "notify_passed"
        self._offer_next(t(key, self.language, start=self._at(requested)),
                         NotificationType.WARNING, wrap=False)

    def _tell_no_reading(self):
        heat = self._kettle("Heat_Kettle")
        self.cbpi.notify(
            self.name,
            t("notify_ready_no_reading", self.language,
              kettle=getattr(heat, "name", "?"),
              assumed=format_temp(self.plan.water_temp, self._unit())),
            NotificationType.WARNING,
        )

    # ----------------------------------------------------------- heating the water

    def _next_on_a_kettle(self):
        """The first step after this one in the profile that names a kettle, or None."""
        profile = list(getattr(getattr(self.cbpi, "step", None), "profile", None) or [])
        at = next((i for i, item in enumerate(profile) if getattr(item, "id", None) == self.id), None)
        for item in (profile[at + 1:] if at is not None else []):
            props = getattr(item, "props", None)
            if props is not None and props.get("Kettle", None):
                return item
        return None

    def _next_heats_this_kettle(self):
        """The step right after this one is a MashInStep on Heat_Kettle.

        A recipe made for BrewStart before 1.3.0.post1, as the rig page's step 9b
        has it: BrewStart, then a strike water step on the HLT that heats the
        water, holds it and asks for it to be moved itself, and moves on only on
        the brewer's Next. There BrewStart hands over as the heating starts, so
        the water isn't heated or asked about twice. Only the step right after
        it counts: a step between them might switch a pump or move on by itself.
        """
        kettle = self.props.get("Heat_Kettle", None)
        profile = list(getattr(getattr(self.cbpi, "step", None), "profile", None) or [])
        at = next((i for i, item in enumerate(profile) if getattr(item, "id", None) == self.id), None)
        following = profile[at + 1] if at is not None and at + 1 < len(profile) else None
        props = getattr(following, "props", None)
        return bool(kettle and props is not None and getattr(following, "type", None) == "MashInStep"
                    and props.get("Kettle", None) == kettle)

    def _heats_itself(self):
        """BrewStart heats Heat_Kettle itself, holds the water and asks for it to
        be moved - Start at and Ready by alike - unless the step right after it
        is a MashInStep on that kettle."""
        return not self._next_heats_this_kettle()

    def _hands_over(self):
        """Hand over to the next step by itself: only once the time has come,
        before a MashInStep on Heat_Kettle right after it, which heats the
        water, holds it and asks. Never otherwise: BrewStart holds the water
        until the brewer's Next."""
        return (bool(getattr(self, "arrived", False)) and not getattr(self, "heating", False)
                and self._next_heats_this_kettle())

    def _check_heating(self):
        """Raise ValueError unless BrewStart can heat: Heat_Kettle with a sensor,
        and Ready_Temp. What Ready by plans from too is _plan's to check."""
        heat = self._kettle("Heat_Kettle")
        if heat is None or not getattr(heat, "sensor", None):
            raise ValueError(t("err_heat_kettle", self.language))
        if self._ready_temp() is None:
            raise ValueError(t("err_heat_temp", self.language))

    def _ready_temp(self):
        try:
            return float(self.props.get("Ready_Temp", None))
        except (TypeError, ValueError):
            return None

    def _kettle_name(self):
        return getattr(self._kettle("Heat_Kettle"), "name", None) or "?"

    def _next_kettle_name(self):
        """Where the water goes: the kettle of the next step on one."""
        following = self._next_on_a_kettle()
        try:
            kettle = self.get_kettle(following.props.get("Kettle", None)) if following is not None else None
        except Exception:  # noqa: BLE001 - a deleted kettle is simply none
            kettle = None
        return getattr(kettle, "name", None) or t("default_mash_tun", self.language)

    async def _heat(self, target, tell=True):
        """Heat_Kettle's target to `target`, and its AUTO on if it isn't: its
        logic heats the water there and holds it. False when it can't, said to
        every screen unless `tell` is False (change_settings says it its own way)."""
        kettle = self._kettle("Heat_Kettle")
        if kettle is None:
            return False
        self.kettle = kettle
        try:
            await self.cbpi.kettle.set_target_temp(kettle.id, target)
            instance = getattr(kettle, "instance", None)
            if instance is None or not getattr(instance, "state", False):
                await self.cbpi.kettle.start(kettle.id)
        except Exception as exc:  # noqa: BLE001 - said below; the step waits on
            logger.warning("BrewStart: could not heat %s - %s", getattr(kettle, "name", "?"), exc)
        instance = getattr(self._kettle("Heat_Kettle"), "instance", None)
        if instance is None or not getattr(instance, "state", False):
            if tell:
                self.cbpi.notify(self.name, t("notify_cannot_heat", self.language, kettle=kettle.name),
                                 NotificationType.ERROR)
            return False
        return True

    async def _heat_off(self):
        """Heat_Kettle's AUTO off, so the next step's logic is the only one
        driving its element: on the rig the mash tun's PID_HERMS drives the HLT's."""
        await self._kettle_off(self._kettle("Heat_Kettle"))

    async def _kettle_off(self, kettle):
        """`kettle`'s AUTO off, if it is on."""
        instance = getattr(kettle, "instance", None) if kettle is not None else None
        if instance is None or not getattr(instance, "state", False):
            return
        try:
            await self.cbpi.kettle.stop(kettle.id)
            logger.info("BrewStart: %s's AUTO off", kettle.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("BrewStart: could not switch %s's AUTO off - %s", kettle.name, exc)

    async def _start_heating(self, message=None, resumed=False):
        """The time to heat: Heat_Kettle's target is Ready_Temp and its
        AUTO on, and the step watches the water until it reads Ready_Temp.
        Remembered with the step (HEATING_PROP). `resumed`: back to the heating
        after Stop and Start or a restart, which says nothing new - and, once
        it had asked for the water to be moved (ASKED_PROP), back to holding
        it with no second question: the brewer's Next moves on."""
        self.arrived = True  # the heating start has come: change_time changes the ready time only
        self.heating = True
        self.asked = bool(resumed and self.props.get(self.ASKED_PROP, False))
        target = self._ready_temp()
        if not self.props.get(self.HEATING_PROP, False):
            self.props[self.HEATING_PROP] = True
            await self._save()
            self._record_heating()
        reset = getattr(self, "reset_heat_watch", None)
        if callable(reset):
            reset()
        if target is not None:
            await self._heat(target)
        if message and not resumed:
            self.cbpi.notify(self.name, message, NotificationType.SUCCESS)
        self._show_heating()
        await self.push_update()

    async def _save(self):
        try:
            await self.cbpi.step.save()
        except Exception as exc:  # noqa: BLE001 - kept in memory either way
            logger.warning("BrewStart: could not save the step - %s", exc)

    # ----------------------------------------------------------- the record, for Figures

    def _record(self, series, value):
        """One row in CraftBeerPi's logs (sensor_<series>.log), stamped now on
        its clock, for the Figures page. Nothing on a core without
        log_control_data, and never a fault: the brew goes on without it."""
        write = getattr(getattr(self.cbpi, "log", None), "log_control_data", None)
        if value is None or not callable(write):
            return
        try:
            write(series, value)
        except Exception as exc:  # noqa: BLE001
            logger.debug("BrewStart: could not log %s - %s", series, exc)
            return
        self._flush_soon(series)

    def _flush_soon(self, series):
        """The row on the disk two real seconds later, rather than in the log's
        batch until something reads it - or lost to a power cut: CraftBeerPi's
        CSV log keeps a series' rows until its next row or a minute later, and
        the record writes one row a brew. Real seconds, as the log's batching
        is real time, whatever speed the brewing clock runs at."""
        flush = getattr(getattr(self.cbpi, "log", None), "_flush_datalogger", None)
        if not callable(flush):
            return

        async def later():
            await asyncio.sleep(2)
            try:
                await flush(series)
            except Exception as exc:  # noqa: BLE001
                logger.debug("BrewStart: could not write %s to the disk - %s", series, exc)

        self._spawn(later())

    def _spawn(self, work):
        """`work`, a coroutine, in a task of its own, held until it ends: the
        event loop keeps only a weak reference to a task. Closed unrun when no
        loop runs."""
        try:
            task = asyncio.get_running_loop().create_task(work)
        except RuntimeError:
            work.close()
            return None
        chores = self.__dict__.setdefault("_chores", set())
        chores.add(task)
        task.add_done_callback(chores.discard)
        return task

    def _record_heating(self):
        """What it planned, as the heating starts: the minutes for the heating,
        the ready time (seconds on CraftBeerPi's clock), the water in litres,
        the element's watts and the efficiency planned with. The plan is made
        again from the water as it is now, as Heat now can come long after the
        plan was."""
        plan = self.plan
        ready_at = getattr(self, "ready_at", None)
        if ready_at is not None:
            try:
                plan = self._plan(ready_at)
            except ValueError:
                pass
        if plan is None:
            return
        try:
            water_l = litres(self.props.get("Volume", None), self.props.get("Volume_Unit", None),
                             self._unit(), self.language)
        except ValueError:
            water_l = None
        try:
            watts = float(self.props.get("Heater_Watts", None))
        except (TypeError, ValueError):
            watts = None
        self._record("brewstart.planned_min", round(plan.heat_minutes, 2))
        self._record("brewstart.ready_by", None if ready_at is None else round(ready_at.timestamp(), 3))
        self._record("brewstart.litres", None if water_l is None else round(water_l, 3))
        self._record("brewstart.watts", watts)
        self._record("brewstart.efficiency", round(efficiency_fraction(self._efficiency()), 4))
        steel = self._steel_j_k(self._kettle("Heat_Kettle"))
        if steel > 0:
            self._record("brewstart.steel_j_k", round(steel, 1))

    def _show_heating(self):
        """The status line, heating or holding. Never "Start ..." nor "heating
        from": Brew Day reads a waiting step's heating start from those."""
        values = dict(target=format_temp(self._ready_temp() or 0, self._unit()),
                      ready=self._at(self.ready_at) if getattr(self, "ready_at", None) else "?")
        if getattr(self, "asked", False):
            self.summary = t("summary_water_ready", self.language, **values)
        elif getattr(self, "late_said", False) and getattr(self, "ready_at", None):
            self.summary = t("summary_heating_late", self.language, **values)
        elif getattr(self, "ready_at", None):
            self.summary = t("summary_heating", self.language, **values)
        else:
            self.summary = t("summary_heating_now", self.language, **values)

    async def _heat_tick(self, now):
        """One look while heating or holding: the stall watch, the question once
        the water reads Ready_Temp, and a word if the ready time comes first."""
        water = self._water_temp(self._kettle("Heat_Kettle"))
        target = self._ready_temp()
        if not self.asked and target is not None:
            watch = getattr(self, "note_heat_progress", None)
            if callable(watch) and water is not None:
                watch(water, target)
            if water is not None and self._reached(water, target):
                await self._water_ready(water)
                return
            ready_at = getattr(self, "ready_at", None)
            if not self.late_said and ready_at is not None and now >= ready_at:
                self.late_said = True
                unit = self._unit()
                self.cbpi.notify(self.name, t("notify_water_late", self.language, ready=self._at(ready_at),
                                              kettle=self._kettle_name(), target=format_temp(target, unit),
                                              water="?" if water is None else format_temp(water, unit)),
                                 NotificationType.WARNING)
        self._show_heating()
        await self.push_update()

    def _reached(self, water, target):
        reached = getattr(self, "target_reached", None)
        if callable(reached):
            return reached(water, target)
        return water >= target

    async def _water_ready(self, water=None):
        """The water reads Ready_Temp: held there, and the brewer asked to move it.
        The first time for this brew, the reading goes in the record: its row's
        time is when the water was ready."""
        self.asked = True
        if not self.props.get(self.ASKED_PROP, False):
            if self.ASKED_PROP not in self.props:  # the first time: forget_wait leaves it False
                self._record("brewstart.ready", water)
            self.props[self.ASKED_PROP] = True
            await self._save()
        self._show_heating()
        await self.push_update()
        self._ask()

    def _ask(self):
        """"Hot at ... Move it to the Mash Tun, connect the hoses, then NEXT.", with Next
        Step, as the strike water step on the HLT asks it: kept on every page
        until answered, and taken down when the step stops or ends. Its button is
        the only thing that moves BrewStart on, with the screens'
        NEXT. The phone alert plugin sends a question with a button on."""
        notify = self.cbpi.notify
        try:
            kept = {"keep": True} if "keep" in inspect.signature(notify).parameters else {}
        except (TypeError, ValueError):
            kept = {}
        target, to = self._ready_temp(), self._next_kettle_name()
        words = (t("notify_water_ready_hot", self.language, to=to) if target is None else
                 t("notify_water_ready", self.language, to=to, target=format_temp(target, self._unit())))
        question = notify(
            self.name,
            words,
            NotificationType.INFO,
            action=[NotificationAction(t("action_next_step", self.language), self.WaterMoved)],
            **kept,
        )
        keep = getattr(getattr(self.cbpi, "step", None), "keep_step_prompt", None)
        if question is not None and callable(keep):
            try:
                keep(self.id, self.QUESTION_KIND, question)
            except Exception as exc:  # noqa: BLE001 - a question never stops the brew
                logger.debug("BrewStart: could not keep the question - %s", exc)

    async def change_target(self, temp):
        """The strike water's new temperature while the step runs: USE and BACK TO
        in CraftBeerPi's strike water helper, which takes a Ready by BrewStart
        that heats its kettle itself as the strike water step.

        Ready_Temp takes it. Waiting, the heating start is worked out again from
        it within a minute, as from the water's temperature. Heating or holding,
        Heat_Kettle's target moves at once, and a question already asked is taken
        down from every page and asked again once the water reads the new
        temperature - at once if it's already there: the HLT can't cool it.
        True when a question was taken down, as MashInStep.change_target says.
        """
        temp = float(temp)
        self.props["Ready_Temp"] = temp
        if not getattr(self, "heating", False):
            return False
        await self._heat(temp)
        reset = getattr(self, "reset_heat_watch", None)
        if callable(reset):
            reset()
        asked = bool(getattr(self, "asked", False))
        if asked:
            self.forget_wait()
        self._show_heating()
        await self.push_update()
        return asked

    def forget_wait(self):
        """Forget that the question was asked, and take it down from every page:
        the water is to be heated to a new temperature first. For the strike
        water helper, on a step stopped after it asked, too: Start then heats and
        asks once the water reads the new temperature. True when it had asked."""
        asked = bool(self.props.get(self.ASKED_PROP, False)) or bool(getattr(self, "asked", False))
        self.asked = False
        if self.ASKED_PROP in self.props:
            self.props[self.ASKED_PROP] = False
        withdraw = getattr(getattr(self.cbpi, "step", None), "withdraw_step_prompt", None)
        if asked and callable(withdraw):
            try:
                withdraw(self.id, self.QUESTION_KIND)
            except Exception as exc:  # noqa: BLE001
                logger.debug("BrewStart: could not take the question down - %s", exc)
        return asked

    async def on_props_saved(self):
        """Feedback as soon as the brewer saves the step, not at brew time.

        A time that has already passed, or settings the step cannot start
        from, are said now - while the form is still in mind - rather than when
        the brew is started, perhaps late at night. The Campden reminder, a
        dose for all the water, comes now too: saving the step is when the
        water is drawn. Called by this fork's server from 4.7.7.post15
        (CBPiStep.on_props_saved); an older one never calls it.
        """
        problem = self._settings_problem(brewing_now())
        if problem:
            self.cbpi.notify(self.name, problem, NotificationType.WARNING)
        self._say_campden()

    def _say_campden(self):
        """The Campden dose for all the water, when the reminder is on; or that the volume is blank."""
        if (self.props.get("Campden_Reminder", None) or "No") != "Yes":
            return
        language = self.language
        unit = self.get_config_value("TEMP_UNIT", "C")
        volume = self.props.get("Volume", None)
        volume_unit = self.props.get("Volume_Unit", None)
        try:
            water_l = litres(volume, volume_unit, unit, language)
        except ValueError:
            self.cbpi.notify(self.name, t("notify_campden_no_volume", language),
                             NotificationType.WARNING)
            return
        self.cbpi.notify(
            self.name,
            t("notify_campden", language,
              tablets=format_tablets(campden_tablets(water_l)),
              volume=volume_label(volume, volume_unit, unit)),
            NotificationType.INFO,
        )

    def campden_dose(self):
        """The Campden tablets for the water volume, rounded up to a quarter
        tablet as the reminder's message rounds them; None when the reminder is
        off or the volume isn't usable. CraftBeerPi's strike water helper shows
        it beside the HLT's water, which it can change on brew day."""
        if (self.props.get("Campden_Reminder", None) or "No") != "Yes":
            return None
        try:
            water_l = litres(self.props.get("Volume", None), self.props.get("Volume_Unit", None),
                             self.get_config_value("TEMP_UNIT", "C"))
        except ValueError:
            return None
        return campden_tablets(water_l)

    @action("Change time", change_time_parameters())
    async def change_time(self, Mode=None, StartDate=None, StartTime=None, **kwargs):
        """A new day and time, also while the step waits for it (POST /step2/action/{id}, action change_time).

        CraftBeerPi refuses a running step's settings, and a step waiting for its time is running, so Brew Day sets
        the time with this, the night before. Only the day and the time: not Timing, which can't change while the
        step waits, and not the water, which the strike water box changes. A setting not given stays as it is.

        Waiting, the step plans again as START does - from the water as it is now, with Today and Tomorrow counted
        from now - says the new start in its notice, and keeps the new moment, so a restart waits for it. Not yet
        started or stopped, the settings are saved, and START plans from them. A ready time too soon for the heating
        starts the heating now, as START does.

        Refused with nothing changed, the reason told to every screen as an info, for a time that has passed, a day
        or time that can't be read, a day the form doesn't offer, and a step whose time has come, that has finished,
        or that waits for Next Step. Raises ValueError with that reason then, so POST /step2/action/{id} answers 400
        with it on a core that passes it on (4.7.7.post61 on).

        Ready by heating its kettle itself (1.3.0.post1): once the heating has begun, a new time only changes the ready
        time. The heating carries on, the water is still asked about once it reads Ready_Temp, and the word that the
        water is late, the carry-on after a restart and the screens follow the new time.
        """
        self._read_props()
        language = self.language
        # Blank is Today, as in the form. Only a day given here must be one the form offers: one saved by an older
        # form (DateTime, Daily) and not given stays as it is, as any setting not given does.
        mode = (str(self.props.get("Mode", None) or MODE_TODAY).strip() or MODE_TODAY) if Mode is None \
            else (str(Mode).strip() or MODE_TODAY)
        start_date = str(self.props.get("StartDate", None) or "").strip() if StartDate is None \
            else str(StartDate).strip()
        start_time = str(self.props.get("StartTime", None) or "").strip() if StartTime is None \
            else str(StartTime).strip()
        step = getattr(getattr(self.cbpi, "step", None), "find_by_id", lambda _id: None)(self.id)
        if getattr(step, "status", None) == StepState.DONE:
            self._refuse_change(t("change_finished", language))
        waiting = self.running is True
        heating = waiting and bool(getattr(self, "heating", False))
        if waiting and getattr(self, "arrived", False) and not heating:
            moment = getattr(self, "target_start", None) or getattr(self, "asked_for", None)
            self._refuse_change(t("change_arrived", language, start=self._at(moment) if moment else "?"))
        if waiting and getattr(self, "target_start", None) is None and not heating:
            self._refuse_change(t("change_waiting_heat_now" if getattr(self, "waits_for_heat_now", False)
                                  else "change_waiting_next", language))
        if Mode is not None and mode not in MODES:
            self._refuse_change(t("change_problem", language, error=t("err_unknown_mode", language, mode=mode)))
        now = brewing_now()
        try:
            requested = resolve_start(mode, start_time, start_date, now=now, language=language)
            plan = self._plan(requested) if waiting and self.ready_by and not heating else None
        except ValueError as exc:
            self._refuse_change(t("change_problem", language, error=exc))
        if requested <= now:
            key = "change_passed_today" if mode == MODE_TODAY else "change_passed"
            self._refuse_change(t(key, language, start=self._at(requested)))

        self.props["Mode"] = mode
        self.props["StartDate"] = start_date
        self.props["StartTime"] = start_time
        if not waiting:
            await self.cbpi.step.save()
            return
        was = self.asked_for
        if heating:
            await self._ready_time_changed(was, requested, self._settings_key(mode, start_time, start_date))
            return
        self.plan = plan
        self.ready_at = requested if plan is not None else None
        self.target_start = plan.start if plan is not None else requested
        self.asked_for = requested
        self.pre_notified = False
        self._planned_at = brewing_monotonic()
        await self._keep(self._settings_key(mode, start_time, start_date), requested)
        # The server may carry it on by itself after a restart until two hours after the new time.
        await self._allow_resume(step_id=self.id)
        logger.info("BrewStart: time changed to %s, was %s", self._at(requested), self._at(was))
        remaining = self.wall_clock_remaining(now)
        if remaining <= 0:
            # Ready by with less time left than the heating needs: it starts now, as at START.
            await self._arrive(self._due_message(now))
            return
        self.cbpi.notify(self.name, t("notify_time_changed", language, was=self._at(was),
                                      message=self._scheduled_message()), NotificationType.INFO)
        self._show(remaining)
        await self.push_update()

    async def _ready_time_changed(self, was, requested, key):
        """change_time once the heating has begun: only the time moves - for
        Ready by, the ready time; for Start at, whose heating has begun, nothing
        else. The heating carries on as it is; the word that the water is late
        comes again if a new ready time passes first, and the carry-on after a
        restart is counted from the new time."""
        self.ready_at = requested if self.ready_by else None
        self.asked_for = requested
        self.late_said = False
        await self._keep(key, requested)
        await self._allow_carrying_on()
        logger.info("BrewStart: time changed to %s, was %s; heating carries on",
                    self._at(requested), self._at(was) if was else "?")
        values = dict(was=self._at(was) if was else "?", ready=self._at(requested), kettle=self._kettle_name(),
                      target=format_temp(self._ready_temp() or 0, self._unit()))
        self.cbpi.notify(self.name, t("notify_ready_time_changed" if self.ready_by else "notify_start_time_changed",
                                      self.language, **values), NotificationType.INFO)
        self._show_heating()
        await self.push_update()

    def _refuse_change(self, message):
        """change_time refused: every screen is told why, quietly, and the action endpoint answers 400."""
        logger.info("BrewStart: time not changed - %s", message)
        self.cbpi.notify(self.name, message, NotificationType.INFO)
        raise ValueError(message)

    # ----------------------------------------------------------- change_settings

    @action("Change settings", change_settings_parameters())
    async def change_settings(self, **settings):
        """Any of the step's settings, also while it waits for its time, heats or holds the water
        (POST /step2/action/{id}, action change_settings). CraftBeerPi refuses a running step's settings
        (PUT /step2/{id} answers 409), and Brew Day's BrewStart box and the Brew Steps page offer every setting
        until BrewStart has finished.

        `settings`: any of SETTINGS, by its saved name. One not sent stays as it is, and one sent as it is saved is no
        change, so a whole form may be sent. Blank ("" or None) clears it, read as the form reads a blank. All or
        nothing: a refusal changes nothing, is told to every screen as an info in words that say what to do instead,
        and is raised as ValueError, so POST /step2/action/{id} answers 400 with those words.

        - Not started, or stopped: saved, and acted on as a saved form is (on_props_saved); START plans from them.
        - Waiting for its time: planned again as change_time plans (_replan_waiting).
        - Waiting for Next Step or Heat now: a new Timing, day or time plans again, as waiting; anything else is
          saved, and it waits on.
        - Start at, its time come: when it starts can't change; anything else is saved.
        - Heating or holding: _change_while_heating.

        Volume is never less than the recipe's strike water, the minimum CraftBeerPi's strike water box keeps
        (_check_water). The strike water helper is told of a change at once (_tell_strike).

        Always this step's own settings, changed in place, saved as the step's own saves are: never a new step built
        and swapped in, which would leave this one running out of Stop's reach, its kettle heating, or lose what the
        run has done. What a change does goes by the step's state - running or not - never by what this instance
        remembers of an earlier run, so nothing a reset cleared comes back.
        """
        self._read_props()
        language = self.language
        if self._status() == StepState.DONE:
            self._refuse_settings(t("settings_finished", language))
        new = {}
        for name, value in settings.items():
            if name in IGNORED_SETTINGS:
                continue
            if name not in SETTINGS:
                self._refuse_settings(t("settings_unknown", language, name=name))
            if self._setting_same(name, value):
                continue  # as saved, an older form's choice included: no change, and nothing to check
            try:
                value = self._setting_value(name, value)
            except _Refusal as refusal:
                self._refuse_settings(refusal.words)
            if not self._setting_same(name, value):
                new[name] = value
        if not new:
            return
        before = {name: (name in self.props, self.props[name] if name in self.props else None) for name in SETTINGS}
        for name, value in new.items():
            self.props[name] = value
        self._read_props()
        try:
            await self._apply_settings(new, before)
        except _Refusal as refusal:
            self._restore_settings(before)
            self._read_props()
            self._refuse_settings(refusal.words)
        logger.info("BrewStart: settings changed - %s", ", ".join(sorted(new)))
        self._tell_strike()

    def _refuse_settings(self, message):
        """change_settings refused: every screen is told why, quietly, and the action endpoint answers 400."""
        logger.info("BrewStart: settings not changed - %s", message)
        self.cbpi.notify(self.name, message, NotificationType.INFO)
        raise ValueError(message) from None

    def _restore_settings(self, before):
        for name, (present, value) in before.items():
            if present:
                self.props[name] = value
            elif name in self.props:
                del self.props[name]

    def _setting_value(self, name, value):
        """`value` for setting `name` as it is saved: "" for blank, a number for a number, the text otherwise.
        Raises _Refusal for a value the setting can't take."""
        language = self.language
        if value is None or (isinstance(value, str) and not value.strip()):
            return ""
        setting = t("name_" + SETTING_NAMES[name], language)
        if name in SETTING_CHOICES:
            text = str(value).strip()
            if text not in SETTING_CHOICES[name]:
                raise _Refusal(t("settings_choice", language, setting=setting, value=text,
                                 choices=self._either(SETTING_CHOICES[name])))
            return text
        if name in SETTING_NUMBERS:
            try:
                if isinstance(value, bool):
                    raise ValueError(value)
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError(value)
            except (TypeError, ValueError):
                raise _Refusal(t("settings_number", language, setting=setting, value=value)) from None
            if name in ("Volume", "Heater_Watts") and number <= 0:
                raise _Refusal(t("settings_positive", language, setting=setting))
            if name == "NotifyBefore" and number < 0:
                raise _Refusal(t("settings_not_negative", language, setting=setting))
            if name == "Ready_Temp":
                unit = "F" if is_fahrenheit(self._unit()) else "C"
                if number >= BOILING[unit]:
                    raise _Refusal(t("settings_boiling", language, boil=format_temp(BOILING[unit], unit),
                                     temp=format_temp(number, unit)))
            return number
        text = str(value).strip()
        if name == "Heat_Kettle" and self._kettle_by_id(text) is None:
            raise _Refusal(t("settings_no_kettle", language, value=text))
        return text

    def _either(self, choices):
        """"Today, Tomorrow or Date"."""
        choices = [str(c) for c in choices]
        if len(choices) < 2:
            return "".join(choices)
        return ", ".join(choices[:-1]) + t("settings_or", self.language) + choices[-1]

    def _setting_same(self, name, value):
        """Whether `value` for setting `name` is what is saved, both read as the step reads them."""
        saved = self.props[name] if name in self.props else None
        return self._setting_read(name, saved) == self._setting_read(name, value)

    def _setting_read(self, name, value):
        blank = value is None or (isinstance(value, str) and not value.strip())
        if name in SETTING_NUMBERS:
            if blank:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return str(value).strip()
        text = "" if blank else str(value).strip()
        if not text and name in BLANK_AS:
            return BLANK_AS[name]
        if not text and name == "Volume_Unit":
            return "gal" if is_fahrenheit(self._unit()) else "L"
        return text

    def _volume_unit(self):
        """"gal" or "L": Volume_Unit, a blank one following TEMP_UNIT, as litres() and the strike water box read it."""
        given = str(self.props.get("Volume_Unit", None) or "").strip().lower()
        if given.startswith("gal"):
            return "gal"
        if given in ("l", "liter", "litre", "liters", "litres"):
            return "L"
        return "gal" if is_fahrenheit(self._unit()) else "L"

    def _strike_water(self, unit):
        """The recipe's strike water in `unit` ("gal" or "L"), rounded as CraftBeerPi's strike water box rounds it,
        or None when the recipe doesn't give it: its import keeps it in the brew steps' basic data
        (mash_numbers.infuse_l)."""
        basic = getattr(getattr(self.cbpi, "step", None), "basic_data", None)
        numbers = basic.get("mash_numbers") if isinstance(basic, dict) else None
        try:
            infuse_l = float(numbers.get("infuse_l"))
        except (AttributeError, TypeError, ValueError):
            return None
        if not math.isfinite(infuse_l) or infuse_l <= 0:
            return None
        return round(infuse_l / L_PER_GAL if unit == "gal" else infuse_l, 2)

    def _check_water(self, new):
        """Volume, or a new Volume_Unit, never under the recipe's strike water and the least water the HLT keeps over
        its element and coil once the strike water has gone: the rule and the words of CraftBeerPi's strike water box
        (POST /step2/strike/water's too_little), so the box and this action take and refuse the same water. Nothing is
        refused while the recipe doesn't give its strike water. Raises _Refusal."""
        if "Volume" not in new and "Volume_Unit" not in new:
            return
        try:
            volume = float(self.props.get("Volume", None))
        except (TypeError, ValueError):
            return
        unit = self._volume_unit()
        strike = self._strike_water(unit)
        if strike is None:
            return
        least = self._hlt_least(unit)
        if least is None:
            if volume < strike - 1e-9:
                raise _Refusal(t("settings_too_little", self.language, min="{:.2f}".format(strike), unit=unit))
            return
        if volume - strike < least["value"] - 1e-9:
            raise _Refusal(self._too_little_words(volume, strike, least, unit))

    def _hlt_least(self, unit):
        """{"value", "from", "capacity"}: the least water the HLT keeps over its element and coil, in `unit` ("gal" or
        "L"), as the strike water box counts it (cbpi.strike.vessels.least_water, for the HLT core's find_hlt finds);
        None on a core without it."""
        if _core_least_water is None:
            return None
        try:
            hlt = _core_find_hlt(self.cbpi)
        except Exception:  # noqa: BLE001 - an HLT that can't be found has the cautious figure, as in the box
            hlt = None
        try:
            return _core_least_water(hlt, "F" if is_fahrenheit(self._unit()) else "C", unit)
        except Exception as e:  # noqa: BLE001
            logger.warning("BrewStart: could not count the HLT's least water: %s", e)
            return None

    def _too_little_words(self, volume, strike, least, unit):
        """The strike water box's refusal of too little HLT water (hltWaterRefused), after "BrewStart's settings didn't
        change:". Figures and units joined by a no-break space, as the box joins them."""
        def vol(x):
            return "{:.2f}\u00a0{}".format(x, unit)

        def short(x):
            return "{}\u00a0{}".format(("%.2f" % x).rstrip("0").rstrip("."), unit)

        if least["from"] == "hardware":
            source = t("settings_least_hardware", self.language)
        elif least["from"] == "capacity":
            share = int(round((_CORE_LEAST_SHARE or 0) * 100))
            source = (t("settings_least_capacity", self.language, share=share, capacity=short(least["capacity"]))
                      if least.get("capacity") is not None
                      else t("settings_least_capacity_unknown", self.language, share=share))
        else:
            source = t("settings_least_default", self.language, least=short(least["value"]))
        left = round(volume - strike, 2)
        return t("settings_hlt_water_left" if left >= 0 else "settings_hlt_water_under", self.language,
                 hlt=vol(volume), left=vol(left), mash=vol(strike), least=vol(least["value"]), source=source,
                 min=vol(round(strike + least["value"], 2)))

    def _settings_requested(self, now):
        """The moment the settings ask for, worked out from `now` as START works it out. Raises _Refusal for one that
        can't be read, or has passed."""
        mode, start_time, start_date, _ = self._read_props()
        try:
            requested = resolve_start(mode, start_time, start_date, now=now, language=self.language)
        except ValueError as exc:
            raise _Refusal(t("settings_problem", self.language, error=exc)) from None
        if requested <= now:
            self._refuse_passed(requested, mode)
        return requested

    def _refuse_passed(self, requested, mode):
        key = "settings_passed_today" if mode == MODE_TODAY else "settings_passed"
        raise _Refusal(t(key, self.language, start=self._at(requested) if requested else "?"))

    async def _apply_settings(self, new, before):
        """change_settings with `new` already in the step's settings: checked, then acted on. Raises _Refusal before
        anything outside the settings has changed - or, for a vessel that won't take the heating, with the heating
        put back - and change_settings puts the settings back."""
        when = any(name in new for name in WHEN_SETTINGS)
        self._check_water(new)
        now = brewing_now()
        if self.running is not True:
            if when:
                self._settings_requested(now)
            await self._settings_saved(new)
            return
        if getattr(self, "heating", False):
            await self._change_while_heating(new, before, when, now)
            return
        if getattr(self, "arrived", False):
            if when or "Timing" in new or "AutoNext" in new:
                moment = getattr(self, "target_start", None) or getattr(self, "asked_for", None)
                raise _Refusal(t("settings_arrived", self.language, start=self._at(moment) if moment else "?"))
            await self._settings_kept(new)
            return
        if getattr(self, "target_start", None) is None and not (when or "Timing" in new):
            # Waiting for Next Step or Heat now: only a new time plans again.
            await self._settings_kept(new)
            return
        await self._replan_waiting(new, when, now)

    async def _settings_saved(self, new):
        """Not started, or stopped: saved for START, and acted on as a saved form is. Stopped after it had begun
        heating, START goes back to the heating, on Heat_Kettle at Ready_Temp: a question already asked is taken down
        for a new vessel or temperature."""
        if "Heat_Kettle" in new or "Ready_Temp" in new:
            self.forget_wait()
        await self._save()
        await self.on_props_saved()

    def _forget_heating(self):
        for key in (self.HEATING_PROP, self.ASKED_PROP):
            if key in self.props:
                del self.props[key]

    async def _settings_kept(self, new):
        """Running, with no new time to plan for: saved, and said."""
        _, _, _, notify_before = self._read_props()
        self.notify_before_seconds = notify_before * 60
        self.kettle = self._kettle("Heat_Kettle")
        await self._save()
        self.cbpi.notify(self.name, t("settings_changed", self.language), NotificationType.INFO)
        self._tell_campden(new)
        await self.push_update()

    async def _replan_waiting(self, new, when, now):
        """Waiting for its time - or for Next Step or Heat now, with a new Timing, day or time: planned again as
        change_time plans, from the water as it reads now. The moment already planned stands unless the day or the
        time changed, which count from now - or there was none. Kept for a restart, and the server may carry it on
        until two hours after it. Too little time left for the heating starts it now, as START does."""
        mode, start_time, start_date, notify_before = self._read_props()
        was = getattr(self, "asked_for", None)
        if when or was is None:
            requested = self._settings_requested(now)
        else:
            requested = was
            if requested <= now:
                self._refuse_passed(requested, mode)
        try:
            plan = self._plan(requested) if self.ready_by else None
            if plan is None and self._heats_itself():
                self._check_heating()
        except ValueError as exc:
            raise _Refusal(t("settings_problem", self.language, error=exc)) from None

        self.plan = plan
        self.ready_at = requested if plan is not None else None
        self.target_start = plan.start if plan is not None else requested
        self.asked_for = requested
        self.notify_before_seconds = notify_before * 60
        self.kettle = self._kettle("Heat_Kettle")
        self.pre_notified = False
        self.waits_for_heat_now = False
        self.late_said = False
        self._planned_at = brewing_monotonic()
        key = self._settings_key(mode, start_time, start_date)
        await self._keep(key, requested)
        await self._allow_carrying_on()
        remaining = self.wall_clock_remaining(now)
        if remaining <= 0:
            await self._arrive(self._due_message(now))
            self._tell_campden(new)
            return
        if was is not None and requested != was:
            words = t("notify_time_changed", self.language, was=self._at(was), message=self._scheduled_message())
        else:
            words = t("settings_changed", self.language) + " " + self._scheduled_message()
        self.cbpi.notify(self.name, words, NotificationType.INFO)
        self._tell_campden(new)
        self._show(remaining)
        await self.push_update()

    async def _change_while_heating(self, new, before, when, now):
        """Heating or holding Heat_Kettle, Start at and Ready by alike:

        - Heat_Kettle moves the heating: the old vessel's AUTO off first, so two elements are never on at once, then
          the new vessel's target Ready_Temp and AUTO on. A vessel whose AUTO won't go on is refused, and the old one
          heats again as it did.
        - Ready_Temp moves the vessel's target now, as change_target does.
        - A vessel moved, or a new temperature, takes a question already asked down: it is asked again once the
          water reads Ready_Temp, at once if it already does.
        - A new day or time moves only the time, as change_time does once heating has begun; Timing only says
          whether there is a ready time to be late for. The heating carries on either way.
        - The water, its unit, the element's watts and the grain change the estimate the notice gives.
        """
        language = self.language
        present, old_id = before["Heat_Kettle"]
        old_kettle = self._kettle_by_id(old_id if present else None)
        was = getattr(self, "asked_for", None)
        requested = self._settings_requested(now) if when else was
        try:
            if self.ready_by:
                plan = self._plan(requested or now)
            else:
                self._check_heating()
                plan = self._estimate(requested or now)
        except ValueError as exc:
            raise _Refusal(t("settings_problem", language, error=exc)) from None

        moved = "Heat_Kettle" in new
        if moved and not await self._move_heating(old_kettle, before):
            raise _Refusal(t("settings_cannot_move", language, new=self._kettle_name(),
                             old=getattr(old_kettle, "name", None) or "?"))
        if not moved and "Ready_Temp" in new:
            await self._heat(self._ready_temp())
        if moved or "Ready_Temp" in new:
            reset = getattr(self, "reset_heat_watch", None)
            if callable(reset):
                reset()
            if getattr(self, "asked", False):
                self.forget_wait()
        if when:
            mode, start_time, start_date, _ = self._read_props()
            self.asked_for = self.ready_at = requested
            self.late_said = False
            key = self._settings_key(mode, start_time, start_date)
            await self._keep(key, requested)
            await self._allow_carrying_on()
        else:
            await self._save()
        if not self.ready_by:
            self.ready_at = None  # Start at: its heating has begun, and there is no ready time to be late for
        elif "Timing" in new:
            self.ready_at = self.asked_for  # Ready by from now: hot by its time, said if late
            self.late_said = False

        unit = self._unit()
        values = dict(ready=self._at(self.ready_at) if getattr(self, "ready_at", None) else "?",
                      kettle=self._kettle_name(), target=format_temp(self._ready_temp() or 0, unit))
        if set(new) <= set(WHEN_SETTINGS):
            words = t("notify_ready_time_changed" if self.ready_by else "notify_start_time_changed", language,
                      was=self._at(was) if was else "?", **values)
        else:
            first = (t("notify_time_was", language, was=self._at(was) if was else "?")
                     if when and requested != was else t("settings_changed", language))
            ready = bool(getattr(self, "ready_at", None))
            if moved:
                body = t("notify_heating_moved" if ready else "notify_heating_moved_now", language,
                         old=getattr(old_kettle, "name", None) or "?", new=values["kettle"], target=values["target"],
                         ready=values["ready"])
            elif getattr(self, "asked", False) or (plan is not None and plan.heat_minutes < 0.5):
                body = t("notify_holding", language, kettle=values["kettle"], target=values["target"])
            elif plan is None:
                body = t("notify_heating_plain", language, kettle=values["kettle"], target=values["target"])
            else:
                body = t("notify_heating_estimate" if ready else "notify_heating_estimate_now", language,
                         water=format_temp(plan.water_temp, unit), heat=round(plan.heat_minutes),
                         efficiency=self._efficiency_said(), **values)
            words = first + " " + body
        self.cbpi.notify(self.name, words, NotificationType.INFO)
        self._tell_campden(new)
        self._show_heating()
        await self.push_update()

    def _estimate(self, ready_at):
        """The heating plan for the estimate a Start at's notice gives, or None when the water or the element isn't
        given: Start at needs neither to heat."""
        try:
            return self._plan(ready_at)
        except ValueError:
            return None

    async def _move_heating(self, old, before):
        """The heating from `old` to Heat_Kettle: old's AUTO off first, then Heat_Kettle's target Ready_Temp and its
        AUTO on. False when Heat_Kettle's AUTO won't go on: it is switched off again, and `old` heats again at the
        temperature it had."""
        await self._kettle_off(old)
        if await self._heat(self._ready_temp(), tell=False):
            return True
        await self._kettle_off(self._kettle("Heat_Kettle"))
        if old is not None:
            try:
                present, temp = before["Ready_Temp"]
                if present and temp not in (None, ""):
                    await self.cbpi.kettle.set_target_temp(old.id, float(temp))
                await self.cbpi.kettle.start(old.id)
            except Exception as exc:  # noqa: BLE001 - said in the refusal; the brewer checks it
                logger.warning("BrewStart: could not heat %s again - %s", getattr(old, "name", "?"), exc)
            self.kettle = old
        return False

    def _tell_campden(self, new):
        """The Campden dose for a new water volume, as saving the step gives it, when the reminder is on."""
        if {"Volume", "Volume_Unit", "Campden_Reminder"} & set(new):
            self._say_campden()

    def _tell_strike(self):
        """CraftBeerPi's strike water helper looks at the settings again now, so its state and the topic "strike"
        follow a change at once, as after its own POST /step2/strike/water, rather than at its next look. Nothing on
        a CraftBeerPi without it."""
        update = getattr(getattr(self.cbpi, "strike", None), "update", None)
        if not callable(update):
            return
        try:
            update()
        except Exception as exc:  # noqa: BLE001 - it looks again within a second anyway
            logger.debug("BrewStart: the strike water helper could not look again - %s", exc)

    def _settings_problem(self, now):
        """What would stop these settings starting as meant, or None.

        The water's temperature is not read: at saving time the kettle may
        not even be filled. Only what the brewer chose is checked.
        """
        mode, start_time, start_date, _ = self._read_props()
        try:
            requested = resolve_start(mode, start_time, start_date, now=now,
                                      language=self.language)
            if self.ready_by:
                if not self.props.get("Heat_Kettle", None):
                    raise ValueError(t("err_ready_kettle", self.language))
                plan_ready_by(requested, self.props.get("Ready_Temp", None),
                              self.props.get("Heater_Watts", None),
                              self.get_config_value("TEMP_UNIT", "C"),
                              self.props.get("Volume", None), None,
                              volume_unit=self.props.get("Volume_Unit", None),
                              efficiency=self._efficiency(),
                              language=self.language)
            elif self._heats_itself():
                if not self.props.get("Heat_Kettle", None):
                    raise ValueError(t("err_heat_kettle", self.language))
                if self._ready_temp() is None:
                    raise ValueError(t("err_heat_temp", self.language))
        except ValueError as exc:
            return t("saved_problem", self.language, error=exc)
        if requested <= now:
            key = "saved_passed_today" if mode == MODE_TODAY else "saved_passed"
            return t(key, self.language, start=self._at(requested))
        return None

    # ----------------------------------------------------------- planning

    def _kettle(self, label):
        return self._kettle_by_id(self.props.get(label, None))

    def _kettle_by_id(self, kettle_id):
        if not kettle_id:
            return None
        try:
            return self.get_kettle(kettle_id)
        except Exception:  # noqa: BLE001 - a deleted kettle is simply none
            return None

    def _water_temp(self, kettle):
        """The kettle's current reading, or None when it cannot be trusted."""
        sensor = getattr(kettle, "sensor", None) if kettle is not None else None
        if not sensor:
            return None
        try:
            reading = self.get_sensor_value(sensor)
        except Exception:  # noqa: BLE001
            return None
        value = reading.get("value") if isinstance(reading, dict) else reading
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        if isinstance(reading, dict):
            # This fork's server says how old a reading is and how old it may
            # get. A probe that stopped answering keeps its last value, and
            # planning from it would be planning from the past.
            try:
                age, limit = reading.get("age"), reading.get("max_age")
                if age is not None and limit is not None and float(age) > float(limit):
                    return None
            except (TypeError, ValueError):
                pass
        return value

    def _plan(self, ready_at):
        """The heating plan: all the water in Heat_Kettle, from its probe.

        Only what the form asks for is read, and CraftBeerPi's
        BREWSTART_EFFICIENCY setting: strike and sparge water are heated
        together in the HLT, and the efficiency is the setting's, which USE on
        the Figures page sets from past brews - blank, unreadable or out of
        range is the built-in schedule.HEAT_EFFICIENCY. Strike_Kettle,
        Strike_Volume, Efficiency_Pct and Margin_Minutes saved by an older form
        are ignored, so nothing hidden in a step changes the plan: the spare
        minutes are always DEFAULT_MARGIN_MIN.
        """
        heat = self._kettle("Heat_Kettle")
        if heat is None or not getattr(heat, "sensor", None):
            raise ValueError(t("err_ready_kettle", self.language))
        return plan_ready_by(
            ready_at,
            self.props.get("Ready_Temp", None),
            self.props.get("Heater_Watts", None),
            self._unit(),
            self.props.get("Volume", None),
            self._water_temp(heat),
            volume_unit=self.props.get("Volume_Unit", None),
            efficiency=self._efficiency(),
            language=self.language,
            steel_j_k=self._steel_j_k(heat),
        )

    def _steel_j_k(self, kettle):
        """J/K of Heat_Kettle's own metal, from what it weighs empty and what it's
        made of on CraftBeerPi's Hardware page (4.7.7.post65): heated with the
        water, so counted in the plan. 0.0 without them, or on an older
        CraftBeerPi, and the plan is as it was."""
        try:
            return vessel_j_k(getattr(kettle, "empty_weight", None), getattr(kettle, "material", None),
                              getattr(kettle, "specific_heat", None), self._unit())
        except Exception:  # noqa: BLE001 - a figure that can't be read is none
            return 0.0

    def _efficiency(self):
        """BREWSTART_EFFICIENCY as saved, None when blank: read for every plan,
        so a waiting step plans with a new figure within REPLAN_INTERVAL_S."""
        try:
            return self.get_config_value(EFFICIENCY_SETTING, None)
        except Exception:  # noqa: BLE001 - a server without settings: the built-in
            return None

    def _replan(self):
        """Re-make the plan from the water as it is now."""
        try:
            plan = self._plan(self.ready_at)
        except ValueError as exc:
            logger.warning("BrewStart: keeping the earlier plan - %s", exc)
            return
        lost = plan.assumed and not self.plan.assumed
        moved = (plan.start - self.target_start).total_seconds()
        self.plan = plan
        self.target_start = plan.start
        if abs(moved) >= 60:
            logger.info("BrewStart: heating start moved %+.0f min to %s (water %s)",
                        moved / 60, self._at(plan.start),
                        format_temp(plan.water_temp, self._unit()))
        if lost:
            self._tell_no_reading()

    # ----------------------------------------------------------- the clock

    def wall_clock_remaining(self, now=None):
        """Seconds until the target, measured against the wall clock.

        None when there is no target to measure against.
        """
        if getattr(self, "target_start", None) is None:
            return None
        now = now or brewing_now()
        return int((self.target_start - now).total_seconds())

    def _watch_clock(self, now, monotonic):
        """Report, once, a system clock that has been changed."""
        seen = getattr(self, "_clock_seen", None)
        self._clock_seen = (now, monotonic)
        if seen is None:
            return
        jump = (now - seen[0]).total_seconds() - (monotonic - seen[1])
        if abs(jump) <= self.CLOCK_DRIFT_TOLERANCE_S:
            return
        remaining = max(0, self.wall_clock_remaining(now))
        logger.warning("BrewStart: the system clock moved %+.0fs; %s is %ss away",
                       jump, self._at(self.target_start), remaining)
        self.cbpi.notify(
            self.name,
            t("notify_clock_corrected", self.language,
              start=self._at(self.target_start),
              countdown=format_countdown(remaining)),
            NotificationType.WARNING,
        )

    def clock_moved(self, seconds):
        """CraftBeerPi's clock has just jumped `seconds`, back to the computer's
        time: a simulator's speed changed (CraftBeerPi.brewing_clock, core
        4.7.7.post63; never on a rig). CraftBeerPi leaves a step with a
        clock_moved of its own to it (StepController.replan_after_clock_reset),
        and BrewStart goes on as after a restart:

        - heating or holding its kettle carries on: the kettle's AUTO
          and target stay, and a question asked stays up and is never asked
          again. Its ready time is worked out again from its settings, as START
          does - Today and Tomorrow from the clock as it now is - so it is late
          if that has passed, and may carry on after a restart until two hours
          after it;
        - waiting, it plans again from its settings, as START does: stopped and
          started again, quietly, as CraftBeerPi does a BrewStart without a
          clock_moved of its own;
        - not started, or stopped, it forgets the moment it planned, so START
          plans afresh - one stopped while heating still goes back to the
          heating. Done, or handing over, nothing.
        """
        moved = getattr(super(), "clock_moved", None)
        if callable(moved):
            moved(seconds)  # the stall watch's window
        self._clock_seen = None  # a look before the plan is made again isn't a system clock changed
        if self._status() == StepState.DONE or getattr(self, "_handover", None) is not None:
            return
        if not self.running:
            self._forget()
        elif getattr(self, "heating", False):
            self._ready_time_again()
        else:
            self._spawn(self._plan_again())

    def _status(self):
        step = getattr(getattr(self.cbpi, "step", None), "find_by_id", lambda _id: None)(self.id)
        return getattr(step, "status", None)

    def _ready_time_again(self):
        """Heating or holding as the clock was reset: the ready time from the
        settings again, as START works it out. The heating carries on whatever
        it is, as after change_time once heating has begun."""
        mode, start_time, start_date, _ = self._read_props()
        try:
            requested = resolve_start(mode=mode, start_time=start_time, start_date=start_date,
                                      now=brewing_now(), language=self.language)
        except ValueError as exc:
            logger.warning("BrewStart: the clock was reset; keeping the ready time - %s", exc)
            return
        if requested != getattr(self, "ready_at", None):
            self.late_said = False  # late for the new time is said, once
        self.ready_at = self.asked_for = requested
        if not self.ready_by:
            self.ready_at = None  # Start at: the heating has begun, and there is no ready time to be late for
        self.props[self.PLANNED_PROP] = "{}|{}".format(
            self._settings_key(mode, start_time, start_date), requested.isoformat())
        self._spawn(self._allow_carrying_on())
        self._show_heating()
        logger.info("BrewStart: the clock was reset; carries on heating %s, ready by %s",
                    self._kettle_name(), self._at(requested))

    async def _plan_again(self):
        """Waiting as the clock was reset: stopped, the moment it planned
        forgotten, and started again, so START's planning counts Today and
        Tomorrow from the clock as it now is. The server isn't told it stopped
        or started; the brewer is told the new plan, as START tells it."""
        try:
            await self.stop()
            await self.reset()
            await self.start()
            logger.info("BrewStart: plans again from its settings, the clock having been reset")
        except Exception as exc:  # noqa: BLE001 - the step goes on as it was
            logger.warning("BrewStart: could not plan again after the clock was reset - %s", exc)

    def _show(self, remaining):
        if self.plan is not None:
            water = ("?" if self.plan.assumed
                     else format_temp(self.plan.water_temp, self._unit()))
            self.summary = t("summary_ready_waiting", self.language,
                             ready=self._at(self.ready_at),
                             start=self._at(self.target_start),
                             water=water, countdown=format_countdown(remaining))
        else:
            self.summary = t("summary_waiting", self.language,
                             start=self._at(self.target_start),
                             countdown=format_countdown(remaining))

    def _prewarn(self, remaining):
        if (self.notify_before_seconds > 0 and not self.pre_notified
                and 0 < remaining <= self.notify_before_seconds):
            self.pre_notified = True
            self.cbpi.notify(self.name,
                             t("notify_prewarn_ready", self.language,
                               countdown=format_countdown(remaining)),
                             NotificationType.WARNING)

    async def _tick(self, now=None, monotonic=None):
        """One look at the clock. True when the step should hand over: only
        before a MashInStep on Heat_Kettle right after it (_hands_over)."""
        if getattr(self, "heating", False):
            # Heating or holding its kettle: only the brewer's Next moves it on.
            await self._heat_tick(now or brewing_now())
            return False
        if self.arrived:
            return self._hands_over()
        if self.target_start is None:
            return False  # a configuration error: only Next moves on
        now = now or brewing_now()
        monotonic = brewing_monotonic() if monotonic is None else monotonic
        self._watch_clock(now, monotonic)
        if self.plan is not None and monotonic - self._planned_at >= self.REPLAN_INTERVAL_S:
            self._planned_at = monotonic
            self._replan()
        remaining = self.wall_clock_remaining(now)
        if remaining <= 0:
            if self._past_carrying_on(now):
                await self._too_late(now)
                return False
            await self._arrive(self._reached_message())
            # Heating its kettle itself, it stays: only the brewer's Next moves it on.
            return self._hands_over() and not getattr(self, "heating", False)
        self._show(remaining)
        self._prewarn(remaining)
        await self.push_update()
        return False

    def _read_props(self):
        mode = str(self.props.get("Mode", None) or MODE_TODAY).strip() or MODE_TODAY
        start_time = self.props.get("StartTime", None)
        start_date = self.props.get("StartDate", None)
        start_time = "" if start_time is None else str(start_time).strip()
        start_date = "" if start_date is None else str(start_date).strip()
        # One language for the whole plugin, from the server's settings. The
        # step's own Language setting, saved by older forms, is not read.
        self.language = configured_language(self.cbpi)
        # A blank StartTime is a configuration error, as a blank StartDate is
        # for a Date, not a quiet 06:00.

        raw_notify = self.props.get("NotifyBefore", None)
        try:
            if raw_notify is None or raw_notify == "":
                notify_before = 15
            else:
                notify_before = int(float(raw_notify))
        except (TypeError, ValueError):
            notify_before = 15

        # Start at or Ready by. A Timing left blank, or anything else an older form
        # saved, is Ready by. Both heat Heat_Kettle and hold it until the brewer's
        # Next (_tick); AutoNext, saved by older forms, is not read.
        self.ready_by = (self.props.get("Timing", None) or TIMING_READY) != TIMING_START
        return mode, start_time, start_date, max(0, notify_before)

    # ----------------------------------------------------------- remembering

    def _settings_key(self, mode, start_time, start_date):
        """What the kept moment was worked out from. The step's id is in it, so
        a moment copied along with a profile into a recipe, and loaded back as
        a new step, is not mistaken for this one's."""
        return "{}|{}|{}|{}".format(self.id, mode, start_time, start_date)

    def _recall(self, key, now):
        """The moment kept for these settings, or None."""
        kept = self.props.get(self.PLANNED_PROP, None)
        if not kept:
            return None
        settings, _, moment = str(kept).rpartition("|")
        if settings != key:
            return None  # the settings have changed since: work it out afresh
        try:
            moment = datetime.fromisoformat(moment)
        except ValueError:
            return None
        if moment < now - self.RECALL_WITHIN:
            return None
        return moment

    async def _keep(self, key, moment):
        self.props[self.PLANNED_PROP] = "{}|{}".format(key, moment.isoformat())
        try:
            await self.cbpi.step.save()
        except Exception as exc:  # noqa: BLE001 - kept in memory either way
            logger.warning("BrewStart: could not save the planned start - %s", exc)

    def _forget(self):
        try:
            if self.PLANNED_PROP in self.props:
                del self.props[self.PLANNED_PROP]
        except Exception:  # noqa: BLE001
            self.props[self.PLANNED_PROP] = ""

    async def on_start(self):
        self.timer = None
        self._handover = None
        self.arrived = False
        self.pre_notified = False
        self.plan = None
        self.ready_at = None
        self.target_start = None
        self.asked_for = None
        self._clock_seen = None
        self._planned_at = brewing_monotonic()
        self.heating = False
        self.asked = False
        self.late_said = False
        self.waits_for_heat_now = False
        mode, start_time, start_date, notify_before = self._read_props()
        self.kettle = self._kettle("Heat_Kettle")
        self.notify_before_seconds = notify_before * 60
        now = brewing_now()
        key = self._settings_key(mode, start_time, start_date)

        try:
            requested = self._recall(key, now)
            resumed = requested is not None
            if not resumed:
                requested = resolve_start(
                    mode=mode,
                    start_time=start_time,
                    start_date=start_date,
                    now=now,
                    language=self.language,
                )
            if self.ready_by:
                self.plan = self._plan(requested)
                self.ready_at = requested
                self.target_start = self.plan.start
            else:
                # Start at: heating begins at the time itself, so there is no plan; it needs what it heats.
                if self._heats_itself():
                    self._check_heating()
                self.target_start = requested
        except ValueError as exc:
            logger.error("BrewStart config error: %s", exc)
            self.plan = None
            self.ready_at = None
            self.target_start = None
            self.summary = t("error_prefix", self.language, error=exc)
            await self.push_update()
            # Never starts by itself after a configuration error: one
            # notification, with the way on.
            self._offer_next(t("error_prefix", self.language, error=exc),
                             NotificationType.ERROR)
            return

        if self.props.get(self.HEATING_PROP, False):
            # It had begun heating its kettle when it stopped, or the server did:
            # back to the heating at once, and to the question once the water
            # reads Ready_Temp. Never a second wait for the time.
            self.asked_for = requested
            await self._allow_carrying_on()
            await self._start_heating(resumed=True)
            logger.info("BrewStart: back to heating %s", self._kettle_name())
            return

        if not resumed:
            if requested <= now:
                await self._already_passed(requested, mode)
                return
            # Kept only once it stands: a moment that was never valid must not
            # come back later as if a brew had been interrupted on its way to it.
            await self._keep(key, requested)

        # The time it was asked for - the ready time, or the start time. Allowed
        # from here to carry on by itself after a restart.
        self.asked_for = requested
        await self._allow_resume(self.id)

        if self.plan is not None and self.plan.assumed:
            self._tell_no_reading()

        remaining = self.wall_clock_remaining(now)
        if remaining <= 0:
            if self._past_carrying_on(now):
                await self._too_late(now)
                return
            # Less time left than the heating needs, or a brew resumed after its
            # time: heat now - the brewer set this up. Only Next moves on.
            await self._arrive(self._due_message(now))
            return

        self.cbpi.notify(self.name, self._scheduled_message(), NotificationType.INFO)
        self._show(remaining)
        await self.push_update()
        logger.info("BrewStart waiting %s seconds until %s",
                    remaining, self._at(self.target_start))

    async def on_stop(self):
        self.summary = ""
        if self._switches_heat_off():
            await self._heat_off()
        await self.push_update()

    def _switches_heat_off(self):
        """On Next and on Stop, a BrewStart that heats its kettle itself switches
        that kettle's AUTO off, before the next step starts: two logics must
        never drive one element, and the mash tun's PID_HERMS drives the HLT's.
        Next does even when the step never heated it. Before a MashInStep on the
        same kettle right after it, it is left on: that step goes on heating it."""
        if getattr(self, "heating", False) or self.props.get(self.HEATING_PROP, False):
            return True
        return self.cancel_reason == StepResult.NEXT and not self._next_heats_this_kettle()

    async def reset(self):
        self.summary = ""
        self._forget()
        for key in (self.HEATING_PROP, self.ASKED_PROP):
            try:
                if key in self.props:
                    del self.props[key]
            except Exception:  # noqa: BLE001
                self.props[key] = False

    def _pause(self):
        """Real seconds until the next look at the clock.

        Looking is a cadence: once a real second however fast the clock runs,
        or a simulation would be flooded with updates. The start is an instant,
        though, and a look a whole TICK_S late is SIM_TIME_SCALE seconds late in
        brewing time - a simulated minute at 60x. So in a simulation the last
        pause before the start ends at the start.
        """
        source = _simulation_clock()
        remaining = self.wall_clock_remaining()
        if source is None or remaining is None or self.arrived:
            return self.TICK_S
        rate = getattr(source, "rate", 1.0) or 1.0
        return min(self.TICK_S, max(0.0, remaining / rate))

    async def run(self):
        while self.running is True:
            if await self._tick() and getattr(self, "_handover", None) is None:
                # Handed over the way every step is: next() from a task of its
                # own, which cancels this one and reports NEXT. Returning from
                # run() reports DONE, and StepController.done() acts only on
                # NEXT and ERROR, so returning would leave the profile on a
                # finished step for good.
                self._handover = asyncio.create_task(self._hand_over())
            await asyncio.sleep(self._pause())
        return StepResult.DONE
