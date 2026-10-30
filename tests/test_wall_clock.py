"""BrewStart's string table: every message in both languages, and t() never raises.

This file also held the wall-clock checks until 30 Oct. They ran a copy of
wall_clock_remaining written into the test, and compared it with the same
subtraction; the jitter check asked whether 30 is more than 60 (test-bench
audit, 29 Oct, finding 7). They are in test_step_clock.py now, on the real
step's on_start() and _tick(): a night across the end of daylight saving, a
Ready by across it, a clock moved by under and over a minute, a clock stepped
past the start, and a control that follows the clock back.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cbpi4_BrewStart.strings import MESSAGES, available_languages, normalise_language, t


def check(label, ok, detail=""):
    results.append((label, bool(ok)))
    line = f"  [{'PASS' if ok else 'FAIL'}] {label}"
    if detail:
        line += f" - {detail}"
    print(line)


results = []

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
