#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests für den CSV-Import. Aufruf: python test_csv_import.py <beispiel.csv>"""

import sys
import tempfile
from datetime import date
from pathlib import Path

from csv_import import ImportFehler, importiere_csv
from db import init_db, loesche_alte_gaeste, verbinde


def main(beispiel: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        conn = verbinde(tmp / "test.sqlite")
        init_db(conn)

        # 1. Beispieldatei: 6 Gäste, keine Fehler
        e = importiere_csv(conn, beispiel)
        assert (e.gesamt, e.neu, e.aktualisiert, e.fehler) == (6, 6, 0, 0), e
        z = conn.execute("SELECT * FROM arrivals WHERE reg_nummer='12160'").fetchone()
        assert z["anreise"] == "2026-10-08" and z["abreise"] == "2026-10-09", dict(z)
        assert z["kanal"] == "BHC" and z["email"] == "chang@guest.booking.com"
        assert conn.execute("SELECT kanal FROM arrivals WHERE reg_nummer='13161'").fetchone()[0] is None
        assert conn.execute("SELECT anzahl_personen FROM arrivals WHERE reg_nummer='13065'").fetchone()[0] == 2

        # 2. Dieselbe Datei nochmal: wird übersprungen
        e = importiere_csv(conn, beispiel)
        assert e.bereits_importiert and conn.execute("SELECT COUNT(*) FROM import_log").fetchone()[0] == 1

        # 3. Geänderte Datei: Update, neue Zeile, kaputte Zeilen, Leerzeile, Zusatzspalte
        kopf = "RegNummer;Anreise;Abreise;Buchungsdatum;AnzahlPersonen;Vorname;Nachname;Email;Kanal;Neu\r\n"
        zeilen = [
            "13161;08.10.26 00:00;14.10.26 00:00;27.09.26;1;Hans;Asdf;HANS@libero.it;;x\r\n",   # Abreise + Mail geändert
            "99999;10.10.26 00:00;11.10.26 00:00;01.10.26;3;Neu;Gast;neu@example.com;EHC;x\r\n",  # neu
            "88888;10.10.26 00:00;11.10.26 00:00;01.10.26;1;Kein;Mail;;BHC;x\r\n",               # E-Mail fehlt
            "77777;kaputt;11.10.26 00:00;01.10.26;1;Falsches;Datum;a@b.de;;x\r\n",               # Datum kaputt
            "\r\n",
        ]
        datei = tmp / "zweite.csv"
        datei.write_text(kopf + "".join(zeilen), encoding="utf-8")
        e = importiere_csv(conn, datei)
        assert (e.gesamt, e.neu, e.aktualisiert, e.fehler) == (4, 1, 1, 2), e
        z = conn.execute("SELECT * FROM arrivals WHERE reg_nummer='13161'").fetchone()
        assert z["abreise"] == "2026-10-14" and z["email"] == "hans@libero.it"
        assert conn.execute("SELECT COUNT(*) FROM arrivals").fetchone()[0] == 7
        assert "Zeile 4" in e.fehler_details[0] and "Zeile 5" in e.fehler_details[1], e.fehler_details

        # 4. Pflichtspalte fehlt: Abbruch ohne Spuren
        schlecht = tmp / "schlecht.csv"
        schlecht.write_text("RegNummer;Anreise;Abreise\r\n1;08.10.26;09.10.26\r\n", encoding="utf-8")
        try:
            importiere_csv(conn, schlecht)
            raise AssertionError("ImportFehler erwartet")
        except ImportFehler as exc:
            assert "Email" in str(exc)
        assert conn.execute("SELECT COUNT(*) FROM import_log").fetchone()[0] == 2

        # 5. Latin-1 statt UTF-8: sauberer Fehler
        latin = tmp / "latin.csv"
        latin.write_bytes("RegNummer;Anreise;Abreise;Email\r\n1;08.10.26;09.10.26;a@b.de;Fällanden\r\n".encode("latin-1"))
        try:
            importiere_csv(conn, latin)
            raise AssertionError("ImportFehler erwartet")
        except ImportFehler as exc:
            assert "UTF-8" in str(exc)

        # 5b. Kein Gast lesbar (z. B. unbekanntes Datumsformat): Fehler, kein import_log-Eintrag
        vorher = conn.execute("SELECT COUNT(*) FROM import_log").fetchone()[0]
        unlesbar = tmp / "unlesbar.csv"
        unlesbar.write_text(
            "RegNummer;Anreise;Abreise;Email\r\n1;Okt 8 2026;Okt 9 2026;a@b.de\r\n2;Okt 8 2026;Okt 9 2026;c@d.de\r\n",
            encoding="utf-8")
        try:
            importiere_csv(conn, unlesbar)
            raise AssertionError("ImportFehler erwartet")
        except ImportFehler as exc:
            assert "Alle 2 Zeilen" in str(exc) and "Okt 8 2026" in str(exc), exc
        assert conn.execute("SELECT COUNT(*) FROM import_log").fetchone()[0] == vorher

        # 5b2. Alter Fehleintrag (alle Zeilen fehlerhaft) blockiert eine Neuverarbeitung nicht
        import hashlib
        pruef = hashlib.sha256((tmp / "neu.csv").write_bytes(
            b"RegNummer;Anreise;Abreise;Email\r\n401;08.10.26 00:00;09.10.26 00:00;g@h.de\r\n") and
            (tmp / "neu.csv").read_bytes()).hexdigest()
        with conn:
            conn.execute("INSERT INTO import_log (dateiname, sha256, importiert_am, zeilen_gesamt, zeilen_fehler)"
                         " VALUES ('alt.csv', ?, '2026-10-08 17:59:04', 1, 1)", (pruef,))
        e = importiere_csv(conn, tmp / "neu.csv")
        assert not e.bereits_importiert and e.neu == 1, e

        # 5c. Weitere Datumsformate: mit Sekunden, 4-stelliges Jahr, ISO
        formate = tmp / "formate.csv"
        formate.write_text(
            "RegNummer;Anreise;Abreise;Email\r\n"
            "301;08.10.2026 00:00:00;09.10.2026 00:00:00;a@b.de\r\n"
            "302;08.10.26 00:00:00;09.10.26 00:00:00;c@d.de\r\n"
            "303;2026-10-08 00:00:00;2026-10-09;e@f.de\r\n", encoding="utf-8")
        e = importiere_csv(conn, formate)
        assert (e.neu, e.fehler) == (3, 0), e
        assert conn.execute("SELECT anreise FROM arrivals WHERE reg_nummer='303'").fetchone()[0] == "2026-10-08"

        # 6. Löschfrist inkl. Mail-Log (CASCADE)
        aid = conn.execute("SELECT id FROM arrivals WHERE reg_nummer='12160'").fetchone()[0]
        with conn:
            conn.execute("INSERT INTO mail_log (arrival_id, vorlage, erstellt_am) VALUES (?, 'pre_arrival', '2026-10-06 08:00:00')", (aid,))
        try:
            with conn:
                conn.execute("INSERT INTO mail_log (arrival_id, vorlage, erstellt_am) VALUES (?, 'pre_arrival', 'x')", (aid,))
            raise AssertionError("Doppelter Mail-Log-Eintrag hätte scheitern müssen")
        except Exception as exc:
            assert "UNIQUE" in str(exc)
        assert loesche_alte_gaeste(conn, 30, heute=date(2026, 10, 20)) == 0
        n = loesche_alte_gaeste(conn, 30, heute=date(2026, 11, 20))  # Abreise < 21.10.
        assert n == 11, n
        assert conn.execute("SELECT COUNT(*) FROM mail_log").fetchone()[0] == 0
        conn.close()
    print("Alle Tests bestanden.")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
