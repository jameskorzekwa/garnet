"""Explicit read, apply-and-save, and discard controls."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .const import MANAGERS
from .manager import SoulEntity


async def async_setup_entry(hass, entry, async_add_entities):
    manager = hass.data[MANAGERS][entry.entry_id]
    async_add_entities(
        SoulButton(manager, action) for action in ("read", "apply", "discard")
    )


class SoulButton(SoulEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, manager, action):
        super().__init__(manager, action)
        self.action = action
        self._attr_translation_key = action

    @property
    def available(self):
        manager = self.manager
        if manager.closed or manager.busy or not manager.is_soul:
            return False
        if self.action == "apply":
            return manager.writable and bool(manager.changes)
        return True

    async def async_press(self):
        if self.action == "discard":
            self.manager.discard()
        else:
            await getattr(self.manager, self.action)()
