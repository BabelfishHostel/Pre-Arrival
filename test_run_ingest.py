#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test des Ingest-Jobs gegen einen simulierten IMAP-Server (kein Netzwerk)."""

import imaplib
import os
import tempfile
from email.message import EmailMessage
from pathlib import Path

import run_ingest
from db import verbinde

KOPF = "RegNummer;Anreise;Abreise;Buchungsdatum;AnzahlPersonen;Vorname;Nachname;Email;Kanal\r\n"
ZEILE = "{n};08.10.26 00:00;09.10.26 00:00;01.10.26;1;A;B;gast{n}@example.com;BHC\r\n"


class Server:
    """Gemeinsamer Zustand aller Fake-Verbindungen."""
    nachrichten = {}   # nummer -> {"raw", "ordner", "geloescht"}
    verbindungen_offen = 0


class FakeIMAP:
    def __init__(self, host, port):
        Server.verbindungen_offen += 1
        self.kopiert = {}

    def login(self, user, pw):
        return "OK", []

    def authenticate(self, mechanismus, callback):
        assert mechanismus == "XOAUTH2" and b"auth=Bearer tok" in callback(None)
        return "OK", []

    def select(self, box):
        return "OK", [b"1"]

    def search(self, charset, krit):
        nums = sorted(n for n, m in Server.nachrichten.items() if m["ordner"] == "INBOX")
        return "OK", [b" ".join(str(n).encode() for n in nums)]

    def fetch(self, num, spec):
        raw = Server.nachrichten[int(num)]["raw"]
        return "OK", [(b"1 (BODY[] {%d}" % len(raw), raw), b")"]

    def copy(self, num, ordner):
        if " " in ordner and not ordner.startswith('"'):  # wie Exchange: Name nicht gequotet
            raise imaplib.IMAP4.error("COPY command error: BAD [b'Command Argument Error. 12']")
        self.kopiert[int(num)] = ordner.strip('"')
        return "OK", []

    def create(self, ordner):
        if " " in ordner and not ordner.startswith('"'):
            raise imaplib.IMAP4.error("CREATE command error: BAD [b'Command Argument Error. 12']")
        return "OK", []

    def store(self, num, op, flag):
        Server.nachrichten[int(num)]["geloescht"] = True
        return "OK", []

    def expunge(self):
        for n, m in Server.nachrichten.items():
            if m.get("geloescht") and m["ordner"] == "INBOX":
                m["ordner"] = self.kopiert.get(n, "GELOESCHT")
        return "OK", []

    def logout(self):
        Server.verbindungen_offen -= 1
        return "BYE", []


def mail(nummer, csv_text):
    m = EmailMessage()
    m["From"], m["Subject"] = "pms@example.com", "Anreise in 2 Tagen"
    m.set_content("siehe Anhang")
    m.add_attachment(csv_text.encode("utf-8"), maintype="text", subtype="csv", filename="anreise.csv")
    Server.nachrichten[nummer] = {"raw": m.as_bytes(), "ordner": "INBOX"}


def ordner(nummer):
    return Server.nachrichten[nummer]["ordner"]


def main():
    imaplib.IMAP4_SSL = FakeIMAP
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        os.environ.update({
            "IMAP_SERVER": "x", "IMAP_USER": "u", "IMAP_PASSWORD": "p",
            "ABSENDER_FILTER": "pms@example.com", "BETREFF_FILTER": "Anreise",
            "DB_PATH": str(tmp / "t.sqlite"), "DOWNLOAD_DIR": str(tmp / "dl"),
        })
        zaehle = lambda t: verbinde(tmp / "t.sqlite").execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

        # 1. Zwei Mails: eine mit Gästen, die neuere nur mit Kopfzeile (Tag ohne Anreisen)
        mail(1, KOPF + ZEILE.format(n=101) + ZEILE.format(n=102))
        mail(2, KOPF)
        assert run_ingest.main(tmp / "keine.env") == 0
        assert ordner(1) == "Verarbeitet" and ordner(2) == "Verarbeitet"
        assert zaehle("arrivals") == 2 and zaehle("import_log") == 2
        assert not list((tmp / "dl").glob("*")), "CSV muss nach dem Import gelöscht sein"
        assert Server.verbindungen_offen == 0

        # 2. Leerer Lauf
        assert run_ingest.main(tmp / "keine.env") == 0

        # 3. Dieselbe CSV nochmal zugestellt: kein Doppelimport, Mail wird trotzdem abgeschlossen
        mail(3, KOPF + ZEILE.format(n=101) + ZEILE.format(n=102))
        assert run_ingest.main(tmp / "keine.env") == 0
        assert ordner(3) == "Verarbeitet" and zaehle("arrivals") == 2 and zaehle("import_log") == 2

        # 4. Völlig leere CSV (nur Zeilenumbruch, Tag ohne Anreisen) und kaputte CSV
        mail(4, "\r\n")
        mail(5, "Foo;Bar\r\n1;2\r\n")
        assert run_ingest.main(tmp / "keine.env") == 1       # neueste (5) ist kaputt
        assert ordner(5) == "INBOX" and ordner(4) == "INBOX"  # bleibt liegen, Lauf bricht ab
        assert Server.verbindungen_offen == 0

        # 5. Kaputte Mail entfernt: leere Datei wird als Tag ohne Anreisen verarbeitet
        Server.nachrichten[5]["ordner"] = "WEG"
        assert run_ingest.main(tmp / "keine.env") == 0
        assert ordner(4) == "Verarbeitet"

        # 6. Fehlende Konfiguration
        del os.environ["IMAP_PASSWORD"]
        assert run_ingest.main(tmp / "keine.env") == 2

        # 7. OAuth2 (Microsoft 365): Token statt Passwort
        os.environ.update({"AZURE_TENANT_ID": "t", "AZURE_CLIENT_ID": "c", "AZURE_CLIENT_SECRET": "s"})
        run_ingest.hole_access_token_msal = lambda: "tok"
        mail(6, KOPF + ZEILE.format(n=201))
        assert run_ingest.main(tmp / "keine.env") == 0
        assert ordner(6) == "Verarbeitet" and zaehle("arrivals") == 3

        # 7b. Ordnername mit Leerzeichen (Exchange verlangt Anführungszeichen)
        os.environ["VERARBEITET_ORDNER"] = "Anreise in 2 Tagen"
        mail(8, KOPF + ZEILE.format(n=202))
        assert run_ingest.main(tmp / "keine.env") == 0
        assert ordner(8) == "Anreise in 2 Tagen" and zaehle("arrivals") == 4
        os.environ["VERARBEITET_ORDNER"] = "Fehler äöü"
        assert run_ingest.main(tmp / "keine.env") == 2   # Umlaute nicht unterstützt
        del os.environ["VERARBEITET_ORDNER"]

        # 8. Token-Fehler (z. B. falsches Secret): sauberer Fehler, Exit-Code 1
        def kaputt():
            raise RuntimeError("Token nicht ausgestellt (invalid_client)")
        run_ingest.hole_access_token_msal = kaputt
        mail(7, KOPF)
        assert run_ingest.main(tmp / "keine.env") == 1 and ordner(7) == "INBOX"
    print("Alle Tests bestanden.")


if __name__ == "__main__":
    main()
