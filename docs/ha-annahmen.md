# HA-Annahmen

<!-- Vorlage aus repo-vorlagen/vorlagen/ha-annahmen.md. Liegt im Repo als docs/ha-annahmen.md.
     Der HA-Beta-Bericht (repo-vorlagen/ha-beta-bericht.md) gleicht diese Liste jeden Monat mit den
     Neuerungen der HA-Beta ab. Nur festhalten, was bei einer Änderung in HA still kaputtginge. -->

Worauf sich dieses Repo bei Home Assistant verlässt. Bei jeder Änderung am Code mitpflegen.

**Getestet mit:** HA 2026.9.3 · **Mindestversion:** 2026.9.0 (`hacs.json`)

## Annahmen

| Annahme | Wo im Code | Abgesichert durch |
|---|---|---|
| `async_update_entry(entry, data=…)` ohne Neuladen löst kein Reload aus (Token-Rotation jede Stunde) | `cloud.py` `_save_token` | `tests/test_cloud.py::test_cloud_values` |
| `async_schedule_reload` nach `async_update_entry` lädt mit den neuen Daten (NPSSO, Token) | `cloud.py` `async_activate`, `deactivate` | `test_options_enable_and_disable`, `test_token_expired_creates_repair` |
| Reparatur mit `is_fixable=True` ruft `repairs.async_create_fix_flow(hass, issue_id, data)` mit dem `data` des Issues auf | `cloud.py` `_issue`, `repairs.py` | `test_token_expired_creates_repair` |
| `async_create_issue` mit gleicher ID ersetzt das Issue (Wechsel WARNING → ERROR, kein Duplikat) | `cloud.py` `_issue` | `test_expiring_soon_warns`, `test_npsso_rejected_asks_once` |
| `UpdateFailed(translation_domain=…, translation_key=…)` macht die Cloud-Entitäten nur nicht verfügbar, der lokale Coordinator läuft weiter | `cloud.py` `_async_update_data` | `test_token_expired_creates_repair` |
| `entry.async_create_background_task` blockiert das Setup nicht (erste Cloud-Abfrage) | `__init__.py` | `test_setup_does_not_wait_for_cloud` |
| `async_update_reload_and_abort(…, data_updates=…)` behält übrige Felder im Eintrag (PSN-Cloud bei Neukopplung) | `config_flow.py` Reauth | `tests/test_config_flow.py::test_reauth` |
| DHCP-Erkennung über `manifest.json` `dhcp` (Hostname `ps5-*`, MAC-Präfix, `registered_devices`) | `config_flow.py` `async_step_dhcp` | `test_dhcp` |
| `ImageEntity.__init__(self, hass)` muss von Hand aufgerufen werden (Mehrfachvererbung) | `image.py` | `test_cloud_values` |

## Fremde Integrationen

Keine. Die PSN-Cloud spricht Sony direkt an (`psn.py`), nicht über die HA-Integration „PlayStation Network“.

## Prüfbefehl

Gegen eine andere HA-Version: `requirements_test.txt` anpassen und `python -m pytest -q -p no:logging`. Montags
läuft `.github/workflows/pruefung.yml` zusätzlich gegen die neueste HA.

```sh
python -m pytest -q -p no:logging
```
