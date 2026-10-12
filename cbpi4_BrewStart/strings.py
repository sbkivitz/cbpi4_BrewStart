"""User-facing strings, keyed by language. No CraftBeerPi imports.

A dict rather than gettext, deliberately. gettext is the right answer for an
application: it handles plurals, it has a translator workflow, and it keeps
catalogues out of the source. It is the wrong answer for one plugin with
twenty strings, because it needs .po/.mo compilation and a build step, and
CraftBeerPi plugins are installed with pipx where shipping compiled locale
data is friction that buys nothing.

Switch to gettext when either becomes true: more than one module needs
strings, or somebody who is not the author is translating them.

Every value is a format string used with .format(**kwargs). Keys are stable
identifiers, never English text, so a missing translation is a visible
lookup failure rather than a silent fallback to one particular language.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"

# Display name -> code, so the step's Select property can show something a
# human recognises while the lookup stays on a short code.
LANGUAGE_CODES = {
    "English": "en",
    "Deutsch": "de",
    "en": "en",
    "de": "de",
}

MESSAGES = {
    "en": {
        "err_time_missing": "Start time is missing (format HH:MM)",
        "err_time_invalid": "Invalid start time '{value}' (expected HH:MM)",
        "err_datetime_missing": "Start date and time are missing",
        "err_datetime_invalid": (
            "Invalid start date/time '{value}' "
            "(expected e.g. 2026-10-11 06:00 or 11.10.2026 06:00)"
        ),
        "err_datetime_needs_both": (
            "DateTime mode needs a date (YYYY-MM-DD) and a time (HH:MM)"
        ),
        "err_unknown_mode": "Unknown mode '{mode}'",
        "summary_reached": "Start time reached",
        "summary_waiting": "Start {start} · {countdown} to go",
        "notify_reached": "Scheduled start time reached.",
        "notify_reached_at": "Scheduled start time reached ({start}).",
        "notify_next_step": "{message} Continue to the next step.",
        "notify_already_passed": "Start time has already passed ({start}).",
        "notify_scheduled": "Brew start scheduled for {start}",
        "notify_prewarn": "Brew start in {countdown}",
        "notify_clock_corrected": (
            "System clock changed. Brew start re-scheduled for {start} "
            "({countdown} to go)."
        ),
        "action_next_step": "Next Step",
        "error_prefix": "Error: {error}",
    },
    "de": {
        "err_time_missing": "Startzeit fehlt (Format: HH:MM)",
        "err_time_invalid": "Ungültige Startzeit '{value}' (erwartet HH:MM)",
        "err_datetime_missing": "Startzeitpunkt fehlt",
        "err_datetime_invalid": (
            "Ungültiger Startzeitpunkt '{value}' "
            "(erwartet z.B. 2026-10-11 06:00 oder 11.10.2026 06:00)"
        ),
        "err_datetime_needs_both": (
            "DateTime-Modus benötigt Datum (YYYY-MM-DD) und Zeit (HH:MM)"
        ),
        "err_unknown_mode": "Unbekannter Modus '{mode}'",
        "summary_reached": "Brauzeit erreicht",
        "summary_waiting": "Start {start} · noch {countdown}",
        "notify_reached": "Geplante Startzeit erreicht.",
        "notify_reached_at": "Geplante Startzeit erreicht ({start}).",
        "notify_next_step": "{message} Weiter zum nächsten Step.",
        "notify_already_passed": "Startzeit bereits erreicht ({start}).",
        "notify_scheduled": "Braustart geplant für {start}",
        "notify_prewarn": "Braustart in {countdown}",
        "notify_clock_corrected": (
            "Systemzeit geändert. Braustart neu geplant auf {start} "
            "(noch {countdown})."
        ),
        "action_next_step": "Nächster Step",
        "error_prefix": "Fehler: {error}",
    },
}


def normalise_language(value) -> str:
    """Map a property value to a supported language code."""
    if value is None:
        return DEFAULT_LANGUAGE
    code = LANGUAGE_CODES.get(str(value).strip())
    if code is None:
        code = str(value).strip().lower()[:2]
    return code if code in MESSAGES else DEFAULT_LANGUAGE


def t(key: str, language=DEFAULT_LANGUAGE, **kwargs) -> str:
    """Look up a string and format it.

    Falls back to the default language for a key a translation is missing,
    rather than returning the key or an empty string - a brewer seeing one
    English line among German ones is told something useful, which is more
    than `err_time_invalid` would tell them.

    Never raises. A formatting mistake in a rarely-hit error path must not be
    the thing that takes out a step on a brew morning, so a bad format falls
    back to the unformatted template.
    """
    lang = normalise_language(language)
    template = MESSAGES.get(lang, {}).get(key)
    if template is None:
        template = MESSAGES[DEFAULT_LANGUAGE].get(key)
    if template is None:
        return key
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template


def available_languages():
    """Display names for the step's Select property, stable order."""
    return [name for name, code in LANGUAGE_CODES.items()
            if name not in MESSAGES and code in MESSAGES]
