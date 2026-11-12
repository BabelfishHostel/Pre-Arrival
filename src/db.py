#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db.py - SQLite-Datenbank für die Pre-Arrival-Mails.

Drei Tabellen:
    import_log  eine Zeile je verarbeiteter CSV-Datei (Hash verhindert Doppelimport)
    arrivals    ein Gast je Buchung (RegNummer), ohne Adressdaten
    mail_log    ein Eintrag je Gast und Mailvorlage (verhindert doppelten Versand)

Hinweis zum NAS: SQLite darf nur von Prozessen genutzt werden, die auf dem NAS
selbst laufen. Eine Datenbankdatei auf einer SMB-/NFS-Freigabe, auf die
mehrere Rechner zugreifen, kann durch fehlerhaftes Dateilocking beschädigt
werden. Deshalb bleibt der Journal-Modus auf dem sicheren Standard (kein WAL).
"""

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS import_log (
    id                  INTEGER PRIMARY KEY,
    dateiname           TEXT    NOT NULL,
    sha256              TEXT    NOT NULL UNIQUE,
    importiert_am       TEXT    NOT NULL,
    zeilen_gesamt       INTEGER NOT NULL DEFAULT 0,
    zeilen_neu          INTEGER NOT NULL DEFAULT 0,
    zeilen_aktualisiert INTEGER NOT NULL DEFAULT 0,
    zeilen_fehler       INTEGER NOT NULL DEFAULT 0,
    fehler_details      TEXT
);

CREATE TABLE IF NOT EXISTS arrivals (
    id                  INTEGER PRIMARY KEY,
    reg_nummer          TEXT    NOT NULL UNIQUE,
    vorname             TEXT,
    nachname            TEXT,
    email               TEXT    NOT NULL,
    anreise             TEXT    NOT NULL,   -- ISO-Datum JJJJ-MM-TT
    abreise             TEXT    NOT NULL,   -- ISO-Datum JJJJ-MM-TT
    buchungsdatum       TEXT,               -- ISO-Datum JJJJ-MM-TT
    anzahl_personen     INTEGER,
    kanal               TEXT,               -- EGB, BHC, EHC; leer = Buchung vor Ort/Telefon
    sprache             TEXT,               -- 'de' oder 'en'
    rate                TEXT,               -- 'flex' oder 'fix' (leer = unbekannt)
    buchungsnummer      TEXT,               -- Nummer, die der Gast sieht (z. B. 410200-20-12913); leer = vor Ort/Telefon
    betrag              REAL,               -- veraltet (CSV bis 11/2026: amou1), wird nicht mehr geschrieben
    gesamtbetrag        REAL,               -- veraltet (CSV bis 11/2026: total), wird nicht mehr geschrieben
    paylink             TEXT,               -- persönliche Zahlungs-URL; leer = bezahlt. NICHT loggen.
    vorauszahlung       REAL,               -- CSV Vorauszahlung_1 (> 0: Vorauszahlung nötig)
    vorausdatum         TEXT,               -- CSV Vorausdatum_1 als ISO-Datum; leer/0000-00-00 = keine Vorauszahlung
    schon_eingegangen   REAL,               -- CSV Schon_eingegangen: bisher bezahlt (leer = 0)
    restbetrag          REAL,               -- CSV Restbetrag = Zahlbetrag - Schon_eingegangen (leer = 0)
    zahlbetrag          REAL,               -- CSV Zahlbetrag: Gesamtpreis
    import_id           INTEGER REFERENCES import_log(id),
    erstmals_importiert TEXT    NOT NULL,
    zuletzt_gesehen     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_arrivals_anreise ON arrivals(anreise);

CREATE TABLE IF NOT EXISTS mail_log (
    id              INTEGER PRIMARY KEY,
    arrival_id      INTEGER NOT NULL REFERENCES arrivals(id) ON DELETE CASCADE,
    vorlage         TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'geplant'
                    CHECK (status IN ('geplant', 'gesendet', 'fehler', 'uebersprungen')),
    versuche        INTEGER NOT NULL DEFAULT 0,
    erstellt_am     TEXT    NOT NULL,
    gesendet_am     TEXT,
    message_id      TEXT,
    fehler          TEXT,
    UNIQUE (arrival_id, vorlage)
);
"""


def jetzt() -> str:
    """Aktuelle lokale Zeit als 'JJJJ-MM-TT HH:MM:SS'."""
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def verbinde(pfad: Path) -> sqlite3.Connection:
    """Öffnet die Datenbank und setzt die Verbindungs-Pragmas.

    Args:
        pfad: Pfad der SQLite-Datei (wird bei Bedarf angelegt).

    Returns:
        Verbindung mit Foreign Keys, Row-Factory und 10 s Wartezeit bei Sperren.
    """
    conn = sqlite3.connect(str(pfad), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Legt alle Tabellen an, falls sie fehlen, und setzt die Schemaversion."""
    with conn:
        conn.executescript(SCHEMA)
        spalten = {z["name"] for z in conn.execute("PRAGMA table_info(arrivals)")}
        for name, typ in (("rate", "TEXT"), ("buchungsnummer", "TEXT"), ("betrag", "REAL"),
                          ("gesamtbetrag", "REAL"), ("paylink", "TEXT"), ("vorauszahlung", "REAL"),
                          ("vorausdatum", "TEXT"), ("schon_eingegangen", "REAL"), ("restbetrag", "REAL"),
                          ("zahlbetrag", "REAL")):
            if name not in spalten:  # Migration von älteren Schemata
                conn.execute(f"ALTER TABLE arrivals ADD COLUMN {name} {typ}")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def loesche_alte_gaeste(
    conn: sqlite3.Connection,
    tage_nach_abreise: int = 30,
    heute: Optional[date] = None,
) -> int:
    """Löscht Gäste samt Mail-Log, deren Abreise lange genug zurückliegt.

    Setzt die Löschfrist aus dem Datenschutzkonzept um.

    Args:
        conn: Offene Verbindung.
        tage_nach_abreise: Aufbewahrung in Tagen nach dem Abreisedatum.
        heute: Stichtag (Standard: heute); für Tests überschreibbar.

    Returns:
        Anzahl gelöschter Gäste.
    """
    stichtag = ((heute or date.today()) - timedelta(days=tage_nach_abreise)).isoformat()
    with conn:
        cur = conn.execute("DELETE FROM arrivals WHERE abreise < ?", (stichtag,))
    return cur.rowcount
