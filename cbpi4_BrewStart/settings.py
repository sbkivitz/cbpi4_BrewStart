"""The plugin's one server setting: the language of its form and messages."""

import asyncio
import logging

from cbpi.api import CBPiExtension
from cbpi.api.config import ConfigType

try:
    from .brew_start import LANGUAGE_SETTING
    from .strings import DEFAULT_LANGUAGE, t
except ImportError:  # pragma: no cover - direct import in tests
    from cbpi4_BrewStart.brew_start import LANGUAGE_SETTING
    from cbpi4_BrewStart.strings import DEFAULT_LANGUAGE, t

logger = logging.getLogger(__name__)

LANGUAGE_OPTIONS = [
    {"label": "English", "value": "English"},
    {"label": "Deutsch", "value": "Deutsch"},
]


class BrewStartSettings(CBPiExtension):
    """Adds BREWSTART_LANGUAGE to the server's settings, English, when absent.

    Settings are added from a task because adding one is a coroutine and a
    plugin's setup() is not - the way cbpi4-simvessel adds SIM_TIME_SCALE.
    """

    def __init__(self, cbpi):
        self.cbpi = cbpi
        self._task = asyncio.create_task(self.register_setting())

    async def register_setting(self):
        # Only when absent: adding it on every start would put a brewer's
        # choice back to English each time the server came up.
        if self.cbpi.config.get(LANGUAGE_SETTING, None) is not None:
            return
        try:
            await self.cbpi.config.add(
                LANGUAGE_SETTING,
                "English",
                type=ConfigType.SELECT,
                description=t("setting_language", DEFAULT_LANGUAGE),
                source="cbpi4_BrewStart",
                options=LANGUAGE_OPTIONS,
            )
            logger.info("Added the %s setting, English", LANGUAGE_SETTING)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not add the %s setting: %s", LANGUAGE_SETTING, exc)
