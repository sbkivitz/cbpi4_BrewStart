import asyncio
import logging
from datetime import datetime

from cbpi.api import *
from cbpi.api.dataclasses import NotificationAction, NotificationType
from cbpi.api.step import CBPiStep, StepResult
from cbpi.api.timer import Timer

try:
    from .schedule import format_countdown, format_start, resolve_start
    from .strings import available_languages, normalise_language, t
except ImportError:
    from cbpi4_BrewStart.schedule import format_countdown, format_start, resolve_start
    from cbpi4_BrewStart.strings import available_languages, normalise_language, t

logger = logging.getLogger(__name__)


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
            description="Start time HH:MM / Startzeit (Today, Daily), e.g. 06:00",
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
    """Wait until a configured start time, re-checking the wall clock as it goes.

    The countdown is a DISPLAY. The decision to fire is taken against the wall
    clock, every tick, because the two are not the same thing.

    The original computed `target - datetime.now()` once in on_start() and
    handed that many seconds to a Timer. Timer counts a pure duration and has
    no idea what time it is, so anything that moved the system clock after the
    step started moved the firing time by exactly as much, silently.

    That is not hypothetical on this hardware. A Raspberry Pi has no RTC: after
    a power cut it boots with a wrong clock, CraftBeerPi autostarts, the step
    resolves against that wrong `now()`, and NTP corrects the clock a minute
    later. The countdown keeps running on the stale duration and the brew
    starts hours off. The same arithmetic puts a wait that crosses a daylight
    saving boundary out by an hour.

    Re-resolving each tick makes both self-healing: the target is an absolute
    local datetime, so after any clock correction the remaining time is simply
    recomputed from it.
    """

    #: How far the wall clock may disagree with the countdown before the timer
    #: is rebuilt. Loose enough to ignore scheduling jitter and the rounding in
    #: Timer's own one second tick, tight enough that an NTP step correction or
    #: a daylight saving shift is caught on the next update.
    CLOCK_DRIFT_TOLERANCE_S = 60

    async def NextStep(self, **kwargs):
        await self.next()

    async def on_timer_done(self, timer):
        self.summary = t("summary_reached", self.language)
        await self.push_update()

        if getattr(self, "target_start", None) is not None:
            message = t("notify_reached_at", self.language,
                        start=format_start(self.target_start))
        else:
            message = t("notify_reached", self.language)

        if self.auto_next:
            self.cbpi.notify(self.name, message, NotificationType.SUCCESS)
            await self.next()
        else:
            self.cbpi.notify(
                self.name,
                t("notify_next_step", self.language, message=message),
                NotificationType.INFO,
                action=[NotificationAction(
                    t("action_next_step", self.language), self.NextStep)],
            )

    def wall_clock_remaining(self, now=None):
        """Seconds until the target, measured against the wall clock.

        None when there is no target to measure against.
        """
        if getattr(self, "target_start", None) is None:
            return None
        now = now or datetime.now()
        return int((self.target_start - now).total_seconds())

    async def on_timer_update(self, timer, seconds):
        try:
            secs = int(seconds)
        except (TypeError, ValueError):
            secs = 0

        # The countdown is a display. The wall clock decides.
        actual = self.wall_clock_remaining()
        if actual is not None:
            drift = abs(actual - secs)
            if drift > self.CLOCK_DRIFT_TOLERANCE_S:
                # The system clock moved under us - NTP correcting a Pi that
                # booted without an RTC, a daylight saving shift, or somebody
                # setting the date. Rebuild the countdown from the target,
                # which is an absolute datetime and therefore still correct.
                logger.warning(
                    "BrewStart: clock moved, countdown said %ss but %s is %ss "
                    "away - rescheduling", secs, format_start(self.target_start),
                    actual,
                )
                self.cbpi.notify(
                    self.name,
                    t("notify_clock_corrected", self.language,
                      start=format_start(self.target_start),
                      countdown=format_countdown(max(0, actual))),
                    NotificationType.WARNING,
                )
                await self._rearm(actual)
                return
            secs = max(0, actual)

        if getattr(self, "target_start", None) is not None:
            self.summary = t(
                "summary_waiting", self.language,
                start=format_start(self.target_start),
                countdown=format_countdown(secs),
            )
        else:
            self.summary = Timer.format_time(secs)

        # Vorwarnung / pre-warning
        if (
            getattr(self, "notify_before_seconds", 0) > 0
            and not getattr(self, "pre_notified", False)
            and 0 < secs <= self.notify_before_seconds
        ):
            self.pre_notified = True
            self.cbpi.notify(
                self.name,
                t("notify_prewarn", self.language,
                  countdown=format_countdown(secs)),
                NotificationType.WARNING,
            )

        await self.push_update()

    async def _rearm(self, remaining):
        """Replace the countdown with one that matches the wall clock.

        Fires immediately when the corrected clock says the moment has already
        passed - a clock that jumps forwards past the target must not leave the
        step waiting out a countdown for a time that is behind it.
        """
        old = getattr(self, "timer", None)
        if old is not None:
            try:
                await old.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("BrewStart rearm stop: %s", exc)
        if remaining <= 0:
            self.timer = Timer(1, on_update=self.on_timer_update,
                               on_done=self.on_timer_done)
            await self.on_timer_done(self.timer)
            return
        self.timer = Timer(remaining, on_update=self.on_timer_update,
                           on_done=self.on_timer_done)
        self.timer.start()

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
        return mode, start_time, start_date, max(0, notify_before), auto_next

    async def on_start(self):
        self.pre_notified = False
        mode, start_time, start_date, notify_before, self.auto_next = self._read_props()
        self.notify_before_seconds = notify_before * 60

        try:
            self.target_start = resolve_start(
                mode=mode,
                start_time=start_time,
                start_date=start_date,
                language=self.language,
            )
        except ValueError as exc:
            logger.error("BrewStart config error: %s", exc)
            self.summary = t("error_prefix", self.language, error=exc)
            await self.push_update()
            self.cbpi.notify(self.name, str(exc), NotificationType.ERROR)
            # Kurzer Timer, dann stehen bleiben (kein Auto-Next bei Fehler)
            self.auto_next = False
            self.target_start = None
            self.timer = Timer(1, on_update=self.on_timer_update, on_done=self.on_timer_done)
            return

        seconds = int((self.target_start - datetime.now()).total_seconds())
        if seconds <= 0:
            self.summary = t("summary_reached", self.language)
            await self.push_update()
            self.cbpi.notify(
                self.name,
                t("notify_already_passed", self.language,
                  start=format_start(self.target_start)),
                NotificationType.SUCCESS,
            )
            if self.auto_next:
                await self.next()
            else:
                self.cbpi.notify(
                    self.name,
                    t("notify_next_step", self.language, message="").strip(),
                    NotificationType.INFO,
                    action=[NotificationAction(
                        t("action_next_step", self.language), self.NextStep)],
                )
            self.timer = Timer(1, on_update=self.on_timer_update, on_done=self.on_timer_done)
            return

        self.summary = t(
            "summary_waiting", self.language,
            start=format_start(self.target_start),
            countdown=format_countdown(seconds),
        )
        self.cbpi.notify(
            self.name,
            t("notify_scheduled", self.language,
              start=format_start(self.target_start)),
            NotificationType.INFO,
        )
        await self.push_update()

        # Bewährtes CBPi-Muster (wie DatetimeWaitStep): ein Timer über die gesamte Wartezeit
        self.timer = Timer(
            seconds, on_update=self.on_timer_update, on_done=self.on_timer_done
        )
        self.timer.start()
        logger.info(
            "BrewStart waiting %s seconds until %s",
            seconds,
            format_start(self.target_start),
        )

    async def on_stop(self):
        if getattr(self, "timer", None) is not None:
            try:
                await self.timer.stop()
            except Exception as exc:
                logger.warning("BrewStart timer stop: %s", exc)
        self.summary = ""
        await self.push_update()

    async def reset(self):
        mode, start_time, start_date, notify_before, self.auto_next = self._read_props()
        self.notify_before_seconds = notify_before * 60
        self.pre_notified = False
        try:
            self.target_start = resolve_start(
                mode=mode, start_time=start_time, start_date=start_date,
                language=self.language,
            )
            seconds = max(
                1, int((self.target_start - datetime.now()).total_seconds())
            )
        except ValueError:
            seconds = 1
            self.target_start = None
        self.timer = Timer(
            seconds, on_update=self.on_timer_update, on_done=self.on_timer_done
        )

    async def run(self):
        while self.running is True:
            await asyncio.sleep(1)
        return StepResult.DONE
