import asyncio
import logging

from cbpi.api import *
from cbpi.api.step import StepState

from .step_match import is_matching_step_active
from .strings import DEFAULT_LANGUAGE, t

logger = logging.getLogger(__name__)

STEP_KINDS = [
    "Mash",
    "Boil",
    "Whirlpool",
    "Mash or Boil",
    "Mash+Boil+Whirlpool",
    "AnyActive",
    "Custom",
]

MATCH_MODES = [
    "TypeContains",
    "TypeOrNameContains",
    "AnyActive",
    "TypeExact",
    "NameContains",
    "NameExact",
]


def status_actor_properties(language=DEFAULT_LANGUAGE):
    """StepStatusActor's form, in one language and in words. MatchMode and
    MatchValue only apply to StepKind Custom, and are shown only then (a server
    or interface from before show_if shows them always, and the saved names
    instead of display_label)."""
    custom_only = {"StepKind": "Custom"}
    step_kind = Property.Select(label="StepKind", options=list(STEP_KINDS),
                                description=t("actor_step_kind", language))
    step_kind.display_label = t("name_step_kind", language)
    match_mode = Property.Select(label="MatchMode", options=list(MATCH_MODES),
                                 description=t("actor_match_mode", language))
    match_mode.display_label = t("name_match_mode", language)
    match_mode.show_if = custom_only
    match_value = Property.Text(label="MatchValue", configurable=True,
                                description=t("actor_match_value", language))
    match_value.display_label = t("name_match_value", language)
    match_value.show_if = custom_only
    return [step_kind, match_mode, match_value]


class _StepStatusActorBase(CBPiActor):
    """Shared logic: virtual actor ON while a matching brew step is active."""

    # Subclasses may hardcode a preset; StepStatusActor reads it from props.
    fixed_step_kind = None

    async def on_start(self):
        self.state = False
        self.power = 100
        if self.fixed_step_kind:
            self.step_kind = self.fixed_step_kind
            self.match_mode = "TypeContains"
            self.match_value = ""
        else:
            self.step_kind = self.props.get("StepKind", None) or "Mash"
            self.match_mode = self.props.get("MatchMode", None) or "TypeContains"
            self.match_value = str(self.props.get("MatchValue", "") or "")
        logger.info(
            "%s %s started (kind=%s mode=%s value=%s)",
            self.__class__.__name__,
            self.id,
            self.step_kind,
            self.match_mode,
            self.match_value,
        )

    async def on(self, power=None):
        if power is not None:
            self.power = power
        logger.debug("%s %s: manual ON ignored", self.__class__.__name__, self.id)

    async def off(self):
        logger.debug("%s %s: manual OFF ignored", self.__class__.__name__, self.id)

    def get_state(self):
        return self.state

    async def set_power(self, power):
        self.power = power

    async def run(self):
        while self.running is True:
            try:
                active = is_matching_step_active(
                    getattr(self.cbpi, "step", None),
                    self.match_mode,
                    self.match_value,
                    step_kind=self.step_kind,
                    active_statuses=(StepState.ACTIVE, "A"),
                )
            except Exception as exc:
                logger.error("%s status check failed: %s", self.__class__.__name__, exc)
                active = False

            if active != self.state:
                self.state = active
                try:
                    await self.cbpi.actor.actor_update(
                        self.id, self.power if self.state else 0
                    )
                except Exception:
                    try:
                        await self.cbpi.actor.ws_actor_update()
                    except Exception:
                        pass
                logger.info(
                    "%s %s -> %s",
                    self.__class__.__name__,
                    self.id,
                    "ON" if self.state else "OFF",
                )

            await asyncio.sleep(1)


@parameters(status_actor_properties())
class StepStatusActor(_StepStatusActorBase):
    """A virtual actor that is ON during Mash / Boil / Whirlpool (or a Custom filter)."""

    fixed_step_kind = None


@parameters([])
class MashStatusActor(_StepStatusActorBase):
    """ON during a MashIn or Mash step (by type or name)."""

    fixed_step_kind = "Mash"


@parameters([])
class BoilStatusActor(_StepStatusActorBase):
    """ON during a BoilStep / BM_BoilStep (by type or name)."""

    fixed_step_kind = "Boil"


@parameters([])
class WhirlpoolStatusActor(_StepStatusActorBase):
    """ON during Whirlpool - including Wait/Notification steps named so."""

    fixed_step_kind = "Whirlpool"
