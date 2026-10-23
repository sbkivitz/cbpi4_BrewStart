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
            "System clock changed. Brew start stays at {start} "
            "({countdown} to go)."
        ),
        "action_next_step": "Next Step",
        "error_prefix": "Error: {error}",
        "err_ready_kettle": "Ready by needs a Kettle that has a sensor",
        "err_ready_temp": (
            "Ready by needs Ready_Temp - the temperature the water must reach"
        ),
        "err_ready_volume": "Ready by needs the water Volume (more than 0)",
        "err_ready_watts": (
            "Ready by needs Heater_Watts - the element's power (more than 0)"
        ),
        "summary_ready_waiting": (
            "Ready {ready} · heating from {start} · water {water} · "
            "{countdown} to go"
        ),
        "summary_ready_reached": "Heating start reached",
        "notify_ready_scheduled": (
            "Ready by {ready}: heating starts about {start}. {water} to "
            "{target} takes about {heat} min, plus {margin} min spare."
        ),
        "notify_ready_no_reading": (
            "No usable reading from {kettle} - planning as if the water were "
            "at {assumed}, so heating starts early."
        ),
        "notify_ready_late": (
            "Heating has to start now: from {water} it needs about {heat} min, "
            "so the water will be ready around {eta}, after {ready}."
        ),
        "notify_ready_reached": (
            "Time to heat - the water should be ready by {ready}."
        ),
        "notify_prewarn_ready": "Heating starts in {countdown}",
        "notify_ready_scheduled_strike": (
            "Ready by {ready}: heating starts about {start}. {water} to "
            "{target} takes about {heat} min, {strike} of them for the strike "
            "water, plus {margin} min spare."
        ),
        "notify_campden": (
            "Strike water: {tablets} Campden tablet(s) for {volume} (1 per "
            "20 gal / 75 L). It neutralizes chlorine and chloramine within "
            "minutes and takes up dissolved oxygen, so add it once the water "
            "is hot, just before doughing in."
        ),
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
            "Systemzeit geändert. Braustart bleibt {start} "
            "(noch {countdown})."
        ),
        "action_next_step": "Nächster Step",
        "error_prefix": "Fehler: {error}",
        "err_ready_kettle": "Ready by braucht einen Kessel (Kettle) mit Sensor",
        "err_ready_temp": (
            "Ready by braucht Ready_Temp - die Temperatur, die das Wasser "
            "erreichen soll"
        ),
        "err_ready_volume": "Ready by braucht die Wassermenge Volume (größer 0)",
        "err_ready_watts": (
            "Ready by braucht Heater_Watts - die Leistung des Heizstabs "
            "(größer 0)"
        ),
        "summary_ready_waiting": (
            "Fertig {ready} · Heizen ab {start} · Wasser {water} · "
            "noch {countdown}"
        ),
        "summary_ready_reached": "Heizbeginn erreicht",
        "notify_ready_scheduled": (
            "Fertig um {ready}: Heizbeginn ca. {start}. {water} auf {target} "
            "dauert ca. {heat} min, plus {margin} min Reserve."
        ),
        "notify_ready_no_reading": (
            "Kein gültiger Messwert von {kettle} - geplant, als hätte das "
            "Wasser {assumed}; der Heizbeginn ist daher früher."
        ),
        "notify_ready_late": (
            "Heizen muss jetzt beginnen: ab {water} dauert es ca. {heat} min, "
            "das Wasser ist also gegen {eta} fertig, nach {ready}."
        ),
        "notify_ready_reached": (
            "Heizbeginn erreicht - das Wasser soll um {ready} fertig sein."
        ),
        "notify_prewarn_ready": "Heizbeginn in {countdown}",
        "notify_ready_scheduled_strike": (
            "Fertig um {ready}: Heizbeginn ca. {start}. {water} auf {target} "
            "dauert ca. {heat} min, davon {strike} für das Einmaischwasser, "
            "plus {margin} min Reserve."
        ),
        "notify_campden": (
            "Einmaischwasser: {tablets} Campden-Tablette(n) für {volume} "
            "(1 pro 75 L / 20 gal). Sie neutralisiert Chlor und Chloramin in "
            "wenigen Minuten und bindet gelösten Sauerstoff - darum erst ins "
            "heiße Wasser, kurz vor dem Einmaischen."
        ),
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
