"""Staged sender selectors. Selecting never performs Bluetooth I/O."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory

from .const import MANAGERS
from .manager import SoulEntity
from .soul_protocol import SENDER_NAMES

OPTIONS = ["disabled", "single", "stacked"]


async def async_setup_entry(hass, entry, async_add_entities):
    manager = hass.data[MANAGERS][entry.entry_id]
    async_add_entities(SoulSenderSelect(manager, i) for i in range(8))


class SoulSenderSelect(SoulEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, manager, index):
        super().__init__(manager, f"sender_{index}")
        self.index = index
        self._attr_name = f"{SENDER_NAMES[index]} staged sender"
        self._attr_translation_key = "sender_mode"
        self._attr_options = OPTIONS[:2] if index == 7 else OPTIONS.copy()

    @property
    def available(self):
        return self.manager.writable and not self.manager.busy

    @property
    def current_option(self):
        if self.manager.snapshot is None:
            return None
        state = self.manager.changes.get(
            self.index, self.manager.snapshot.sender_states[self.index]
        )
        return OPTIONS[state]

    async def async_select_option(self, option):
        self.manager.stage(self.index, OPTIONS.index(option))
