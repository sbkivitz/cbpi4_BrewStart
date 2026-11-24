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
    from .schedule import (LEGACY_DATE, MODE_DATE, MODE_TODAY, MODES,
                           campden_tablets, efficiency_fraction,
                           efficiency_setting, format_countdown, format_percent,
                           format_start, format_tablets, format_temp, litres,
                           plan_ready_by, resolve_start, vessel_j_k,
                           volume_label)
    from .strings import DEFAULT_LANGUAGE, normalise_language, t
except ImportError:
    from cbpi4_BrewStart.schedule import (LEGACY_DATE, MODE_DATE, MODE_TODAY,
                                          MODES, campden_tablets,
                                          efficiency_fraction,
                                          efficiency_setting,
                                          format_countdown, format_percent,
                                          format_start, format_tablets,
                                          format_temp, litres, plan_ready_by,
                                          resolve_start, vessel_j_k,
                                          volume_label)
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
ONLY_FOR_READY_BY = {"Timing": TIMING_READY}
ONLY_FOR_START_AT = {"Timing": ["", TIMING_START]}
FOR_THE_WATER = [ONLY_FOR_READY_BY, {"Campden_Reminder": "Yes"}]


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
    German side by side. The form asks only for what applies: the date only
    for a Date, the heating fields only for Ready by, "Go on by itself" only
    for Start at (Ready by heats the water itself and asks for it to be moved,
    and only Next moves it on). Strike_Kettle, Strike_Volume, Efficiency_Pct,
    Margin_Minutes and Language are not asked for; see BrewStart._plan and
    configured_language.

    After Apple's design principles, the fields are named in words ("Time",
    not "StartTime" - the label stays the saved setting's name), Timing comes
    first because it decides what the time means, numbers carry their units,
    and a new step starts from the defaults rather than blanks whose meaning
    is only in the help text.
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
        # A new step starts on Ready by, the one the brewery uses. A step saved
        # with no Timing at all still runs as Start at (see _read_props): that is
        # what it meant before Timing existed.
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
               name("heat_kettle"), ONLY_FOR_READY_BY),
        _field(Property.Number(label="Ready_Temp", configurable=True, unit=degrees(temp_unit),
                               description=says("ready_temp")),
               name("ready_temp"), ONLY_FOR_READY_BY),
        _field(Property.Number(label="Volume", configurable=True, description=says("volume")),
               name("volume"), FOR_THE_WATER),
        _field(Property.Select(label="Volume_Unit", options=["gal", "L"],
                               description=says("volume_unit")),
               name("volume_unit"), FOR_THE_WATER),
        _field(Property.Number(label="Grain_Temp", configurable=True, unit=degrees(temp_unit),
                               description=says("grain_temp")),
               name("grain_temp"), FOR_THE_WATER),
        _field(Property.Number(label="Heater_Watts", configurable=True, unit="W",
                               description=says("heater_watts")),
               name("heater_watts"), ONLY_FOR_READY_BY),
        _field(Property.Select(label="Campden_Reminder", options=["No", "Yes"],
                               default_value="No", description=says("campden")),
               name("campden")),
        _field(Property.Number(label="NotifyBefore", configurable=True, unit="min",
                               description=says("notify_before")),
               name("notify_before")),
        _field(Property.Select(label="AutoNext", options=["Yes", "No"], default_value="Yes",
                               description=says("auto_next")),
               name("auto_next"), ONLY_FOR_START_AT),
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
    """Wait until a configured time, judged against the wall clock, then hand on.

    Two ways to say when:

    - Start at: the next step starts at the time given.
    - Ready by: the water must be at Ready_Temp by the time given, and the
      step works out when heating has to start - from the water's measured
      temperature, the volumes and the element's power (schedule.py). The
      plan is re-made once a minute, so water that cools overnight moves the
      start earlier rather than leaving the water short. At the heating start
      the step heats Heat_Kettle itself (1.3.0.post1): its target Ready_Temp, its
      AUTO on. Once the water reads Ready_Temp it holds it there and asks for
      the strike water to be moved, and only the brewer's Next moves on, with
      Heat_Kettle's AUTO switched off first: on a HERMS the mash tun's logic
      drives the same element. Before a MashInStep on Heat_Kettle - a recipe
      made for an older BrewStart - it hands over at the heating start instead,
      as it always did, and that step heats the water and asks.

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
        """Next Step on "Move the strike water to the Mash Tun, then press Next":
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
            return t("notify_scheduled", self.language,
                     start=self._at(self.target_start))
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
            return t("notify_already_passed", self.language,
                     start=self._at(self.target_start))
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
        return t("notify_reached_at", self.language,
                 start=self._at(self.target_start))

    def _offer_next(self, message, kind=NotificationType.INFO, wrap=True):
        self.cbpi.notify(
            self.name,
            t("notify_next_step", self.language, message=message) if wrap else message,
            kind,
            action=[NotificationAction(
                t("action_next_step", self.language), self.NextStep)],
        )

    def _offer_heat_now(self, message):
        """A Ready by that heats its kettle itself, whose time has passed: nothing
        heats by itself, and the button is Heat now, not Next Step - Next would
        move on to the mash tun with the water still cold."""
        self.waits_for_heat_now = True
        self.cbpi.notify(
            self.name, message, NotificationType.WARNING,
            action=[NotificationAction(t("action_heat_now", self.language), self.HeatNow)],
        )

    async def _arrive(self, message):
        """The time has come: Ready by heats Heat_Kettle itself (or, before a
        MashInStep on that kettle, hands over to it, as it heats and asks); Start
        at hands over, or waits for Next when AutoNext is No."""
        if self._heats_itself():
            await self._start_heating(message)
            return
        if self.plan is not None:
            # Ready by before a strike water step on Heat_Kettle: that step heats from now.
            self._record_heating()
        self.arrived = True
        self.summary = t("summary_ready_reached" if self.plan is not None
                         else "summary_reached", self.language)
        await self.push_update()
        if self.auto_next:
            self.cbpi.notify(self.name, message, NotificationType.SUCCESS)
        else:
            self._offer_next(message)

    async def _already_passed(self, requested, mode):
        """The time asked for was over before the brew was started.

        A Date left from an earlier brew, or Today chosen in the evening for
        the next morning. Going on would start the next step - and on a Ready
        by profile, the heating - at once, with nobody expecting it. So
        nothing happens by itself, whatever AutoNext says: the brewer is told,
        and Next Step goes on.
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
        """The next step on a kettle is a MashInStep on Heat_Kettle.

        A recipe made for BrewStart before 1.3.0.post1, as the rig page's step 9b has
        it: BrewStart, then a strike water step on the HLT that heats the water,
        holds it and asks for it to be moved itself. There BrewStart hands over
        as the heating starts, as it always did, so the water isn't heated or
        asked about twice. Steps on no kettle between them, a notification say,
        don't count.
        """
        kettle = self.props.get("Heat_Kettle", None)
        following = self._next_on_a_kettle()
        return bool(kettle and following is not None and getattr(following, "type", None) == "MashInStep"
                    and following.props.get("Kettle", None) == kettle)

    def _heats_itself(self):
        """Ready by heats Heat_Kettle itself, holds the water and asks for it to
        be moved, unless the next step on a kettle is a MashInStep on that one."""
        return bool(getattr(self, "ready_by", False)) and not self._next_heats_this_kettle()

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

    async def _heat(self, target):
        """Heat_Kettle's target to `target`, and its AUTO on if it isn't: its
        logic heats the water there and holds it. False when it can't."""
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
            self.cbpi.notify(self.name, t("notify_cannot_heat", self.language, kettle=kettle.name),
                             NotificationType.ERROR)
            return False
        return True

    async def _heat_off(self):
        """Heat_Kettle's AUTO off, so the next step's logic is the only one
        driving its element: on the rig the mash tun's PID_HERMS drives the HLT's."""
        kettle = self._kettle("Heat_Kettle")
        instance = getattr(kettle, "instance", None) if kettle is not None else None
        if instance is None or not getattr(instance, "state", False):
            return
        try:
            await self.cbpi.kettle.stop(kettle.id)
            logger.info("BrewStart: %s's AUTO off", kettle.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("BrewStart: could not switch %s's AUTO off - %s", kettle.name, exc)

    async def _start_heating(self, message=None, resumed=False):
        """Ready by's time to heat: Heat_Kettle's target is Ready_Temp and its
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
        else:
            self.summary = t("summary_heating", self.language, **values)

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
        """"Move the strike water to the Mash Tun, then press Next", with Next
        Step, as the strike water step on the HLT asks it: kept on every page
        until answered, and taken down when the step stops or ends. Its button is
        the only thing that moves a Ready by BrewStart on, with the screens'
        NEXT. The phone alert plugin sends a question with a button on."""
        notify = self.cbpi.notify
        try:
            kept = {"keep": True} if "keep" in inspect.signature(notify).parameters else {}
        except (TypeError, ValueError):
            kept = {}
        question = notify(
            self.name,
            t("notify_water_ready", self.language, to=self._next_kettle_name()),
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
        """change_time once the heating has begun: only the ready time moves.
        The heating carries on as it is; the word that the water is late comes
        again if the new time passes first, and the carry-on after a restart is
        counted from the new time."""
        self.ready_at = requested
        self.asked_for = requested
        self.late_said = False
        await self._keep(key, requested)
        await self._allow_carrying_on()
        logger.info("BrewStart: ready time changed to %s, was %s; heating carries on",
                    self._at(requested), self._at(was) if was else "?")
        self.cbpi.notify(self.name, t("notify_ready_time_changed", self.language,
                                      was=self._at(was) if was else "?", ready=self._at(requested),
                                      kettle=self._kettle_name(),
                                      target=format_temp(self._ready_temp() or 0, self._unit())),
                         NotificationType.INFO)
        self._show_heating()
        await self.push_update()

    def _refuse_change(self, message):
        """change_time refused: every screen is told why, quietly, and the action endpoint answers 400."""
        logger.info("BrewStart: time not changed - %s", message)
        self.cbpi.notify(self.name, message, NotificationType.INFO)
        raise ValueError(message)

    def _settings_problem(self, now):
        """What would stop these settings starting as meant, or None.

        The water's temperature is not read: at saving time the kettle may
        not even be filled. Only what the brewer chose is checked.
        """
        mode, start_time, start_date, _, _ = self._read_props()
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
        except ValueError as exc:
            return t("saved_problem", self.language, error=exc)
        if requested <= now:
            key = "saved_passed_today" if mode == MODE_TODAY else "saved_passed"
            return t(key, self.language, start=self._at(requested))
        return None

    # ----------------------------------------------------------- planning

    def _kettle(self, label):
        kettle_id = self.props.get(label, None)
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

        - Ready by heating or holding its kettle carries on: the kettle's AUTO
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
        mode, start_time, start_date, _, _ = self._read_props()
        try:
            requested = resolve_start(mode=mode, start_time=start_time, start_date=start_date,
                                      now=brewing_now(), language=self.language)
        except ValueError as exc:
            logger.warning("BrewStart: the clock was reset; keeping the ready time - %s", exc)
            return
        if requested != getattr(self, "ready_at", None):
            self.late_said = False  # late for the new time is said, once
        self.ready_at = self.asked_for = requested
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
            key = "notify_prewarn_ready" if self.plan is not None else "notify_prewarn"
            self.cbpi.notify(self.name,
                             t(key, self.language,
                               countdown=format_countdown(remaining)),
                             NotificationType.WARNING)

    async def _tick(self, now=None, monotonic=None):
        """One look at the clock. True when the step should hand over."""
        if getattr(self, "heating", False):
            # Ready by heating its kettle itself: only Next moves it on.
            await self._heat_tick(now or brewing_now())
            return False
        if self.arrived:
            return self.auto_next
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
            return self.auto_next and not getattr(self, "heating", False)
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

        self.ready_by = (self.props.get("Timing", None) or TIMING_START) == TIMING_READY
        # AutoNext is for Start at only, whatever an older form saved for Ready
        # by. Ready by heats Heat_Kettle itself at the heating start and only the
        # brewer's Next moves it on (_tick); before a MashInStep on that kettle it
        # hands over then, as it always did, so that step heats the water.
        auto_next = self.ready_by or (self.props.get("AutoNext", None) or "Yes") == "Yes"
        return mode, start_time, start_date, max(0, notify_before), auto_next

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
        mode, start_time, start_date, notify_before, self.auto_next = self._read_props()
        self.kettle = self._kettle("Heat_Kettle") if self.ready_by else None
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

        if self.ready_by and self.props.get(self.HEATING_PROP, False):
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
            # Ready by with less time left than the heating needs, or a brew
            # resumed after its time: go on now - the brewer set this up.
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
        """On Next and on Stop, a Ready by that heats its kettle itself switches
        that kettle's AUTO off, before the next step starts: two logics must
        never drive one element, and the mash tun's PID_HERMS drives the HLT's.
        Next does even when the step never heated it. Before a MashInStep on the
        same kettle it is left on: that step goes on heating it."""
        if not getattr(self, "ready_by", False):
            return False
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
