"""CraftBeerPi4 plugin: scheduled brew start + step status actors."""

import logging

logger = logging.getLogger(__name__)

try:
    from .schedule import format_countdown, format_start, resolve_start
except Exception:  # pragma: no cover
    format_countdown = format_start = resolve_start = None  # type: ignore

__all__ = [
    "format_countdown",
    "format_start",
    "resolve_start",
    "setup",
]


def setup(cbpi):
    """Register plugin types with the CraftBeerPi4 server.

    The forms' descriptions are built in the language chosen in the server's
    settings (BREWSTART_LANGUAGE, English when unset), and the temperature's
    unit from TEMP_UNIT - read here, once, so a change shows in the forms after
    a restart. Messages read them as they go.
    """
    try:
        from .brew_start import BrewStart, configured_language, step_properties

        language = configured_language(cbpi)
        try:
            temp_unit = cbpi.config.get("TEMP_UNIT", "C")
        except Exception:  # noqa: BLE001
            temp_unit = "C"
        BrewStart.cbpi_parameters = step_properties(language, temp_unit)
        cbpi.plugin.register("BrewStart", BrewStart)
        logger.info("Registered BrewStart step (%s)", language)
    except Exception as exc:
        logger.exception("FAILED to register BrewStart: %s", exc)
        raise

    try:
        from .settings import BrewStartSettings

        cbpi.plugin.register("BrewStartSettings", BrewStartSettings)
    except Exception as exc:  # noqa: BLE001 - the step works without it
        logger.exception("Could not add BrewStart's language setting: %s", exc)

    try:
        from .step_status_actor import (
            BoilStatusActor,
            MashStatusActor,
            StepStatusActor,
            WhirlpoolStatusActor,
            status_actor_properties,
        )

        StepStatusActor.cbpi_parameters = status_actor_properties(language)
        cbpi.plugin.register("StepStatusActor", StepStatusActor)
        cbpi.plugin.register("MashStatusActor", MashStatusActor)
        cbpi.plugin.register("BoilStatusActor", BoilStatusActor)
        cbpi.plugin.register("WhirlpoolStatusActor", WhirlpoolStatusActor)
        logger.info("Registered StepStatusActor / Mash / Boil / Whirlpool")
    except Exception as exc:
        logger.exception("FAILED to register StepStatus actors: %s", exc)
