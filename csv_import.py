#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
csv_import.py - Liest die tägliche Anreise-CSV des PMS in die Datenbank.

Erwartetes Format (Stand Beispieldatei): Semikolon, UTF-8, Kopfzeile,
Datum als 'TT.MM.JJ HH:MM'. Spalten werden über ihre Überschrift gelesen,
nicht über die Position; zusätzliche Spalten werden ignoriert.

Verhalten:
    * Dieselbe Datei (gleicher SHA-256) wird nie zweimal verarbeitet.
    * Je RegNummer genau ein Gast; erneute Importe aktualisieren ihn.
    * Fehlerhafte Zeilen (keine/ungültige E-Mail, unlesbares Datum) werden
      übersprungen und im import_log vermerkt, der Rest wird importiert.
      Ist keine einzige Zeile lesbar, wird nichts importiert und nichts
      vermerkt (ImportFehler), damit ein verändertes Format auffällt.
    * Fehlen Pflichtspalten, wird nichts importiert (ImportFehler).
    * Eine Datei nur mit Kopfzeile oder ganz ohne Inhalt gilt als Tag ohne
      Anreisen: Sie wird im import_log vermerkt, es werden 0 Gäste importiert.
    * Straße, PLZ und Stadt werden bewusst nicht gespeichert.

Aufruf:
    python csv_import.py <datenbank.sqlite> <datei.csv>
"""

import csv
import hashlib
import io
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from db import init_db, jetzt, verbinde

PFLICHTSPALTEN = ["RegNummer", "Anreise", "Abreise", "Email"]
DATUMSFORMATE = [
    "%d.%m.%y %H:%M", "%d.%m.%Y %H:%M", "%d.%m.%y %H:%M:%S", "%d.%m.%Y %H:%M:%S",
    "%d.%m.%y", "%d.%m.%Y",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
]
EMAIL_MUSTER = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ImportFehler(Exception):
    """Die Datei kann als Ganzes nicht importiert werden."""


@dataclass
class ImportErgebnis:
    """Zusammenfassung eines Importlaufs."""

    bereits_importiert: bool = False
    import_id: Optional[int] = None
    gesamt: int = 0
    neu: int = 0
    aktualisiert: int = 0
    fehler: int = 0
    fehler_details: List[str] = field(default_factory=list)


def _datum(text: str) -> Optional[str]:
    """Wandelt 'TT.MM.JJ HH:MM' (u. ä.) in ein ISO-Datum um, sonst None."""
    text = (text or "").strip()
    for fmt in DATUMSFORMATE:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _zeile_lesen(zeile: dict) -> dict:
    """Prüft und normalisiert eine CSV-Zeile.

    Raises:
        ValueError: Mit lesbarer Begründung, wenn die Zeile unbrauchbar ist.
    """
    reg = (zeile.get("RegNummer") or "").strip()
    if not reg:
        raise ValueError("RegNummer fehlt")
    email = (zeile.get("Email") or "").strip().lower()
    if not EMAIL_MUSTER.match(email):
        raise ValueError(f"ungültige oder fehlende E-Mail ({email or 'leer'})")
    anreise, abreise = _datum(zeile.get("Anreise")), _datum(zeile.get("Abreise"))
    if not anreise or not abreise:
        raise ValueError(
            "Anreise oder Abreise nicht lesbar "
            f"(Anreise={zeile.get('Anreise')!r}, Abreise={zeile.get('Abreise')!r})"
        )
    try:
        personen = int((zeile.get("AnzahlPersonen") or "").strip())
    except ValueError:
        personen = None
    return {
        "reg_nummer": reg,
        "vorname": (zeile.get("Vorname") or "").strip() or None,
        "nachname": (zeile.get("Nachname") or "").strip() or None,
        "email": email,
        "anreise": anreise,
        "abreise": abreise,
        "buchungsdatum": _datum(zeile.get("Buchungsdatum")),
        "anzahl_personen": personen,
        "kanal": (zeile.get("Kanal") or "").strip() or None,
    }


UPSERT = """
INSERT INTO arrivals (reg_nummer, vorname, nachname, email, anreise, abreise,
                      buchungsdatum, anzahl_personen, kanal, import_id,
                      erstmals_importiert, zuletzt_gesehen)
VALUES (:reg_nummer, :vorname, :nachname, :email, :anreise, :abreise,
        :buchungsdatum, :anzahl_personen, :kanal, :import_id, :jetzt, :jetzt)
ON CONFLICT(reg_nummer) DO UPDATE SET
    vorname = excluded.vorname,           nachname = excluded.nachname,
    email = excluded.email,               anreise = excluded.anreise,
    abreise = excluded.abreise,           buchungsdatum = excluded.buchungsdatum,
    anzahl_personen = excluded.anzahl_personen, kanal = excluded.kanal,
    import_id = excluded.import_id,       zuletzt_gesehen = excluded.zuletzt_gesehen
"""


def importiere_csv(conn: sqlite3.Connection, pfad: Path) -> ImportErgebnis:
    """Importiert eine CSV-Datei in einer einzigen Transaktion.

    Args:
        conn: Verbindung zu einer initialisierten Datenbank.
        pfad: Pfad der CSV-Datei.

    Returns:
        ImportErgebnis; bei bereits verarbeiteter Datei mit
        `bereits_importiert=True` und ohne Änderung an der Datenbank.

    Raises:
        ImportFehler: Datei nicht lesbar/dekodierbar oder Pflichtspalten fehlen.
    """
    rohdaten = Path(pfad).read_bytes()
    pruefsumme = hashlib.sha256(rohdaten).hexdigest()

    # Altlast aus früheren Versionen: Eine Datei, bei der keine einzige Zeile
    # lesbar war, darf nicht als "bereits importiert" gelten, sonst gingen die
    # Gäste verloren. Solche Einträge werden verworfen und die Datei neu gelesen.
    with conn:
        conn.execute(
            "DELETE FROM import_log WHERE sha256 = ? AND zeilen_gesamt > 0"
            " AND zeilen_fehler = zeilen_gesamt",
            (pruefsumme,),
        )

    vorhanden = conn.execute(
        "SELECT id FROM import_log WHERE sha256 = ?", (pruefsumme,)
    ).fetchone()
    if vorhanden:
        return ImportErgebnis(bereits_importiert=True, import_id=vorhanden["id"])

    try:
        text = rohdaten.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportFehler(f"Datei ist nicht UTF-8-kodiert: {exc}") from exc

    leser = csv.DictReader(io.StringIO(text), delimiter=";")
    kopf = [(s or "").strip() for s in (leser.fieldnames or [])]
    leser.fieldnames = kopf
    fehlend = [s for s in PFLICHTSPALTEN if s not in kopf]
    if fehlend and text.strip():  # völlig leere Datei = Tag ohne Anreisen
        raise ImportFehler(f"Pflichtspalten fehlen: {', '.join(fehlend)}")

    ergebnis = ImportErgebnis()
    zeitpunkt = jetzt()
    with conn:
        cur = conn.execute(
            "INSERT INTO import_log (dateiname, sha256, importiert_am) VALUES (?, ?, ?)",
            (Path(pfad).name, pruefsumme, zeitpunkt),
        )
        ergebnis.import_id = cur.lastrowid

        for zeile in leser:
            if not any((wert or "").strip() for wert in zeile.values()):
                continue  # Leerzeile, z. B. am Dateiende
            ergebnis.gesamt += 1
            try:
                daten = _zeile_lesen(zeile)
            except ValueError as exc:
                ergebnis.fehler += 1
                ergebnis.fehler_details.append(f"Zeile {leser.line_num}: {exc}")
                continue

            existiert = conn.execute(
                "SELECT 1 FROM arrivals WHERE reg_nummer = ?", (daten["reg_nummer"],)
            ).fetchone()
            conn.execute(UPSERT, {**daten, "import_id": ergebnis.import_id, "jetzt": zeitpunkt})
            if existiert:
                ergebnis.aktualisiert += 1
            else:
                ergebnis.neu += 1

        if ergebnis.gesamt and ergebnis.fehler == ergebnis.gesamt:
            # Kein einziger Gast lesbar: vermutlich ein verändertes Dateiformat.
            # Exception => Rollback, die Datei wird nicht als importiert vermerkt.
            beispiele = "; ".join(ergebnis.fehler_details[:3])
            raise ImportFehler(f"Alle {ergebnis.gesamt} Zeilen fehlerhaft, z. B. {beispiele}")

        conn.execute(
            "UPDATE import_log SET zeilen_gesamt=?, zeilen_neu=?, zeilen_aktualisiert=?,"
            " zeilen_fehler=?, fehler_details=? WHERE id=?",
            (ergebnis.gesamt, ergebnis.neu, ergebnis.aktualisiert, ergebnis.fehler,
             "\n".join(ergebnis.fehler_details) or None, ergebnis.import_id),
        )
    return ergebnis


def main(argv: List[str]) -> int:
    """Kommandozeile: legt die DB bei Bedarf an und importiert die CSV."""
    if len(argv) != 3:
        print("Aufruf: python csv_import.py <datenbank.sqlite> <datei.csv>")
        return 2
    conn = verbinde(Path(argv[1]))
    try:
        init_db(conn)
        erg = importiere_csv(conn, Path(argv[2]))
    except ImportFehler as exc:
        print(f"Import abgebrochen: {exc}")
        return 1
    finally:
        conn.close()
    if erg.bereits_importiert:
        print("Datei wurde schon importiert - nichts zu tun.")
    else:
        print(f"{erg.gesamt} Zeilen: {erg.neu} neu, {erg.aktualisiert} aktualisiert, {erg.fehler} Fehler")
        for meldung in erg.fehler_details:
            print("  -", meldung)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
