# PlayStation 5 – Projektregeln

HA-Integration (`custom_components/ps5`, Domain `ps5`) für die PlayStation 5: Status, Aufwecken und Ruhemodus lokal
über DDP/Remote Play, optional Spiel/Trophäen über die PSN-Cloud. Entwickelt und geprüft an einer PS5 (der Test-Konsole).
Alternative zur App ps5-mqtt. Lizenz GPLv3 (Protokoll und Schlüsseltabellen aus pyremoteplay, siehe `NOTICE`).

- Repo ist **öffentlich**: nichts Persönliches ins Repo (keine Namen, echten IPs/MACs/Konto-IDs, Konsolennamen,
  Tokens, Heimnetz-Details, lokalen Pfade). Testdaten bleiben fiktiv.
- README, CHANGELOG, Release-Notizen, Issue-Texte und Commit-Texte auf Englisch.
- Lokal committen ok, Push und Releases nur nach Rückfrage.

## Protokoll (13.10.2026, gegen pyremoteplay 0.7.6 byte-genau verglichen)

- DDP UDP 9302: `SRCH * HTTP/1.1\ndevice-discovery-protocol-version:00030010\n` → `200 Ok` (an) / `620 Server
  Standby`, Felder host-id, host-name, system-version, running-app-name/-titleid. Aus = keine Antwort.
- Aufwecken: `WAKEUP` mit `user-credential` = int(bytes.fromhex(bytes.fromhex(RegistKey).decode())).
- Kopplung: UDP `SRC3` → `RES3`, dann TCP 9295 `POST /sie/ps5/rp/sess/rgst` (kaputte Anfragezeile ist Absicht),
  AES-CFB-128 mit Schlüssel aus REG_KEY_0 + PIN, IV = HMAC-SHA256(HMAC_KEY, nonce‖Zähler)[:16]. Antwort:
  PS5-RegistKey, RP-Key. Konto-ID = Base64 der 8 Bytes (little endian) der PSN-Konto-ID.
- Ruhemodus: `GET /sie/ps5/rp/sess/init` (RP-Registkey) → RP-Nonce; `GET …/sess/ctrl` mit verschlüsselten
  Kopfzeilen (Reihenfolge der encrypt-Aufrufe zählt!), auf Nachricht 0x33 (Sitzungs-ID) warten, Herzschlag 0xFE
  beantworten, dann 0x50 senden.
- Konto-ID per NPSSO (psn.py): authorize (302 → code) → token (Basic-Auth der PS-App) → dms …/accounts/me;
  Parameter 1:1 wie PSNAWP 3.0.3. Falsches Token → Location mit error=… (live geprüft).
- PS5-DDP enthält KEIN running-app (am 13.10. mit FIFA 26 geprüft) → Spiel nur über PSN-Cloud (cloud.py, psn.py:
  basicPresences v2, trophySummary, profiles; Token-Rotation über refresh_token, im Eintrag gespeichert).
- Firmware: Sony-Seite /{de-de|en-us}/support/hardware/ps5/system-software-info/ „Version: 26.06-14.10.00“;
  DDP system-version 14100003 → 14.10.00. Kein Installieren von außen.
- Live 13.10.: Aufwecken ~5 s; Ruhemodus ok. Jede init-Anfrage reserviert Remote Play ~1 Min (danach 403
  80108b10 „belegt“) – also NICHT mit init-Anfragen pollen. Kurz nach dem Aufwachen bricht ctrl ab → Wiederholung.
- Test gegen das Original: `cmp_ps5.py`-Muster (uv mit pycryptodomex, pyee<9, pyps4-2ndscreen, netifaces gestubbt).

## Version
Kalender-Schema **YY.M.N** ohne v (Jahr zweistellig, Monat ohne führende Null, N = laufende Nummer im Monat ab 1;
es zählen nur echte, veröffentlichte GitHub-Releases des Monats, auch alte X.Y.Z; bloße Tags nicht; ohne Release im Monat → .1; neuer Monat → wieder .1).
Jedes Release als GitHub-Release veröffentlichen (Tag + Release-Titel ohne v = genau die manifest-Version), sonst zählt es nicht mit.
Nächste Nummer immer aus `tools/next-version`, nie von Hand hochzählen, keine Patch-/Minor-Unterscheidung
(+ `CHANGELOG.md` `## YY.M.N – DD.MM.YYYY`, englisch). Die Versions-Badge im README liest die manifest.json selbst.

## Prüfen

- `python -m pytest -q -p no:logging` in einer venv mit `requirements_test.txt` (tests/fake_ps5.py = nachgebaute PS5 auf
  localhost, Sockets über das Fixture `socket_enabled`)
- `uvx ruff check . && uvx ruff format --check .`, `uvx mypy@2.3.1 --python-executable <venv>/bin/python`
- `python tools/pruefe-uebersetzungen.py` (strings.json gegen translations/*.json)
- GitHub Actions `.github/workflows/pruefung.yml`: dasselbe bei Push/PR auf main, montags zusätzlich gegen die neueste
  HA; Abdeckungsgrenze dort 94 % (`--cov-fail-under`, bei Bedarf anheben). `.github/workflows/hacs.yml`: HACS-Validierung.
- Testdaten sind fiktiv (192.0.2.x, aa:bb:cc:dd:ee:ff, Konsolenname PS5-123, erfundene Konto-IDs und Online-Namen);
  keine echten Werte aus einem Heimnetz oder PSN-Konto in Tests.
- hassfest: `docker run --rm -v "$PWD/custom_components/ps5:/github/workspace/custom_components/ps5" ghcr.io/home-assistant/hassfest`
- Live: in einer Test-HA mit der Test-Konsole. Status geht ohne Kopplung; Ein/Aus und Kopplung nur, wenn gerade
  niemand an der Konsole spielt. Danach die Konsole wieder in den Ausgangszustand setzen.
