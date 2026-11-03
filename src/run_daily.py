#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_daily.py - Tagesjob für den Cronjob: erst Ingest, dann Versand.

Beide Teile sind idempotent, der Job kann deshalb mehrmals täglich laufen
(z. B. stündlich ab 06:15), damit eine verspätete CSV-Mail trotzdem noch am
selben Tag verarbeitet wird.

* Ein Lock-File verhindert parallele Läufe.
* Der Versand startet nur, wenn der Ingest ohne Fehler lief.
* Optional (HEALTHCHECK_URL): Ping nach erfolgreichem Lauf, WENN heute eine CSV
  importiert wurde; bei Fehlern Ping auf /fail. Bleibt der Ping aus, schlägt
  Healthchecks.io Alarm (z. B. ASSD-Mail ist nicht angekommen).

Betriebsart (SEND_MODUS in der .env):
    vorschau  (Standard) Es wird NICHTS versendet. Die Mails werden gerendert und als
              HTML-Dateien in VORSCHAU_DIR abgelegt (Dateien älter als VORSCHAU_TAGE
              Tage werden gelöscht). Die Dateien enthalten Gästedaten und Paylinks.
    live      Echter Versand an die Gäste (run_send.py --live).

Exit-Code: 0 ok, 1 Fehler, 2 Konfiguration, 3 anderer Lauf aktiv.
"""

import logging
import os
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path
from typing import Optional

import run_ingest
import run_send
from db import verbinde
from run_ingest import STANDARD_ENV, lade_env, pfad_aus_env

log = logging.getLogger("run_daily")
LOCK_MAX_ALTER = 2 * 3600  # ein älteres Lock gilt als Überbleibsel eines Absturzes


def _lock_holen(pfad: Path) -> bool:
    try:
        fd = os.open(str(pfad), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - pfad.stat().st_mtime < LOCK_MAX_ALTER:
            return False
        log.warning("Altes Lock-File (Absturz?) wird übernommen: %s", pfad)
        pfad.unlink()
        return _lock_holen(pfad)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return True


def loesche_alte_vorschauen(ordner: Path, tage: int) -> int:
    """Löscht *.html im Vorschau-Ordner, die älter als 'tage' Tage sind (Datenschutz)."""
    if not ordner.is_dir():
        return 0
    grenze, n = time.time() - tage * 86400, 0
    for f in ordner.glob("*.html"):
        if f.stat().st_mtime < grenze:
            f.unlink()
            n += 1
    return n


def send_argumente(modus: str, vorschau_dir: Path) -> list:
    """Argumente für run_send je nach Betriebsart; unbekannte Werte fallen auf 'vorschau' zurück."""
    if modus == "live":
        return ["--live"]
    return ["--vorschau", str(vorschau_dir)]


def heute_importiert(db_pfad: Path) -> bool:
    conn = verbinde(db_pfad)
    try:
        z = conn.execute("SELECT COUNT(*) FROM import_log WHERE substr(importiert_am,1,10)=?",
                         (date.today().isoformat(),)).fetchone()
        return z[0] > 0
    finally:
        conn.close()


def ping(url: str, fehler: bool = False) -> None:
    if not url:
        return
    try:
        urllib.request.urlopen(url.rstrip("/") + ("/fail" if fehler else ""), timeout=10).close()  # noqa: S310
    except Exception as exc:  # Monitoring darf den Job nie beeinträchtigen
        log.warning("Healthcheck-Ping fehlgeschlagen: %s", exc)


def main(env_pfad: Optional[Path] = None, argv_send: Optional[list] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env_pfad = env_pfad or STANDARD_ENV
    lade_env(env_pfad)
    lock = pfad_aus_env(os.environ.get("LOCK_FILE", "data/run_daily.lock"))
    lock.parent.mkdir(parents=True, exist_ok=True)
    if not _lock_holen(lock):
        log.warning("Ein anderer Lauf ist aktiv, Abbruch.")
        return 3
    url = os.environ.get("HEALTHCHECK_URL", "").strip()
    try:
        code = run_ingest.main(env_pfad)
        if code == 0:
            modus = os.environ.get("SEND_MODUS", "vorschau").strip().lower()
            vorschau_dir = pfad_aus_env(os.environ.get("VORSCHAU_DIR", "data/vorschau"))
            if modus != "live":
                n = loesche_alte_vorschauen(vorschau_dir, int(os.environ.get("VORSCHAU_TAGE", "7")))
                log.info("Betriebsart VORSCHAU: es wird nichts versendet (%d alte Vorschau(en) gelöscht).", n)
            code = run_send.main(argv_send if argv_send is not None else send_argumente(modus, vorschau_dir), env_pfad)
        if code == 0:
            if heute_importiert(pfad_aus_env(os.environ["DB_PATH"])):
                ping(url)
            else:
                log.info("Heute noch keine CSV importiert - kein Healthcheck-Ping.")
        else:
            ping(url, fehler=True)
        return code
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
