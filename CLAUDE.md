# PlayStation 5 – Projektregeln

HA-Integration (`custom_components/ps5`, Domain `ps5`) für die PlayStation 5: Status, Aufwecken und Ruhemodus lokal
über DDP/Remote Play, optional Spiel/Trophäen über die PSN-Cloud. Entwickelt und geprüft an einer PS5 (der Test-Konsole).
Alternative zur App ps5-mqtt. Lizenz GPLv3 (Protokoll und Schlüsseltabellen aus pyremoteplay, siehe `NOTICE`).

- Repo ist **öffentlich**: nichts Persönliches ins Repo (keine Namen, echten IPs/MACs/Konto-IDs, Konsolennamen,
  Tokens, Heimnetz-Details, lokalen Pfade). Testdaten bleiben fiktiv (192.0.2.x, aa:bb:cc:dd:ee:ff, Konsolenname
  PS5-123, erfundene Konto-IDs und Online-Namen).
- README, CHANGELOG, Release-Notizen, Issue-Texte und Commit-Texte auf Englisch.
- Lokal committen ok, Push und Releases nur nach Rückfrage.

## Protokoll (13.10.2026, gegen pyremoteplay 0.7.6 byte-genau verglichen)

Ablauf steht in `protocol.py`/`keys.py`/`psn.py`; hier nur, was der Code nicht erklärt:
- Kopplung: kaputte Anfragezeile bei `POST /sie/ps5/rp/sess/rgst` ist Absicht. Ruhemodus: Reihenfolge der
  encrypt-Aufrufe für die Kopfzeilen zählt!
- Konto-ID per NPSSO: Parameter 1:1 wie PSNAWP 3.0.3. Falsches Token → Location mit error=… (live geprüft).
- PS5-DDP enthält KEIN running-app (am 13.10. mit FIFA 26 geprüft) → Spiel nur über PSN-Cloud.
- Erneuerungs-Token (refresh_token) hält fest ~10 Tage ab NPSSO-Tausch, die Erneuerung verlängert NICHT (live
  13.–25.10.2026, Ablauf schwankt nur ±1 s). Darum bleibt das NPSSO im Eintrag (`psn_npsso`) und holt 3 Tage vor
  Ablauf ein neues; Reparatur nur, wenn Sony das NPSSO ablehnt.
- Firmware: Version von der Sony-Seite (`firmware.py`) gegen DDP system-version. Kein Installieren von außen.
- Live 13.10.: Aufwecken ~5 s; Ruhemodus ok. Jede init-Anfrage reserviert Remote Play ~1 Min (danach 403
  80108b10 „belegt“) – also NICHT mit init-Anfragen pollen. Kurz nach dem Aufwachen bricht ctrl ab → Wiederholung.
- Test gegen das Original pyps4-2ndscreen: Vergleichsskript per uv (pycryptodomex, pyee<9, pyps4-2ndscreen, netifaces
  gestubbt); das frühere `cmp_ps5.py` ist nicht mehr da, bei Bedarf neu schreiben.

## Version
Versionen über `tools/next-version` (globale Regeln), Tag/Titel ohne v = genau die manifest-Version;
`CHANGELOG.md` `## YY.M.N – DD.MM.YYYY`, englisch. Die Versions-Badge im README liest die manifest.json selbst.

## Prüfen

- `python -m pytest -q -p no:logging` in einer venv mit `requirements_test.txt` (tests/fake_ps5.py = nachgebaute PS5 auf
  localhost, Sockets über das Fixture `socket_enabled`)
- `uvx ruff@0.16.9 check . && uvx ruff@0.16.9 format --check .`, `uvx mypy@2.3.1 --python-executable <venv>/bin/python`
  (Versionen wie in `.github/workflows/pruefung.yml`)
- `python tools/pruefe-uebersetzungen.py` (strings.json gegen translations/*.json)
- CI `pruefung.yml`: dasselbe bei Push/PR auf main, montags zusätzlich gegen die neueste HA; Abdeckungsgrenze 94 %
  (`--cov-fail-under`, bei Bedarf anheben). `hacs.yml`: HACS-Validierung.
- hassfest: `docker run --rm -v "$PWD/custom_components/ps5:/github/workspace/custom_components/ps5" ghcr.io/home-assistant/hassfest`
- Live: in einer Test-HA mit der Test-Konsole. Status geht ohne Kopplung; Ein/Aus und Kopplung nur, wenn gerade
  niemand an der Konsole spielt. Danach die Konsole wieder in den Ausgangszustand setzen.
