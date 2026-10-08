#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
imap_diagnose.py - Diagnose für den Postfach-Zugriff (liest nur, ändert nichts).

Nutzt dieselbe Konfiguration wie run_ingest.py (.env). Gibt aus:
    * Fähigkeiten des IMAP-Servers
    * vorhandene Ordner
    * Antwort auf das Anlegen des Ordners "Verarbeitet"
    * Treffer für den Absender-/Betrefffilter (nur Nummern, keine Inhalte)

Mit `--copy-test` wird zusätzlich die neueste passende Mail per Nummer und per
UID in den Ordner "Verarbeitet" KOPIERT (nicht verschoben, die Mail bleibt im
Posteingang) und die Serverantwort ausgezeichnet. Dabei entstehen Kopien, die
du danach im Ordner löschen kannst.

Aufruf:
    python imap_diagnose.py [--copy-test]
"""

import imaplib
import sys
from pathlib import Path

from imap_anhang_abruf import quote_ordner, verbinde
from run_ingest import lade_env, lese_konfig


def _zeige(titel: str, antwort) -> None:
    """Gibt eine imaplib-Antwort (Status, Daten) lesbar aus."""
    print(f"\n== {titel}")
    print(antwort)


def main(argv) -> int:
    """Führt die Diagnose aus und gibt 0 bei Erfolg zurück."""
    lade_env(Path(__file__).with_name(".env"))
    try:
        konfig = lese_konfig()
    except RuntimeError as exc:
        print(f"Konfiguration unvollständig: {exc}")
        return 2
    ik = konfig["imap"]
    imap = verbinde(ik, konfig["token_provider"], konfig["passwort"])
    try:
        _zeige("Fähigkeiten", imap.capability())
        _zeige("Ordner", imap.list())
        try:
            _zeige(f'CREATE "{ik.verarbeitet_ordner}"', imap.create(quote_ordner(ik.verarbeitet_ordner)))
        except imaplib.IMAP4.error as exc:
            _zeige(f'CREATE "{ik.verarbeitet_ordner}" (Fehler)', repr(exc))

        kriterium = f'(UNSEEN FROM "{ik.absender_filter}" SUBJECT "{ik.betreff_filter}")'
        status, daten = imap.search(None, kriterium)
        _zeige("Suche ungelesen + Filter", (status, daten))
        nummern = daten[0].split() if status == "OK" and daten and daten[0] else []
        if nummern:
            _zeige("UID der neuesten Treffer-Mail", imap.fetch(nummern[-1], "(UID)"))

        if "--copy-test" in argv and nummern:
            nr = nummern[-1]
            try:
                _zeige("COPY per Nummer", imap.copy(nr, quote_ordner(ik.verarbeitet_ordner)))
            except imaplib.IMAP4.error as exc:
                _zeige("COPY per Nummer (Fehler)", repr(exc))
            try:
                _, antwort = imap.fetch(nr, "(UID)")
                uid = antwort[0].decode().split("UID")[1].strip(" )")
                _zeige("COPY per UID", imap.uid("COPY", uid, quote_ordner(ik.verarbeitet_ordner)))
            except (imaplib.IMAP4.error, IndexError, AttributeError) as exc:
                _zeige("COPY per UID (Fehler)", repr(exc))
    finally:
        imap.logout()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
