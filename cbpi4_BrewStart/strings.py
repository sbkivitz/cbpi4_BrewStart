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
        "err_time_missing": "The time is blank - set it",
        "err_time_invalid": "Invalid start time '{value}' (expected HH:MM)",
        "err_date_missing": "Day is Date, but no date is set",
        "err_date_invalid": "Invalid start date '{value}' (expected YYYY-MM-DD)",
        "err_datetime_missing": "Start date and time are missing",
        "err_datetime_invalid": (
            "Invalid start date/time '{value}' "
            "(expected e.g. 2026-10-11 06:00 or 11.10.2026 06:00)"
        ),
        "err_datetime_needs_both": "Day is Date: set the date and the time",
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
            "Ready by needs the Temperature the water must reach"
        ),
        "err_ready_volume": "Ready by needs the Water volume (more than 0)",
        "err_ready_watts": (
            "Ready by needs the Element power in watts (more than 0)"
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
            "Campden: {tablets} tablet(s) for {volume} of brewing water - 1 per "
            "20 gal / 75 L. Crush it and stir it in; it clears chlorine and "
            "chloramine within minutes, in cold water or hot."
        ),
        "notify_campden_no_volume": (
            "The Campden reminder is on, but the water volume is blank - fill "
            "it in to get the dose."
        ),
        "notify_passed_today": (
            "{start} has already passed, so nothing starts by itself. Press "
            "Next Step to go on now - or stop the brew, set Day to Tomorrow "
            "and start it again."
        ),
        "notify_passed": (
            "{start} has already passed, so nothing starts by itself. Press "
            "Next Step to go on now - or stop the brew, choose a later date "
            "or time and start it again."
        ),
        "summary_passed": "{start} has passed - waiting for Next Step",
        "notify_too_late": (
            "It is now {now}, more than {hours} hours after the {asked} you "
            "set, so heating does not start by itself. Press Next Step to "
            "start it now."
        ),
        "summary_too_late": "Long past {asked} - waiting for Next Step",
        "saved_problem": "Saved, but this step cannot start as set: {error}",
        "saved_passed_today": (
            "Saved, but {start} has already passed. For tomorrow, set Day to "
            "Tomorrow."
        ),
        "saved_passed": (
            "Saved, but {start} has already passed. Choose a later date or "
            "time."
        ),
        # The step form. The labels are the saved settings' names and cannot
        # change; name_* is what the form shows instead, prop_* the line under.
        "name_timing": "Timing",
        "name_mode": "Day",
        "name_start_date": "Date",
        "name_start_time": "Time",
        "name_heat_kettle": "Kettle",
        "name_ready_temp": "Temperature",
        "name_volume": "Water volume",
        "name_volume_unit": "Volume unit",
        "name_heater_watts": "Element power",
        "name_campden": "Campden reminder",
        "name_notify_before": "Heads-up",
        "name_auto_next": "Go on by itself",
        "prop_timing": (
            "Start at: the next step begins at the time. Ready by: the water is "
            "hot by then - heating starts as early as it needs to."
        ),
        "prop_mode": "Today and Tomorrow count from when you start the brew.",
        "prop_start_date": "",
        "prop_start_time": (
            "Start at: when the next step begins. Ready by: when the water must "
            "be hot."
        ),
        "prop_heat_kettle": (
            "The kettle with the element, holding all the strike and sparge "
            "water. Its probe gives the starting temperature."
        ),
        "prop_ready_temp": "How hot the water must be by the time.",
        "prop_volume": "All the water in the kettle. Also sets the Campden dose.",
        "prop_volume_unit": "Blank: gallons with °F, litres with °C.",
        "prop_heater_watts": "Volts × amps, measured if you can.",
        "prop_campden": (
            "Yes: saving this step shows the Campden dose for the water volume "
            "(1 tablet per 20 gal / 75 L)."
        ),
        "prop_notify_before": (
            "A notification this many minutes before the start - with Ready "
            "by, before heating starts. Blank: 15. 0: none."
        ),
        "prop_auto_next": (
            "Yes: the next step begins at the time. No: it waits for you to "
            "press Next Step."
        ),
        "prop_format_date": "Format: YYYY-MM-DD.",
        "prop_format_time": "Format: HH:MM, 24-hour.",
        "name_step_kind": "Step kind",
        "name_match_mode": "Match by",
        "name_match_value": "Match text",
        "actor_step_kind": (
            "Mash, Boil, Whirlpool: ready-made filters - Whirlpool also matches "
            "a step named 'Whirlpool'. Custom: choose Match by and Match text."
        ),
        "actor_match_mode": "How the match text is compared with the running step.",
        "actor_match_value": (
            "Text to look for; separate several with commas, e.g. "
            "'boil,whirlpool'. Blank with TypeContains: 'mash'."
        ),
        "setting_language": (
            "Language of BrewStart's settings and messages. The step form "
            "shows a change after a restart."
        ),
    },
    "de": {
        "err_time_missing": "Die Uhrzeit ist leer - bitte setzen",
        "err_time_invalid": "Ungültige Startzeit '{value}' (erwartet HH:MM)",
        "err_date_missing": "Tag ist Date, aber kein Datum gesetzt",
        "err_date_invalid": "Ungültiges Startdatum '{value}' (erwartet JJJJ-MM-TT)",
        "err_datetime_missing": "Startzeitpunkt fehlt",
        "err_datetime_invalid": (
            "Ungültiger Startzeitpunkt '{value}' "
            "(erwartet z.B. 2026-10-11 06:00 oder 11.10.2026 06:00)"
        ),
        "err_datetime_needs_both": "Tag ist Date: Datum und Uhrzeit setzen",
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
        "err_ready_kettle": "Ready by braucht einen Kessel mit Sensor",
        "err_ready_temp": (
            "Ready by braucht die Temperatur, die das Wasser erreichen soll"
        ),
        "err_ready_volume": "Ready by braucht die Wassermenge (größer 0)",
        "err_ready_watts": (
            "Ready by braucht die Heizleistung in Watt (größer 0)"
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
            "Campden: {tablets} Tablette(n) für {volume} Brauwasser - 1 pro "
            "75 L / 20 gal. Zerdrücken und einrühren; sie neutralisiert Chlor "
            "und Chloramin in wenigen Minuten, in kaltem wie in heißem Wasser."
        ),
        "notify_campden_no_volume": (
            "Die Campden-Erinnerung ist an, aber die Wassermenge ist leer - "
            "eintragen, dann kommt die Dosis."
        ),
        "notify_passed_today": (
            "{start} ist schon vorbei, darum startet nichts von selbst. Next "
            "Step drücken, um jetzt weiterzumachen - oder den Brauvorgang "
            "stoppen, Tag auf Tomorrow stellen und neu starten."
        ),
        "notify_passed": (
            "{start} ist schon vorbei, darum startet nichts von selbst. Next "
            "Step drücken, um jetzt weiterzumachen - oder den Brauvorgang "
            "stoppen, ein späteres Datum oder eine spätere Uhrzeit wählen und "
            "neu starten."
        ),
        "summary_passed": "{start} ist vorbei - wartet auf Next Step",
        "notify_too_late": (
            "Es ist {now}, mehr als {hours} Stunden nach dem eingestellten "
            "{asked}, darum beginnt das Heizen nicht von selbst. Next Step "
            "drücken, um jetzt zu beginnen."
        ),
        "summary_too_late": "{asked} ist lange vorbei - wartet auf Next Step",
        "saved_problem": "Gespeichert, aber so kann der Step nicht starten: {error}",
        "saved_passed_today": (
            "Gespeichert, aber {start} ist schon vorbei. Für morgen den Tag auf "
            "Tomorrow stellen."
        ),
        "saved_passed": (
            "Gespeichert, aber {start} ist schon vorbei. Ein späteres Datum "
            "oder eine spätere Uhrzeit wählen."
        ),
        "name_timing": "Zeitpunkt",
        "name_mode": "Tag",
        "name_start_date": "Datum",
        "name_start_time": "Uhrzeit",
        "name_heat_kettle": "Kessel",
        "name_ready_temp": "Temperatur",
        "name_volume": "Wassermenge",
        "name_volume_unit": "Mengeneinheit",
        "name_heater_watts": "Heizleistung",
        "name_campden": "Campden-Erinnerung",
        "name_notify_before": "Vorwarnung",
        "name_auto_next": "Selbst weiter",
        "prop_timing": (
            "Start at: der nächste Step beginnt zur Uhrzeit. Ready by: das "
            "Wasser ist dann heiß - geheizt wird so früh wie nötig."
        ),
        "prop_mode": (
            "Today (heute) und Tomorrow (morgen) zählen ab dem Start des "
            "Brauvorgangs; Date: ein gewähltes Datum."
        ),
        "prop_start_date": "",
        "prop_start_time": (
            "Start at: wann der nächste Step beginnt. Ready by: wann das "
            "Wasser heiß sein muss."
        ),
        "prop_heat_kettle": (
            "Der Kessel mit dem Heizstab und dem ganzen Einmaisch- und "
            "Nachgusswasser. Sein Fühler gibt die Starttemperatur."
        ),
        "prop_ready_temp": "Wie heiß das Wasser zur Uhrzeit sein muss.",
        "prop_volume": (
            "Das ganze Wasser im Kessel. Bestimmt auch die Campden-Dosis."
        ),
        "prop_volume_unit": "Leer: Gallonen bei °F, Liter bei °C.",
        "prop_heater_watts": "Volt × Ampere, gemessen wenn möglich.",
        "prop_campden": (
            "Yes: beim Speichern dieses Steps erscheint die Campden-Dosis für "
            "die Wassermenge (1 Tablette pro 75 L / 20 gal)."
        ),
        "prop_notify_before": (
            "Eine Meldung so viele Minuten vor dem Start - bei Ready by vor "
            "dem Heizbeginn. Leer: 15. 0: keine."
        ),
        "prop_auto_next": (
            "Yes: der nächste Step beginnt zur Uhrzeit. No: er wartet auf Next "
            "Step."
        ),
        "prop_format_date": "Format: JJJJ-MM-TT.",
        "prop_format_time": "Format: HH:MM, 24 Stunden.",
        "name_step_kind": "Step-Art",
        "name_match_mode": "Vergleich",
        "name_match_value": "Suchtext",
        "actor_step_kind": (
            "Mash, Boil, Whirlpool: fertige Filter - Whirlpool erkennt auch "
            "einen Step namens 'Whirlpool'. Custom: Vergleich und Suchtext "
            "wählen."
        ),
        "actor_match_mode": "Wie der Suchtext mit dem laufenden Step verglichen wird.",
        "actor_match_value": (
            "Suchtext; mehrere mit Komma trennen, z.B. 'boil,whirlpool'. Leer "
            "bei TypeContains: 'mash'."
        ),
        "setting_language": (
            "Sprache der Einstellungen und Meldungen von BrewStart. Das "
            "Step-Formular zeigt eine Änderung nach einem Neustart."
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
