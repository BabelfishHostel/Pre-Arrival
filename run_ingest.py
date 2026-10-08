#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_ingest.py - Ingest-Job: Postfach abrufen, CSV importieren, aufräumen.

Ablauf je Lauf:
    1. Alle ungelesenen, passenden Mails nacheinander abholen (neueste zuerst).
    2. Die CSV jeder Mail in die Datenbank importieren (csv_import.py).
    3. Nach erfolgreichem Import die Mail nach "Verarbeitet" verschieben und
       die heruntergeladene CSV löschen (sie enthält Gästedaten).
    4. Gäste, deren Abreise länger als RETENTION_DAYS zurückliegt, löschen.

Schlägt der Import einer Mail fehl, bleibt sie ungelesen im Postfach, der Lauf
bricht ab und endet mit Exit-Code 1 (Cron/Monitoring kann darauf reagieren).
Eine CSV nur mit Kopfzeile (Tag ohne Anreisen) ist kein Fehler.

Konfiguration über Umgebungsvariablen oder eine ".env" neben diesem Skript
(Vorlage: .env.example). Bereits gesetzte Umgebungsvariablen haben Vorrang.
Anmeldung: IMAP_PASSWORD (klassisch) oder, wenn es fehlt, OAuth2 über die
AZURE_*-Werte (Microsoft 365, benötigt `pip install msal`).

Aufruf:
    python run_ingest.py
"""

import imaplib
import logging
import os
import sys
from pathlib import Path
from typing import Callable, Optional

from csv_import import ImportFehler, importiere_csv
from db import init_db, loesche_alte_gaeste, verbinde
from imap_anhang_abruf import (
    ImapKonfig,
    hole_access_token_msal,
    hole_anhang,
    markiere_verarbeitet,
    quote_ordner,
)

log = logging.getLogger("pre_arrival")

MAX_MAILS_PRO_LAUF = 20


def lade_env(pfad: Path) -> None:
    """Liest 'KEY=VALUE'-Zeilen einer .env in os.environ (ohne zu überschreiben).

    Args:
        pfad: Pfad der .env; existiert sie nicht, passiert nichts.
    """
    if not pfad.is_file():
        return
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        schluessel, wert = zeile.split("=", 1)
        os.environ.setdefault(schluessel.strip(), wert.strip().strip('"').strip("'"))


def _pflicht(name: str) -> str:
    """Liest eine Pflicht-Umgebungsvariable.

    Raises:
        RuntimeError: Wenn sie fehlt oder leer ist.
    """
    wert = os.environ.get(name, "").strip()
    if not wert:
        raise RuntimeError(f"Umgebungsvariable {name} ist nicht gesetzt.")
    return wert


def _anmeldung():
    """Wählt die Anmeldeart anhand der Umgebung.

    Ist IMAP_PASSWORD gesetzt, wird das klassische IMAP-Login verwendet.
    Andernfalls wird OAuth2 (Microsoft 365 / Exchange Online) genutzt; dafür
    müssen AZURE_TENANT_ID, AZURE_CLIENT_ID und AZURE_CLIENT_SECRET gesetzt sein.

    Returns:
        Tupel (passwort, token_provider); genau einer der beiden Werte ist None.

    Raises:
        RuntimeError: Wenn weder Passwort noch vollständige Azure-Werte vorliegen.
    """
    passwort = os.environ.get("IMAP_PASSWORD", "").strip()
    if passwort:
        return passwort, None
    azure = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")
    fehlend = [name for name in azure if not os.environ.get(name, "").strip()]
    if fehlend:
        raise RuntimeError(
            "Weder IMAP_PASSWORD noch die Azure-Werte gesetzt (es fehlt: "
            + ", ".join(fehlend) + ")."
        )
    return None, hole_access_token_msal


def lese_konfig() -> dict:
    """Baut die Laufkonfiguration aus der Umgebung.

    Returns:
        Dict mit `imap` (ImapKonfig), `passwort`, `token_provider`, `db_pfad`
        und `aufbewahrung_tage`.

    Raises:
        RuntimeError: Bei fehlenden Pflichtwerten oder ungültiger Zahl.
    """
    try:
        tage = int(os.environ.get("RETENTION_DAYS", "30"))
    except ValueError as exc:
        raise RuntimeError("RETENTION_DAYS muss eine ganze Zahl sein.") from exc
    imap = ImapKonfig(
        server=_pflicht("IMAP_SERVER"),
        user=_pflicht("IMAP_USER"),
        absender_filter=_pflicht("ABSENDER_FILTER"),
        betreff_filter=_pflicht("BETREFF_FILTER"),
        zielordner=Path(_pflicht("DOWNLOAD_DIR")),
        port=int(os.environ.get("IMAP_PORT", "993")),
        verarbeitet_ordner=os.environ.get("VERARBEITET_ORDNER", "Verarbeitet"),
        dateiendung=".csv",
    )
    try:
        quote_ordner(imap.verarbeitet_ordner)  # prüft, ob der Ordnername zulässig ist
    except ValueError as exc:
        raise RuntimeError(f"VERARBEITET_ORDNER ungültig: {exc}") from exc
    passwort, token_provider = _anmeldung()
    return {
        "imap": imap,
        "passwort": passwort,
        "token_provider": token_provider,
        "db_pfad": Path(_pflicht("DB_PATH")),
        "aufbewahrung_tage": tage,
    }


def verarbeite_postfach(
    conn,
    imap_konfig: ImapKonfig,
    passwort: Optional[str] = None,
    token_provider: Optional[Callable[[], str]] = None,
) -> int:
    """Holt und importiert alle passenden Mails.

    Args:
        conn: Verbindung zur initialisierten Datenbank.
        imap_konfig: Postfach- und Filtereinstellungen.
        passwort: IMAP-Passwort (klassisches Login), sonst None.
        token_provider: Callable für OAuth2-Tokens, sonst None.

    Returns:
        Anzahl vollständig verarbeiteter Mails.

    Raises:
        ImportFehler: Wenn eine CSV nicht importiert werden kann (Mail bleibt ungelesen).
        imaplib.IMAP4.error, OSError, RuntimeError: Bei Postfach-, Netzwerk- oder
            Token-Problemen.
    """
    verarbeitet = 0
    for _ in range(MAX_MAILS_PRO_LAUF):
        ergebnis = hole_anhang(imap_konfig, token_provider=token_provider, passwort=passwort)
        if ergebnis is None:
            return verarbeitet
        try:
            imp = importiere_csv(conn, ergebnis.dateipfad)
            if imp.bereits_importiert:
                log.info("%s: bereits importiert, Mail wird nur abgeschlossen.", ergebnis.dateipfad.name)
            else:
                log.info(
                    "%s: %d Zeilen, %d neu, %d aktualisiert, %d Fehler.",
                    ergebnis.dateipfad.name, imp.gesamt, imp.neu, imp.aktualisiert, imp.fehler,
                )
                if imp.gesamt == 0:
                    log.info("Keine Anreisen in dieser Datei.")
                for meldung in imp.fehler_details:
                    log.warning("  %s", meldung)
            markiere_verarbeitet(ergebnis.imap, ergebnis.msg_num, imap_konfig)
            ergebnis.dateipfad.unlink(missing_ok=True)  # enthält Gästedaten
            verarbeitet += 1
        finally:
            try:
                ergebnis.imap.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
    log.warning("Maximal %d Mails pro Lauf erreicht, Rest beim nächsten Lauf.", MAX_MAILS_PRO_LAUF)
    return verarbeitet


def main(env_pfad: Optional[Path] = None) -> int:
    """Führt den Ingest-Job aus.

    Args:
        env_pfad: Pfad der .env (Standard: neben diesem Skript).

    Returns:
        0 bei Erfolg, 1 bei Fehlern, 2 bei fehlender Konfiguration.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    lade_env(env_pfad or Path(__file__).with_name(".env"))
    try:
        konfig = lese_konfig()
    except RuntimeError as exc:
        log.error("Konfiguration unvollständig: %s", exc)
        return 2

    exit_code = 0
    conn = verbinde(konfig["db_pfad"])
    try:
        init_db(conn)
        try:
            anzahl = verarbeite_postfach(
                conn, konfig["imap"], konfig["passwort"], konfig["token_provider"]
            )
            log.info("%d Mail(s) verarbeitet.", anzahl)
        except ImportFehler as exc:
            log.error("Import fehlgeschlagen, Mail bleibt ungelesen: %s", exc)
            exit_code = 1
        except (imaplib.IMAP4.error, OSError, RuntimeError) as exc:
            log.error("Postfach-Zugriff fehlgeschlagen: %s", exc)
            exit_code = 1
        geloescht = loesche_alte_gaeste(conn, konfig["aufbewahrung_tage"])
        if geloescht:
            log.info("%d Gäste nach Ablauf der Aufbewahrungsfrist gelöscht.", geloescht)
    finally:
        conn.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
