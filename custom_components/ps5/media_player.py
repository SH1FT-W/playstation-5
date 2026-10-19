"""Die PS5 als Medienplayer: an / Ruhemodus / aus, laufendes Spiel, Ein- und Ausschalten."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import STATE_ON, PS5ConfigEntry, PS5Coordinator
from .entity import PS5Entity
from .sensor import cloud_game

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: PS5ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([PS5MediaPlayer(entry.runtime_data)])


class PS5MediaPlayer(PS5Entity, MediaPlayerEntity):
    _attr_name = None  # Name des Geräts
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF
    _attr_media_content_type = MediaType.GAME

    def __init__(self, coordinator: PS5Coordinator) -> None:
        super().__init__(coordinator, None)

    @property
    def state(self) -> MediaPlayerState:
        state = self.coordinator.data.state
        if state == STATE_ON:
            return MediaPlayerState.PLAYING if self.media_title else MediaPlayerState.ON
        # STANDBY ist in HA abgekündigt: Ruhemodus = OFF (der Status-Sensor unterscheidet Ruhemodus/aus)
        return MediaPlayerState.OFF

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.coordinator.psn:
            self.async_on_remove(self.coordinator.psn.async_add_listener(self.async_write_ha_state))

    @property
    def media_title(self) -> str | None:
        return self.coordinator.data.app_name or cloud_game(self.coordinator).get("titleName")

    @property
    def app_name(self) -> str | None:
        return self.media_title

    @property
    def app_id(self) -> str | None:
        return self.coordinator.data.title_id or cloud_game(self.coordinator).get("npTitleId")

    @property
    def media_image_url(self) -> str | None:
        game = cloud_game(self.coordinator)
        return game.get("conceptIconUrl") or game.get("npTitleIconUrl")

    @property
    def media_image_remotely_accessible(self) -> bool:
        return True  # Sonys Bild-CDN, ohne Anmeldung abrufbar

    async def async_turn_on(self) -> None:
        await self.coordinator.async_turn_on()

    async def async_turn_off(self) -> None:
        await self.coordinator.async_turn_off()
