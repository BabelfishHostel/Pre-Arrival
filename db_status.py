#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db_status.py - Zeigt den Stand der Datenbank (nur Zahlen, keine Gästedaten).

Liest DB_PATH aus der .env (oder aus dem ersten Argument) und gibt aus:
    * die letzten Importe mit Zeilenzahlen
    * die Anzahl Gäste je Anreisedatum
    * den Status der Mails im mail_log

Aufruf:
    python db_status.py [pfad/zur/datenbank.sqlite]
"""

import os
import sys
from pathlib import Path

from db import verbinde
from run_ingest import lade_env


def main(argv) -> int:
    """Gibt den Datenbankstand aus; 0 bei Erfolg, 2 wenn kein Pfad bekannt ist."""
    lade_env(Path(__file__).with_name(".env"))
    pfad = argv[0] if argv else os.environ.get("DB_PATH", "")
    if not pfad or not Path(pfad).is_file():
        print(f"Datenbank nicht gefunden: '{pfad}'")
        return 2
    conn = verbinde(Path(pfad))
    print(f"Datenbank: {pfad}")

    print("\nImporte (neueste zuerst):")
    zeilen = conn.execute(
        "SELECT id, importiert_am, dateiname, zeilen_gesamt, zeilen_neu,"
        " zeilen_aktualisiert, zeilen_fehler FROM import_log ORDER BY id DESC LIMIT 10"
    ).fetchall()
    for z in zeilen or []:
        print(f"  #{z['id']} {z['importiert_am']}  {z['dateiname']}: {z['zeilen_gesamt']} Zeilen, "
              f"{z['zeilen_neu']} neu, {z['zeilen_aktualisiert']} aktualisiert, {z['zeilen_fehler']} Fehler")
    if not zeilen:
        print("  (keine)")

    print("\nGäste je Anreisedatum:")
    gaeste = conn.execute(
        "SELECT anreise, COUNT(*) AS n FROM arrivals GROUP BY anreise ORDER BY anreise"
    ).fetchall()
    for g in gaeste:
        print(f"  {g['anreise']}: {g['n']}")
    if not gaeste:
        print("  (keine)")

    print("\nMail-Log je Status:")
    status = conn.execute("SELECT status, COUNT(*) AS n FROM mail_log GROUP BY status").fetchall()
    for s in status:
        print(f"  {s['status']}: {s['n']}")
    if not status:
        print("  (keine Einträge)")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
