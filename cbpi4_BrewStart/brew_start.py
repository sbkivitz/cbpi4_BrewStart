import asyncio
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
                           campden_tablets, format_countdown, format_start,
                           format_tablets, format_temp, litres, plan_ready_by,
                           resolve_start, volume_label)
    from .strings import DEFAULT_LANGUAGE, normalise_language, t
except ImportError:
    from cbpi4_BrewStart.schedule import (LEGACY_DATE, MODE_DATE, MODE_TODAY,
                                          MODES, campden_tablets,
                                          format_countdown, format_start,
                                          format_tablets, format_temp, litres,
                                          plan_ready_by, resolve_start,
                                          volume_label)
    from cbpi4_BrewStart.strings import DEFAULT_LANGUAGE, normalise_language, t

logger = logging.getLogger(__name__)

TIMING_START = "Start at"
TIMING_READY = "Ready by"

#: The server setting that picks the language of the step's form and messages.
LANGUAGE_SETTING = "BREWSTART_LANGUAGE"


def configured_language(cbpi):
    """The language chosen in the server's settings; English when unset."""
    try:
        value = cbpi.config.get(LANGUAGE_SETTING, None)
    except Exception:  # noqa: BLE001 - a server without settings: the default
        value = None
    return normalise_language(value)


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

    Scott, 29 Oct: "If my language is English, I should not see other
    languages" - the descriptions were English and German side by side, some
    German only. The form asks only for what applies: the date only for a
    Date, the heating fields only for Ready by, "Go on by itself" only for
    Start at (Ready by always goes on - waiting for a click there meant the
    water never heated). Strike_Kettle, Strike_Volume, Efficiency_Pct,
    Margin_Minutes and Language are no longer asked for; see BrewStart._plan
    and configured_language.

    And, after Apple's design principles (Scott sent them the same day): the
    fields are named in words ("Time", not "StartTime" - the label stays the
    saved setting's name), Timing comes first because it decides what the time
    means, numbers carry their units, and a new step starts from the defaults
    rather than blanks whose meaning is only in the help text.
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
        _field(Property.Select(label="Timing", options=[TIMING_START, TIMING_READY],
                               default_value=TIMING_START, description=says("timing")),
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


def _simulation_clock():
    """The simulation's clock when one runs the server, else None.

    This fork's server has one clock for everything that measures time
    (cbpi.api.clock). On a rig its source is real time, and the step asks the
    wall clock itself, as it always has. A simulation (cbpi4-simvessel) runs
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
      start earlier rather than leaving the water short.

    Why the step's own loop looks at the wall clock, with no countdown:

    The first version computed `target - datetime.now()` once and handed that
    many seconds to a Timer. A Timer counts a pure duration and has no idea
    what time it is, so anything that moved the system clock moved the start
    by as much, silently - and a Raspberry Pi has no RTC: after a power cut it
    boots with a wrong clock, CraftBeerPi autostarts, the step resolves
    against that wrong `now()`, and NTP corrects the clock a minute later. A
    wait across a daylight saving change is out by an hour the same way.

    The next version (this fork, 12 Oct) kept the Timer and rebuilt it
    whenever it disagreed with the wall clock. The rebuild ran inside the old
    Timer's own update, and a Timer cannot be stopped from inside itself:
    stop() cancels the task and then awaits it, the await swallows the
    cancellation, and the old countdown runs on - re-detecting the same drift
    every second. Measured on 23 Oct with the real Timer: one 100 s clock
    correction produced 24 "System clock changed" notifications, 8 "start
    time reached", and a countdown still running after the step had finished.

    So nothing is rebuilt. run() compares the target with the wall clock every
    TICK_S and hands over when it is reached; a clock that jumps is reported
    once, and the target - an absolute local datetime - is simply still right.

    "The wall clock" is the clock the brew runs by: the PC's on a rig, the
    simulation's in a simulation (brewing_now). Until 1.2.0.post3 the step
    read the PC's clock in a simulation too. At SIM_TIME_SCALE 60 it waited
    real minutes for a start a few simulated minutes away, while the water
    heated by the simulation's clock - and was hot long before the time it
    had been asked for.

    When, from 1.2.0.post4: Today, Tomorrow or a Date (schedule.resolve_start).
    The moment worked out is kept with the step (PLANNED_PROP), because Today
    and Tomorrow mean nothing on their own: a step that worked Tomorrow out
    again at 05:30 on brew day, after a restart, would wait until the day
    after. And a time that had already passed when the brew was started is
    never acted on by itself - an old Date, or Today chosen in the evening for
    the next morning, would otherwise start heating at once with nobody
    expecting it.

    After a power cut, from 1.2.0.post5 (Scott, 30 Oct: "BrewStart should
    resume after an overnight power cut and attempt to honor the contract to
    have hot water ready for me"): the step allows the server to carry it on
    by itself, and the step it hands over to, until RESUME_GRACE after the
    time it was asked for (StepController.allow_resume, cbpi4 4.7.7.post21).
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

    #: Until on_start reads the server's setting.
    language = DEFAULT_LANGUAGE

    async def NextStep(self, **kwargs):
        await self._hand_over()

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
        RESUME_GRACE after the time asked for. False on a server that cannot -
        one from before cbpi4 4.7.7.post21 asks "Resume brew?", as before."""
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
        self._offer_next(t("notify_too_late", self.language, now=self._at(now),
                           asked=self._at(self.asked_for),
                           hours=round(self.RESUME_GRACE.total_seconds() / 3600)),
                         NotificationType.WARNING, wrap=False)

    # ----------------------------------------------------------- messages

    def _at(self, moment):
        return format_start(moment, self.language)

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
                 margin=round(self.plan.margin_minutes))

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
                 eta=self._at(eta), ready=self._at(self.ready_at))

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

    async def _arrive(self, message):
        """The time has come: hand over, or wait for Next when AutoNext is No."""
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

    async def on_props_saved(self):
        """Feedback as soon as the brewer saves the step, not at brew time.

        A time that has already passed, or settings the step cannot start
        from, are said now - while the form is still in mind - rather than when
        the brew is started, perhaps late at night. And the Campden dose -
        Scott, 29 Oct: remind "when setting the BrewStart setting - or just
        after clicking okay/save", which is when the water is drawn. Until
        1.2.0.post4 the dose came when the brew started, for the strike water
        only. Called by this fork's server from 4.7.7.post15
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

        Only what the form asks for is read (Scott, 29 Oct: strike and sparge
        water are heated together in the HLT, and the efficiency and spare
        minutes are not his to set). Strike_Kettle, Strike_Volume,
        Efficiency_Pct and Margin_Minutes saved by an older form are ignored,
        so nothing hidden changes the plan: the built-in efficiency
        (schedule.HEAT_EFFICIENCY) and spare minutes (DEFAULT_MARGIN_MIN) apply.
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
            language=self.language,
        )

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
            return self.auto_next
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
        # step's own Language setting, from older forms, is no longer read.
        self.language = configured_language(self.cbpi)
        # A blank StartTime is a configuration error, as a blank StartDate is
        # for a Date: until 1.2.0.post4 it quietly meant 06:00.

        raw_notify = self.props.get("NotifyBefore", None)
        try:
            if raw_notify is None or raw_notify == "":
                notify_before = 15
            else:
                notify_before = int(float(raw_notify))
        except (TypeError, ValueError):
            notify_before = 15

        self.ready_by = (self.props.get("Timing", None) or TIMING_START) == TIMING_READY
        # Ready by always goes on by itself: waiting for a click at the heating
        # start would leave the water cold by the time it was asked for. Until
        # 1.2.0.post4 the form allowed it, and the rig page had to warn.
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
        mode, start_time, start_date, notify_before, self.auto_next = self._read_props()
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

        if not resumed:
            if requested <= now:
                await self._already_passed(requested, mode)
                return
            # Kept only once it stands: a moment that was never valid must not
            # come back later as if a brew had been interrupted on its way to it.
            await self._keep(key, requested)

        # The time it was asked for - the ready time, or the start time. Allowed
        # from here to carry on by itself after a restart (Scott, 30 Oct).
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
        await self.push_update()

    async def reset(self):
        self.summary = ""
        self._forget()

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
                # NEXT and ERROR - the profile sat on a finished step for good,
                # seen live on 23 Oct.
                self._handover = asyncio.create_task(self._hand_over())
            await asyncio.sleep(self._pause())
        return StepResult.DONE
