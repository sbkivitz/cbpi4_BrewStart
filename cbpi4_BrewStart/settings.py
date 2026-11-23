"""The plugin's server settings: the language of its form and messages, and
the efficiency it plans the heating with."""

import asyncio
import logging

from cbpi.api import CBPiExtension
from cbpi.api.config import ConfigType

try:
    from .brew_start import EFFICIENCY_SETTING, LANGUAGE_SETTING, configured_language
    from .strings import DEFAULT_LANGUAGE, t
except ImportError:  # pragma: no cover - direct import in tests
    from cbpi4_BrewStart.brew_start import EFFICIENCY_SETTING, LANGUAGE_SETTING, configured_language
    from cbpi4_BrewStart.strings import DEFAULT_LANGUAGE, t

logger = logging.getLogger(__name__)

LANGUAGE_OPTIONS = [
    {"label": "English", "value": "English"},
    {"label": "Deutsch", "value": "Deutsch"},
]


class BrewStartSettings(CBPiExtension):
    """Adds BREWSTART_LANGUAGE to the server's settings, English, when absent,
    and BREWSTART_EFFICIENCY, blank.

    Settings are added from a task because adding one is a coroutine and a
    plugin's setup() is not - the way cbpi4-simvessel adds SIM_TIME_SCALE.
    """

    def __init__(self, cbpi):
        self.cbpi = cbpi
        self._task = asyncio.create_task(self.register_settings())

    async def register_settings(self):
        await self.register_setting()
        await self.register_efficiency()

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

    async def register_efficiency(self):
        """BREWSTART_EFFICIENCY, the share of the HLT element's power that heats
        the water, in %: blank is the built-in 90 %, and USE on the Figures page
        sets it from past brews. A saved value is kept, as the core keeps
        GRAIN_ABSORPTION's; the description is written again only when it has
        changed - a new version's words, or another language."""
        description = t("setting_efficiency", configured_language(self.cbpi))
        cache = getattr(self.cbpi.config, "cache", None)
        known = cache.get(EFFICIENCY_SETTING) if isinstance(cache, dict) else None
        if known is not None and getattr(known, "description", None) == description \
                and getattr(known, "type", None) == ConfigType.NUMBER:
            return
        saved = self.cbpi.config.get(EFFICIENCY_SETTING, None)
        try:
            await self.cbpi.config.add(
                EFFICIENCY_SETTING,
                "" if saved is None else saved,
                type=ConfigType.NUMBER,
                description=description,
                source="cbpi4_BrewStart",
            )
            logger.info("Registered the %s setting (%s)", EFFICIENCY_SETTING,
                        "blank: the built-in 90 %" if saved is None else "kept: {}".format(saved))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not add the %s setting: %s", EFFICIENCY_SETTING, exc)
