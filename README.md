# YouTube Lounge media player

[![GitHub Release][releases-shield]][releases-link]
[![License][license-shield]][license-link]

## Description

This is a custom integration for [Home Assistant](https://www.home-assistant.io/). It provides a media player for a YouTube Lounge screen by pairing manually or discovery through DIAL.

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=FabioGNR&repository=youtube_lounge)

## Setup

After installing the integration, screens in your network should be automatically discovered if they support DIAL.
If not, go to Configuration -> Integrations, click the + button at the bottom right, and search for "YouTube Lounge" to add a screen through a pairing code.

Optionally a YouTube v3 API token can be configured which is used to retrieve information about the playing video.

## Services

### `youtube_lounge.reconnect`

Reconnect the integration to the YouTube Lounge screen.
This is a manual step sometimes needed when connection gets stuck.

[releases-shield]: https://img.shields.io/github/release/FabioGNR/youtube_lounge.svg
[releases-link]: https://github.com/FabioGNR/youtube_lounge/releases
[license-shield]: https://img.shields.io/github/license/FabioGNR/youtube_lounge?color=brightgreen
[license-link]: https://github.com/FabioGNR/youtube_lounge/blob/master/LICENSE
