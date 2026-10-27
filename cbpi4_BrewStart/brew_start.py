import asyncio
import logging
import math
import time
from datetime import datetime, timedelta

from cbpi.api import *
from cbpi.api.dataclasses import NotificationAction, NotificationType
from cbpi.api.step import CBPiStep, StepResult

try:
    from cbpi.api import clock as brewing_clock
except ImportError:  # upstream CraftBeerPi: the wall clock is the only clock
    brewing_clock = None

try:
    from .schedule import (campden_tablets, format_countdown, format_start,
                           format_tablets, format_temp, litres, plan_ready_by,
                           resolve_start, volume_given, volume_label)
    from .strings import available_languages, normalise_language, t
except ImportError:
    from cbpi4_BrewStart.schedule import (campden_tablets, format_countdown,
                                          format_start, format_tablets,
                                          format_temp, litres, plan_ready_by,
                                          resolve_start, volume_given,
                                          volume_label)
    from cbpi4_BrewStart.strings import available_languages, normalise_language, t

logger = logging.getLogger(__name__)

TIMING_START = "Start at"
TIMING_READY = "Ready by"


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


@parameters(
    [
        Property.Select(
            label="Mode",
            options=["Today", "DateTime", "Daily"],
            description=(
                "Today: today at the start time, else tomorrow / heute zur "
                "Startzeit, sonst morgen. "
                "DateTime: fixed date and time / festes Datum + Uhrzeit. "
                "Daily: next occurrence / nächste Uhrzeit. "
                "NOTE: Daily currently behaves exactly like Today - it does "
                "NOT repeat on later days."
            ),
        ),
        Property.Text(
            label="StartDate",
            configurable=True,
            description=(
                "Nur DateTime: Datum YYYY-MM-DD, oder komplett "
                "'YYYY-MM-DD HH:MM' / '11.10.26-06:00'"
            ),
        ),
        Property.Text(
            label="StartTime",
            configurable=True,
            description=(
                "Start time HH:MM / Startzeit (Today, Daily), e.g. 06:00. "
                "With Timing 'Ready by', the time the water must be ready / "
                "mit 'Ready by' die Fertig-Zeit"
            ),
        ),
        Property.Select(
            label="Timing",
            options=[TIMING_START, TIMING_READY],
            description=(
                "Start at: the next step starts at the time above. Ready by: "
                "the water must be at Ready_Temp by then, and the step works "
                "out when to start heating from the water's temperature, its "
                "volume and the element's power. / Start at: der nächste Step "
                "startet zur Zeit oben. Ready by: das Wasser soll dann "
                "Ready_Temp haben; der Heizbeginn wird berechnet."
            ),
        ),
        Property.Kettle(
            label="Heat_Kettle",
            description=(
                "Ready by: the kettle with the heating element (on a HERMS, "
                "the HLT). Its sensor gives its water's starting temperature. "
                "/ Kessel mit dem Heizstab (HERMS: HLT)"
            ),
        ),
        Property.Number(
            label="Ready_Temp",
            configurable=True,
            description=(
                "Ready by: the temperature the water must reach - on a HERMS "
                "the strike temperature / Zieltemperatur des Wassers"
            ),
        ),
        Property.Number(
            label="Volume",
            configurable=True,
            description=(
                "Ready by: the water in Heat_Kettle, in Volume_Unit / "
                "Wassermenge im Heat_Kettle"
            ),
        ),
        Property.Kettle(
            label="Strike_Kettle",
            description=(
                "Ready by, optional: the mash tun, when its strike water is "
                "heated by circulating it through a HERMS coil. Its sensor "
                "gives the strike water's starting temperature. / optional: "
                "Maischebottich, wenn das Einmaischwasser über die HERMS-"
                "Spirale geheizt wird"
            ),
        ),
        Property.Number(
            label="Strike_Volume",
            configurable=True,
            description=(
                "Ready by, optional: strike water in Strike_Kettle, in "
                "Volume_Unit. Adds the time to heat it too. / optional: "
                "Einmaischwasser"
            ),
        ),
        Property.Select(
            label="Volume_Unit",
            options=["gal", "L"],
            description=(
                "Ready by: unit of Volume. Blank follows the temperature unit "
                "(F: gal, C: L) / Einheit von Volume"
            ),
        ),
        Property.Number(
            label="Heater_Watts",
            configurable=True,
            description=(
                "Ready by: the element's power in watts - measured, volts x "
                "amps, if you can / Heizleistung in Watt"
            ),
        ),
        Property.Number(
            label="Efficiency_Pct",
            configurable=True,
            description=(
                "Ready by: how much of the element's power ends up in the "
                "water, in % (blank = 90, from a measured 20 gal HLT). Lower "
                "it if heating took longer than planned. / Wirkungsgrad in %"
            ),
        ),
        Property.Number(
            label="Margin_Minutes",
            configurable=True,
            description=(
                "Ready by: spare minutes added to the heat-up (blank = 10) / "
                "Reserve in Minuten"
            ),
        ),
        Property.Select(
            label="Campden_Reminder",
            options=["No", "Yes"],
            description=(
                "Remind me to treat the strike water with Campden, with the "
                "dose for Strike_Volume (or Volume): 1 tablet per 20 gal / "
                "75 L. / Erinnerung an Campden fürs Einmaischwasser"
            ),
        ),
        Property.Number(
            label="NotifyBefore",
            configurable=True,
            description="Minuten vorher benachrichtigen (0 = aus, leer = 15)",
        ),
        Property.Select(
            label="AutoNext",
            options=["Yes", "No"],
            description=(
                "Yes: continue automatically / automatisch weiter. "
                "No: wait for confirmation / auf Bestätigung warten."
            ),
        ),
        Property.Select(
            label="Language",
            options=available_languages(),
            description="Language for notifications / Sprache der Meldungen",
        ),
    ]
)
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

    async def NextStep(self, **kwargs):
        await self.next()

    # ----------------------------------------------------------- messages

    def _unit(self):
        return self.get_config_value("TEMP_UNIT", "C")

    def _scheduled_message(self):
        if self.plan is None:
            return t("notify_scheduled", self.language,
                     start=format_start(self.target_start))
        unit = self._unit()
        key = ("notify_ready_scheduled_strike" if self.plan.strike_minutes > 0
               else "notify_ready_scheduled")
        return t(key, self.language,
                 ready=format_start(self.ready_at),
                 start=format_start(self.target_start),
                 water=format_temp(self.plan.water_temp, unit),
                 target=format_temp(self.plan.target_temp, unit),
                 heat=round(self.plan.heat_minutes),
                 strike=round(self.plan.strike_minutes),
                 margin=round(self.plan.margin_minutes))

    def _due_message(self, now):
        """What to say when the time has already come as the step starts."""
        if self.plan is None:
            return t("notify_already_passed", self.language,
                     start=format_start(self.target_start))
        eta = now + timedelta(minutes=self.plan.heat_minutes)
        if eta <= self.ready_at:
            # Due only because of the spare minutes: still on time.
            return t("notify_ready_reached", self.language,
                     ready=format_start(self.ready_at))
        return t("notify_ready_late", self.language,
                 water=format_temp(self.plan.water_temp, self._unit()),
                 heat=round(self.plan.heat_minutes),
                 eta=format_start(eta), ready=format_start(self.ready_at))

    def _reached_message(self):
        if self.plan is not None:
            return t("notify_ready_reached", self.language,
                     ready=format_start(self.ready_at))
        return t("notify_reached_at", self.language,
                 start=format_start(self.target_start))

    def _offer_next(self, message, kind=NotificationType.INFO):
        self.cbpi.notify(
            self.name,
            t("notify_next_step", self.language, message=message),
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

    def _tell_no_reading(self):
        heat = self._kettle("Heat_Kettle")
        self.cbpi.notify(
            self.name,
            t("notify_ready_no_reading", self.language,
              kettle=getattr(heat, "name", "?"),
              assumed=format_temp(self.plan.water_temp, self._unit())),
            NotificationType.WARNING,
        )

    def _campden(self):
        """Remind, once, to treat the strike water - with the dose."""
        if (self.props.get("Campden_Reminder", None) or "No") != "Yes":
            return
        volume = self.props.get("Strike_Volume", None)
        if not volume_given(volume):
            volume = self.props.get("Volume", None)
        unit = self._unit()
        volume_unit = self.props.get("Volume_Unit", None)
        try:
            water_l = litres(volume, volume_unit, unit, self.language)
        except ValueError:
            return  # no volume to dose for
        self.cbpi.notify(
            self.name,
            t("notify_campden", self.language,
              tablets=format_tablets(campden_tablets(water_l)),
              volume=volume_label(volume, volume_unit, unit)),
            NotificationType.INFO,
        )

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
            strike_volume=self.props.get("Strike_Volume", None),
            strike_temp=self._water_temp(self._kettle("Strike_Kettle")),
            volume_unit=self.props.get("Volume_Unit", None),
            margin=self.props.get("Margin_Minutes", None),
            efficiency=self.props.get("Efficiency_Pct", None),
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
                        moved / 60, format_start(plan.start),
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
                       jump, format_start(self.target_start), remaining)
        self.cbpi.notify(
            self.name,
            t("notify_clock_corrected", self.language,
              start=format_start(self.target_start),
              countdown=format_countdown(remaining)),
            NotificationType.WARNING,
        )

    def _show(self, remaining):
        if self.plan is not None:
            water = ("?" if self.plan.assumed
                     else format_temp(self.plan.water_temp, self._unit()))
            self.summary = t("summary_ready_waiting", self.language,
                             ready=format_start(self.ready_at),
                             start=format_start(self.target_start),
                             water=water, countdown=format_countdown(remaining))
        else:
            self.summary = t("summary_waiting", self.language,
                             start=format_start(self.target_start),
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
            await self._arrive(self._reached_message())
            return self.auto_next
        self._show(remaining)
        self._prewarn(remaining)
        await self.push_update()
        return False

    def _read_props(self):
        mode = self.props.get("Mode", None) or "Today"
        start_time = self.props.get("StartTime", None)
        start_date = self.props.get("StartDate", None)
        start_time = "" if start_time is None else str(start_time).strip()
        start_date = "" if start_date is None else str(start_date).strip()
        self.language = normalise_language(self.props.get("Language", None))

        # Häufiger Fehler: StartTime leer gelassen
        if not start_time and mode != "DateTime":
            start_time = "06:00"
        if mode == "DateTime" and not start_date and start_time:
            # Erlaubt: kompletter Zeitstempel nur in StartTime
            start_date = start_time
            start_time = ""

        raw_notify = self.props.get("NotifyBefore", None)
        try:
            if raw_notify is None or raw_notify == "":
                notify_before = 15
            else:
                notify_before = int(float(raw_notify))
        except (TypeError, ValueError):
            notify_before = 15

        auto_next = (self.props.get("AutoNext", None) or "Yes") == "Yes"
        self.ready_by = (self.props.get("Timing", None) or TIMING_START) == TIMING_READY
        return mode, start_time, start_date, max(0, notify_before), auto_next

    async def on_start(self):
        self.timer = None
        self._handover = None
        self.arrived = False
        self.pre_notified = False
        self.plan = None
        self.ready_at = None
        self.target_start = None
        self._clock_seen = None
        self._planned_at = brewing_monotonic()
        mode, start_time, start_date, notify_before, self.auto_next = self._read_props()
        self.notify_before_seconds = notify_before * 60

        try:
            resolved = resolve_start(
                mode=mode,
                start_time=start_time,
                start_date=start_date,
                now=brewing_now(),
                language=self.language,
            )
            if self.ready_by:
                self.plan = self._plan(resolved)
                self.ready_at = resolved
                self.target_start = self.plan.start
            else:
                self.target_start = resolved
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

        self._campden()
        if self.plan is not None and self.plan.assumed:
            self._tell_no_reading()

        now = brewing_now()
        remaining = self.wall_clock_remaining(now)
        if remaining <= 0:
            await self._arrive(self._due_message(now))
            return

        self.cbpi.notify(self.name, self._scheduled_message(), NotificationType.INFO)
        self._show(remaining)
        await self.push_update()
        logger.info("BrewStart waiting %s seconds until %s",
                    remaining, format_start(self.target_start))

    async def on_stop(self):
        self.summary = ""
        await self.push_update()

    async def reset(self):
        self.summary = ""

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
                self._handover = asyncio.create_task(self.next())
            await asyncio.sleep(self._pause())
        return StepResult.DONE
