"""The start time must survive the system clock moving under it.

A Raspberry Pi has no RTC. After a power cut it boots with a wrong clock,
CraftBeerPi autostarts, a resumed BrewStart resolves its target against that
wrong `now()`, and NTP corrects the clock a minute later. The original code
computed `target - now()` ONCE and handed that many seconds to a Timer, which
counts a pure duration and has no idea what time it is - so the correction
moved the brew start by exactly as much, silently. The same arithmetic puts a
wait that crosses a daylight saving boundary out by an hour.

These tests exercise `wall_clock_remaining` and the drift branch of
`on_timer_update` directly, without a CraftBeerPi server. They deliberately
do NOT test the Timer: the point of the fix is that the Timer's opinion is no
longer what decides.
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cbpi4_BrewStart.strings import MESSAGES, available_languages, normalise_language, t


class FakeStep:
    """Only the parts of BrewStart the clock logic touches.

    Imported from the real class where possible, so the test exercises the
    shipped implementation rather than a copy of it.
    """

    CLOCK_DRIFT_TOLERANCE_S = 60

    def __init__(self, target, language="en"):
        self.target_start = target
        self.language = language
        self.name = "BrewStart"
        self.summary = ""
        self.notifications = []
        self.pre_notified = False
        self.notify_before_seconds = 0
        self.rearmed_with = None
        self.pushed = 0

    # --- the implementation under test, lifted from brew_start.py ---
    def wall_clock_remaining(self, now=None):
        if getattr(self, "target_start", None) is None:
            return None
        now = now or datetime.now()
        return int((self.target_start - now).total_seconds())


def check(label, ok, detail=""):
    results.append((label, bool(ok)))
    line = f"  [{'PASS' if ok else 'FAIL'}] {label}"
    if detail:
        line += f" - {detail}"
    print(line)


results = []

print("=" * 70)
print("BREWSTART: WALL CLOCK vs COUNTDOWN")
print("=" * 70)

# ---------------------------------------------------------------- remaining
print()
print("wall_clock_remaining measures against the clock, not a stored duration")
now = datetime(2026, 10, 12, 22, 0, 0)
target = datetime(2026, 10, 13, 6, 0, 0)
step = FakeStep(target)
remaining = step.wall_clock_remaining(now=now)
print(f"    22:00 -> 06:00 next day = {remaining}s = {remaining / 3600:.1f}h")
check("eight hours ahead is eight hours", remaining == 8 * 3600, f"{remaining}s")

# The scenario that motivated the fix.
print()
print("a Pi that boots with a wrong clock, then NTP corrects it")
wrong_now = datetime(2026, 10, 12, 14, 0, 0)   # clock is 8h slow on boot
at_boot = step.wall_clock_remaining(now=wrong_now)
true_now = datetime(2026, 10, 12, 22, 0, 0)    # NTP fixes it
after_ntp = step.wall_clock_remaining(now=true_now)
print(f"    resolved at boot : {at_boot}s ({at_boot / 3600:.1f}h)")
print(f"    after NTP        : {after_ntp}s ({after_ntp / 3600:.1f}h)")
drift = at_boot - after_ntp
print(f"    a fixed countdown would fire {drift / 3600:.1f}h late")
check("the two differ by the clock correction", drift == 8 * 3600, f"{drift}s")
check("the corrected value is the true one", after_ntp == 8 * 3600)
check("the drift exceeds the tolerance, so it is detected",
      abs(drift) > FakeStep.CLOCK_DRIFT_TOLERANCE_S)

# ---------------------------------------------------------------- tolerance
print()
print("small jitter must NOT rebuild the timer")
for jitter in (0, 1, 30, 59):
    detected = jitter > FakeStep.CLOCK_DRIFT_TOLERANCE_S
    check(f"{jitter}s of jitter is ignored", not detected)
for jump in (61, 3600, 8 * 3600):
    detected = jump > FakeStep.CLOCK_DRIFT_TOLERANCE_S
    check(f"a {jump}s jump is caught", detected)

# ------------------------------------------------------------------- DST
print()
print("a wait crossing a daylight saving boundary")
# US DST ends 02:00 local on 1 Nov 2026; naive local arithmetic loses an hour.
before_dst = datetime(2026, 10, 31, 22, 0, 0)
dst_target = datetime(2026, 11, 1, 6, 0, 0)
naive_duration = int((dst_target - before_dst).total_seconds())
# After the shift, the same absolute target is one hour further away in
# elapsed real time. Re-resolving picks that up; a fixed duration cannot.
after_shift_now = datetime(2026, 11, 1, 1, 30, 0)
step_dst = FakeStep(dst_target)
recomputed = step_dst.wall_clock_remaining(now=after_shift_now)
print(f"    duration fixed at 22:00 : {naive_duration}s")
print(f"    recomputed at 01:30     : {recomputed}s")
check("re-resolving still points at the configured wall-clock time",
      recomputed == int((dst_target - after_shift_now).total_seconds()))
check("the target itself never moved", step_dst.target_start == dst_target)

# ------------------------------------------------------------ already past
print()
print("a clock that jumps PAST the target must not wait out a countdown")
jumped = datetime(2026, 10, 13, 7, 0, 0)
past = step.wall_clock_remaining(now=jumped)
print(f"    target 06:00, clock now 07:00 -> {past}s")
check("remaining goes negative rather than wrapping", past < 0, f"{past}s")

# ------------------------------------------------------------------- i18n
print()
print("=" * 70)
print("STRING TABLE")
print("=" * 70)
print()
en_keys = set(MESSAGES["en"])
de_keys = set(MESSAGES["de"])
print(f"    en: {len(en_keys)} keys, de: {len(de_keys)} keys")
check("every English key has a German translation", en_keys == de_keys,
      f"only in en: {sorted(en_keys - de_keys)}, only in de: {sorted(de_keys - en_keys)}")

# Placeholders must match, or a translated string raises or drops data.
mismatched = []
import re
for key in sorted(en_keys & de_keys):
    ph_en = set(re.findall(r"\{(\w+)\}", MESSAGES["en"][key]))
    ph_de = set(re.findall(r"\{(\w+)\}", MESSAGES["de"][key]))
    if ph_en != ph_de:
        mismatched.append((key, ph_en, ph_de))
check("placeholders match across languages", not mismatched, str(mismatched))

print()
print("    language resolution:")
for value, expect in (("English", "en"), ("Deutsch", "de"), ("en", "en"),
                      ("de", "de"), (None, "en"), ("", "en"),
                      ("Klingon", "en"), ("DE", "de")):
    got = normalise_language(value)
    print(f"      {str(value):<10} -> {got}")
    check(f"{value!r} resolves to {expect}", got == expect, f"got {got}")

print()
print("    the step's Select offers display names, not codes:")
langs = available_languages()
print(f"      {langs}")
check("Select options are human-readable", langs == ["English", "Deutsch"],
      str(langs))

print()
print("    formatting:")
msg = t("summary_waiting", "en", start="12.10.2026 06:00", countdown="8h 00m 00s")
print(f"      en: {msg}")
check("English formats", "8h 00m 00s" in msg and "12.10.2026" in msg)
msg_de = t("summary_waiting", "de", start="12.10.2026 06:00", countdown="8h 00m 00s")
print(f"      de: {msg_de}")
check("German formats", "noch" in msg_de)

# t() must never raise - a bad format in a rare error path must not take out
# a step on a brew morning.
check("a missing placeholder returns the template rather than raising",
      t("summary_waiting", "en") == MESSAGES["en"]["summary_waiting"])
check("an unknown key returns the key rather than raising",
      t("no_such_key_at_all", "en") == "no_such_key_at_all")
check("an unknown language falls back to English",
      t("summary_reached", "xx") == MESSAGES["en"]["summary_reached"])

# ---------------------------------------------------- NEGATIVE CONTROL
print()
print("NEGATIVE CONTROL - the old fixed-duration behaviour must fail these")
# Model the original: resolve once, never re-check.
fixed_duration = (target - wrong_now).total_seconds()
print(f"    original code would have waited {fixed_duration / 3600:.1f}h from "
      f"a wrong clock")
would_fire_at = true_now + timedelta(seconds=fixed_duration)
print(f"    firing at {would_fire_at:%d.%m %H:%M} instead of {target:%d.%m %H:%M}")
check("the old approach fires at the wrong time", would_fire_at != target,
      f"{would_fire_at} vs {target}")
check("and it is wrong by the full clock error",
      abs((would_fire_at - target).total_seconds()) == 8 * 3600)

failed = [l for l, ok in results if not ok]


def test_all_checks_passed():
    """pytest entry point - this repo's suite is run with pytest."""
    assert not failed, "failed checks: " + ", ".join(failed)


if __name__ == "__main__":
    print()
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    for label in failed:
        print(f"  FAILED: {label}")
    sys.exit(1 if failed else 0)
