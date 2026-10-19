"""Konstanten der Integration PlayStation 5."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "ps5"

CONF_ACCOUNT_ID = "account_id"
CONF_PIN = "pin"
CONF_NPSSO = "npsso"
# Optionaler Cloud-Teil: PSN-Konto und das zuletzt erhaltene Erneuerungs-Token (+ Ablauf als Unix-Zeit)
CONF_PSN_ACCOUNT_ID = "psn_account_id"
CONF_PSN_TOKEN = "psn_refresh_token"
CONF_PSN_EXPIRES = "psn_refresh_expires"
CONF_DISABLE_CLOUD = "disable_cloud"
CONF_REGIST_KEY = "regist_key"
CONF_RP_KEY = "rp_key"

# DDP ist eine kleine UDP-Anfrage – alle 5 s reicht, damit Automationen (HDMI-Umschalten) schnell reagieren
UPDATE_INTERVAL = timedelta(seconds=5)
# So lange zeigen wir nach Aufwecken/Ruhemodus den erwarteten Zustand, bis die PS5 ihn meldet
TRANSITION_SECONDS = 45
