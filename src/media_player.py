"""Defines media player entity for youtube lounge integration."""

from __future__ import annotations

import asyncio
import datetime as dt
import math
from asyncio import Task
from typing import Any, Awaitable, Callable, Mapping, TypedDict

import homeassistant
import homeassistant.util
import voluptuous as vol
from aiogoogle import Aiogoogle
from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    async_get_current_platform,
)
from pyytlounge import (
    EventListener,
    PlaybackStateEvent,
    YtLoungeApi,
    get_thumbnail_url,
)
from pyytlounge import (
    State as YtState,
)
from pyytlounge.events import NowPlayingEvent

from .const import (
    ATTR_LANGUAGE_CODE,
    DOMAIN,
    LOGGER,
    SERVICE_RECONNECT,
    SERVICE_SELECT_SUBTITLE_TRACK,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the media player entity for the given configuration."""
    api: YtLoungeApi = hass.data[DOMAIN][entry.entry_id]

    api_key: str | None = entry.data.get("google_api_key")
    async_add_entities([YtMediaPlayer(entry, api, api_key)])

    platform = async_get_current_platform()

    platform.async_register_entity_service(SERVICE_RECONNECT, {}, "manual_reconnect")
    platform.async_register_entity_service(
        SERVICE_SELECT_SUBTITLE_TRACK,
        {vol.Optional(ATTR_LANGUAGE_CODE): cv.string},
        "select_subtitle_track",
    )


class _VideoSnippet(TypedDict):
    """Data schema as it comes from API."""

    title: str
    description: str
    channelTitle: str


class _VideoInfo:
    """Class to store video info from YouTube Data API."""

    id: str
    title: str
    description: str
    channel_title: str

    def __init__(self, video_id: str, snippet: _VideoSnippet) -> None:
        self.id = video_id
        self.title = snippet["title"]
        self.description = snippet["description"]
        self.channel_title = snippet["channelTitle"]


class YtEventListener(EventListener):
    def __init__(
        self, entity: MediaPlayerEntity, video_changed: Callable[[str], Awaitable[Any]]
    ):
        self._entity = entity
        self._video_changed = video_changed
        self.video_id: str | None = None
        self.state = YtState.Stopped
        self.current_time: int | None = None
        self.duration: int | None = None
        self.volume: float | None = None
        self.muted: bool | None = None
        self.position_updated_at = homeassistant.util.dt.utcnow()
        self.subtitle_track: str | None = None

    def reset(self):
        self.video_id = None
        self.state = None
        self.current_time = None
        self.duration = None
        self.volume = None
        self.muted = None
        self.position_updated_at = None
        self.subtitle_track = None

    def copy_state(self, event: PlaybackStateEvent | NowPlayingEvent):
        self.state = event.state
        self.current_time = event.current_time
        self.position_updated_at = homeassistant.util.dt.utcnow()
        self.duration = event.duration

    async def playback_state_changed(self, event):
        self.copy_state(event)
        self._entity.async_write_ha_state()

    async def now_playing_changed(self, event):
        self.copy_state(event)
        if event.video_id != self.video_id:
            self.video_id = event.video_id
            await self._video_changed()
        self._entity.async_write_ha_state()

    async def volume_changed(self, event):
        self.volume = event.volume / 100
        self.muted = event.muted
        self._entity.async_write_ha_state()

    async def subtitles_track_changed(self, event):
        self.subtitle_track = event.language_code
        self._entity.async_write_ha_state()


CONNECT_RETRY_INTERVAL = 10
ERROR_RETRY_INTERVAL = 30
SUBSCRIBE_RETRY_INTERVAL = 1


class YtMediaPlayer(MediaPlayerEntity):
    """Media player entity for YouTube Lounge integration."""

    def __init__(
        self, entry: ConfigEntry, api: YtLoungeApi, api_key: str | None
    ) -> None:
        """Initialize media player entity with api and optional api key."""
        self._entry = entry
        self._api = api
        self._google_api_key = api_key
        self._yt_api = None

        self._video_info: _VideoInfo | None = None
        self._yt_listener = YtEventListener(self, self._update_video_snippet)
        api.event_listener = self._yt_listener
        self._subscription: Task | None = None

    async def _setup_youtube_api(self):
        async with Aiogoogle(api_key=self._google_api_key) as aiogoogle:
            self._yt_api = await aiogoogle.discover("youtube", "v3")
        if self._yt_listener.video_id:
            await self._update_video_snippet()
            self.async_write_ha_state()

    async def _subscription_task(self):
        while True:
            try:
                LOGGER.debug("Starting subscribe and keep alive")
                await self._subscribe_and_keep_alive()
            except asyncio.CancelledError:
                break
            except:
                LOGGER.exception(
                    "Subscribe and keep alive encountered error, waiting %.0f seconds",
                    ERROR_RETRY_INTERVAL,
                )
                await asyncio.sleep(ERROR_RETRY_INTERVAL)

    async def _subscribe_and_keep_alive(self):
        if not self._api.connected():
            await self._api.connect()

        while True:
            while not self._api.connected():
                LOGGER.debug("subscribe_and_keep_alive: reconnecting")
                self._yt_listener.reset()
                await asyncio.sleep(CONNECT_RETRY_INTERVAL)
                if not self._api.linked():
                    await self._api.refresh_auth()
                await self._api.connect()
            LOGGER.debug("subscribe_and_keep_alive: subscribing")
            await self._api.subscribe()
            await asyncio.sleep(SUBSCRIBE_RETRY_INTERVAL)

    async def manual_reconnect(self):
        """Refresh the authorization of the api, to manually fix broken connections."""
        if self._subscription:
            LOGGER.debug("manual_reconnect: cancelling subscription")
            self._subscription.cancel()
            LOGGER.debug("manual_reconnect: waiting for subscription to end")
            await self._subscription
            refreshed = await self._api.refresh_auth()
            LOGGER.debug("manual_reconnect: refresh auth %s", refreshed)
            connected = await self._api.connect()
            LOGGER.debug("manual_reconnect: connect %s", connected)
        self._subscription = self._entry.async_create_background_task(
            self.hass, self._subscription_task(), "Subscription"
        )

    async def select_subtitle_track(self, language_code: str | None):
        """Select subtitle track based on language code"""
        if self._yt_listener.video_id:
            return await self._api.set_closed_captions(
                language_code=language_code,
                video_id=self._yt_listener.video_id,
            )
        LOGGER.warning("select_subtitle_track: no video currently playing")
        return False

    async def async_added_to_hass(self) -> None:
        """Connect and subscribe to dispatcher signals and state updates."""
        await super().async_added_to_hass()

        self._subscription = self._entry.async_create_background_task(
            self.hass, self._subscription_task(), "Subscription"
        )

        if self._google_api_key:
            self._entry.async_create_task(
                self.hass, self._setup_youtube_api(), "Setup youtube api"
            )

        self.async_on_remove(self._removed_from_hass)

    def _removed_from_hass(self) -> None:
        if self._subscription:
            self._subscription.cancel()
            self._subscription = None

    async def _update_video_snippet(self):
        if self._yt_api and self._yt_listener.video_id:
            async with Aiogoogle(api_key=self._google_api_key) as aiogoogle:
                request = self._yt_api.videos.list(
                    part="snippet", id=self._yt_listener.video_id
                )
                response = await aiogoogle.as_api_key(request)
                snippet = response["items"][0]["snippet"]
                self._video_info = _VideoInfo(self._yt_listener.video_id, snippet)
        else:
            self._video_info = None

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return self._api.auth.screen_id

    @property
    def has_entity_name(self) -> bool:
        """Return if the name of the entity is describing only the entity itself."""
        return True

    @property
    def name(self):
        """Name of the entity."""
        # return None to use device name
        return None

    @property
    def state(self) -> MediaPlayerState:
        """State of the player."""
        if not self._yt_listener.state:
            return MediaPlayerState.OFF
        if self._yt_listener.state in [
            YtState.Playing,
            YtState.Starting,
            YtState.Buffering,
            YtState.Advertisement,
        ]:
            return MediaPlayerState.PLAYING
        if self._yt_listener.state == YtState.Paused:
            return MediaPlayerState.PAUSED
        if self._yt_listener.state == YtState.Stopped:
            return MediaPlayerState.ON
        return MediaPlayerState.OFF

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Flag media player features that are supported."""
        return (
            MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PREVIOUS_TRACK
            | MediaPlayerEntityFeature.NEXT_TRACK
            | MediaPlayerEntityFeature.SEEK
            | MediaPlayerEntityFeature.VOLUME_SET
        )

    @property
    def device_class(self) -> MediaPlayerDeviceClass | None:
        """Return the class of this entity."""
        return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information for this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._api.auth.screen_id)},
            manufacturer="YouTube",
            name=f"YouTube on {self._entry.title}",
        )

    @property
    def media_title(self) -> str | None:
        """Title of current playing media."""
        return self._video_info and self._video_info.title or None

    @property
    def media_channel(self) -> str | None:
        """Channel currently playing."""
        return self._video_info and self._video_info.channel_title or None

    @property
    def media_position(self) -> int | None:
        """Position of current playing media in seconds."""
        return (
            self._yt_listener.current_time
            and int(self._yt_listener.current_time)
            or None
        )

    @property
    def media_position_updated_at(self) -> dt.datetime | None:
        """When was the position of the current playing media valid.

        Returns value from homeassistant.util.dt.utcnow().
        """
        return self._yt_listener.position_updated_at

    @property
    def media_duration(self) -> int | None:
        """Duration of current playing media in seconds."""
        return self._yt_listener.duration and int(self._yt_listener.duration) or None

    @property
    def media_image_url(self) -> str | None:
        """Image url of current playing media."""
        if self._yt_listener.video_id:
            return get_thumbnail_url(self._yt_listener.video_id)

        return None

    @property
    def volume_level(self) -> float | None:
        """Volume level of the media player (0..1)."""
        return self._yt_listener.volume

    @property
    def is_volume_muted(self) -> bool | None:
        """Boolean if volume is currently muted."""
        return self._yt_listener.muted

    async def async_media_pause(self) -> None:
        """Send pause command."""
        return await self._api.pause()

    async def async_media_play(self) -> None:
        """Send play command."""
        return await self._api.play()

    async def async_media_previous_track(self) -> None:
        """Send previous track command."""
        return await self._api.previous()

    async def async_media_next_track(self) -> None:
        """Send next track command."""
        return await self._api.next()

    async def async_media_seek(self, position: float) -> None:
        """Send seek command."""
        return await self._api.seek_to(position)

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        self.extra_state_attributes
        return await self._api.set_volume(math.floor(volume * 100))

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return entity specific state attributes.

        Implemented by platform classes. Convention for attribute names
        is lowercase snake_case.
        """
        return {"subtitle_track": self._yt_listener.subtitle_track}
