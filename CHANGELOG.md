# Changelog

## 26.10.3 – 25.10.2026

- **Fix:** The PSN cloud asked for a new NPSSO token every 3 days. Sony's refresh token runs out a fixed 10 days
  after it was issued (renewals do not extend it), and the repair showed up 7 days before. Home Assistant now keeps
  the NPSSO token and fetches a new refresh token by itself 3 days before expiry, also right after a long downtime.
  A repair only appears when Sony rejects the NPSSO (about every two months). Existing setups get the repair one
  last time, after that it renews itself.
- The diagnostics also redact the stored NPSSO token.
- Tests: renewal via NPSSO, rescue of an expired token, rejected and unreachable NPSSO, no early warning.

## 26.10.2 – 19.10.2026

- **Fix:** If the PS5 is switched off completely while the rest mode command is being retried (no answer on the
  network any more), Home Assistant stops sending right away; before, all attempts kept running and a wrong warning
  showed up at the end.
- Errors of Sony's update page (not reachable, no version number) are translated.
- Tests: error paths of the update page and "fully off" during the retries; fictitious online name in the test data.
- **Security:** The diagnostics now also redact the PSN account ID and the cloud's refresh token.
- **Fix:** "Re-pair" dropped the PSN cloud fields from the entry; the cloud had to be set up again afterwards.
- **Fix:** After a rest mode command the console did not accept, "off" stays while Home Assistant retries in the
  background (up to 210 s); before, the entity jumped back to "on" after 45 s and the log warned too early. A second
  "off" no longer starts a second retry chain.
- **Fix:** Unreadable answers from the PS5 (no HTTP, endless header) are a clean error instead of a crash.
- Setup no longer waits for Sony: the first cloud request and the update page run in the background (cloud entities
  are unavailable until the first answer).
- The device only reports its network connection with a real MAC address.
- README: entities are never "unavailable" (console off = off), background retries explained.
- Tooling: GitHub checks (ruff, mypy, translations, pytest with a coverage threshold, hassfest, weekly against the
  latest Home Assistant), `tools/pruefe-uebersetzungen.py`, `tools/next-version` from the shared template.

## 26.10.1 – 16.10.2026

- **New version numbers:** From now on the version counts **year.month.number of the release in that month**: `26.10.1` is the first release in October 2026, next month starts again at `.1` (for example `26.11.1`). The jump from `1.2.3` to `26.10.1` is intentional: Home Assistant recognises `26.10.1` as newer, and the update shows up as usual.

## 1.2.3 – 13.10.2026

- Avatar and game cover show a picture right after startup (instead of "unknown" until the first change).

## 1.2.2 – 13.10.2026

- Sturdier rest mode (found live): shortly after waking up the PS5 drops the session and then reports "Remote Play
  busy" (0x80108B10) for about a minute. Now: no error, Home Assistant retries in the background after 20/40/60/90 s.
- "Busy" no longer triggers re-pairing; only real pairing errors do (0x80108B02/0x80108B09).

## 1.2.1 – 13.10.2026

- Media player: rest mode counts as "off" (Home Assistant deprecated "standby"); rest mode and off are still told
  apart in the status sensor.

## 1.2.0 – 13.10.2026

- Optional PSN cloud (Configure → NPSSO): current game with cover (game sensor, media player, image), online status,
  last online, trophy level and progress, platinum/gold/silver/bronze, PS Plus, avatar, online ID. Only the refresh
  token is stored (replaced on every renewal); its expiry is a sensor, and a repair with an input field shows up
  7 days before and when it expires. Control stays local.
- Update entity "System software": installed (local) against the latest version on Sony's update page, with release
  notes. Home Assistant cannot install it; the PS5 does that itself in rest mode.
- Important: the PS5 does not report the current game locally (only the PS4 did); without the cloud the game sensor
  stays empty.

## 1.1.0 – 13.10.2026

- Look up the account ID with an NPSSO token (for everyone who does not know their PSN account ID); the token is not
  stored.
- Discovery also by MAC address (Sony prefix) and for known devices (IP change).

## 1.0.0 – 13.10.2026

First version: status, current game, wake-up, rest mode, locally over DDP/Remote Play; pairing with a PIN, DHCP
discovery, re-pair, reconfigure, diagnostics.
