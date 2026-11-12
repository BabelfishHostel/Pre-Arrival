#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_send.py - Versand-Job: schickt die Pre-Arrival-Mail an fällige Gäste.

Modi (Standard ist der sichere Trockenlauf):
    python run_send.py                    Trockenlauf: rendert, schreibt Vorschauen, sendet NICHTS
    python run_send.py --test-an a@b.de   sendet ALLE fälligen Mails an diese Testadresse (kein Log)
    python run_send.py --live             echter Versand an die Gäste, Protokoll in mail_log

Wer ist fällig: Anreise zwischen heute und heute + SEND_TAGE_VORHER (Standard 3),
und für die Vorlage gibt es noch keinen 'gesendet'/'uebersprungen'-Eintrag.
Fehlgeschlagene Mails ('fehler') werden beim nächsten Lauf erneut versucht
(höchstens MAX_VERSUCHE). Ein Eintrag, der bei 'geplant' hängen blieb (Absturz
mitten im Versand), wird NICHT automatisch wiederholt, um Doppelmails zu vermeiden.

Exit-Code: 0 ok, 1 mindestens ein Fehler, 2 Konfiguration fehlt.
"""

import argparse
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from db import init_db, jetzt, verbinde
from events import EventQuelle
from graph_mail import VersandFehler, baue_mime, hole_graph_token, sende_mime
from render import MailDaten, render_mail, zahlung_variante
from run_ingest import STANDARD_ENV, lade_env, pfad_aus_env

log = logging.getLogger("run_send")

VORLAGE = "pre_arrival_v1"
MAX_VERSUCHE = 3
EMAIL_MUSTER = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class SendKonfig:
    absender: str = "info@babelfish-hostel.de"
    postfach: str = ""                      # Graph-Postfach, Standard = absender
    tage_vorher: int = 3
    sprache_standard: str = "de"
    storno_tage: int = 2                    # kostenfreie Stornierung bis N Tage vor Anreise
    vorschau_dir: Optional[Path] = None


@dataclass
class SendErgebnis:
    gesendet: int = 0
    vorschau: int = 0
    uebersprungen: int = 0
    fehler: int = 0
    details: List[str] = field(default_factory=list)


def faellige_gaeste(conn: sqlite3.Connection, heute: date, tage: int, nur_ungesendet: bool = True):
    """Gäste mit Anreise in [heute, heute+tage] ohne abgeschlossenen Versand."""
    bis = date.fromordinal(heute.toordinal() + tage).isoformat()
    sql = """
        SELECT a.*, m.status AS m_status, COALESCE(m.versuche, 0) AS m_versuche
        FROM arrivals a LEFT JOIN mail_log m ON m.arrival_id = a.id AND m.vorlage = ?
        WHERE a.anreise BETWEEN ? AND ?
    """
    zeilen = conn.execute(sql + " ORDER BY a.anreise, a.id", (VORLAGE, heute.isoformat(), bis)).fetchall()
    if not nur_ungesendet:
        return zeilen
    return [z for z in zeilen if z["m_status"] in (None, "geplant", "fehler")]


def zahlung_hinweis(z: sqlite3.Row) -> Optional[str]:
    """Hinweistext (ohne Personendaten) bei widersprüchlichen Zahlungsdaten, sonst None.

    Die Mail geht in jedem Fall raus; die Entscheidung über den Zahlungsbereich fällt allein am Paylink.
    """
    rest = z["restbetrag"] or 0
    if z["paylink"] and rest <= 0:
        return "Paylink, aber Restbetrag 0 - Zahlungsbereich wird trotzdem angezeigt"
    if not z["paylink"] and rest > 0:
        return "Restbetrag offen, aber kein Paylink - Zahlungsbereich entfällt"
    return None


def mail_daten(z: sqlite3.Row, k: SendKonfig, events: EventQuelle, heute: date) -> MailDaten:
    sprache = z["sprache"] if z["sprache"] in ("de", "en") else k.sprache_standard
    anreise = date.fromisoformat(z["anreise"])
    return MailDaten(
        vorname=z["vorname"], buchungsnummer=z["buchungsnummer"] or z["reg_nummer"],
        anreise=anreise, abreise=date.fromisoformat(z["abreise"]), personen=z["anzahl_personen"],
        sprache=sprache, heute=heute,
        events_html=events.html(anreise, sprache),
        paymentlink=z["paylink"],
        voraus_datum=date.fromisoformat(z["vorausdatum"]) if z["vorausdatum"] else None,
        kanal=(z["kanal"] or "").strip().upper() or None, rate=z["rate"],
        schon_eingegangen=z["schon_eingegangen"],
        storno_tage=k.storno_tage,
    )


def _log_planen(conn, arrival_id: int) -> None:
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO mail_log (arrival_id, vorlage, status, versuche, erstellt_am) "
            "VALUES (?, ?, 'geplant', 0, ?)", (arrival_id, VORLAGE, jetzt()))


def _log_setzen(conn, arrival_id: int, status: str, message_id: Optional[str] = None,
                fehler: Optional[str] = None) -> None:
    with conn:
        conn.execute(
            "UPDATE mail_log SET status=?, message_id=?, fehler=?, "
            "gesendet_am=CASE WHEN ?='gesendet' THEN ? ELSE gesendet_am END "
            "WHERE arrival_id=? AND vorlage=?",
            (status, message_id, fehler, status, jetzt(), arrival_id, VORLAGE))


def verarbeite(conn: sqlite3.Connection, k: SendKonfig, events: EventQuelle, heute: date,
               modus: str, sender: Optional[Callable] = None, test_an: Optional[str] = None) -> SendErgebnis:
    """Rendert und versendet für alle fälligen Gäste.

    Args:
        modus: 'dry' (nichts senden), 'test' (an test_an, kein Log), 'live'.
        sender: Funktion (MIME-Nachricht) -> Message-ID; nötig bei 'test'/'live'.
    """
    erg = SendErgebnis()
    for z in faellige_gaeste(conn, heute, k.tage_vorher):
        ref = f"Gast {z['reg_nummer']} (Anreise {z['anreise']})"
        if modus == "live" and z["m_status"] == "geplant" and z["m_versuche"] > 0:
            erg.uebersprungen += 1
            erg.details.append(f"{ref}: Versand blieb zuvor unklar bei 'geplant' - bitte manuell prüfen")
            log.warning("%s: unklarer früherer Versand, nicht automatisch wiederholt", ref)
            continue
        if modus == "live" and z["m_status"] == "fehler" and z["m_versuche"] >= MAX_VERSUCHE:
            continue
        if not EMAIL_MUSTER.match(z["email"] or ""):
            if modus == "live":
                _log_planen(conn, z["id"])
                _log_setzen(conn, z["id"], "uebersprungen", fehler="ungültige E-Mail-Adresse")
            erg.uebersprungen += 1
            erg.details.append(f"{ref}: ungültige E-Mail-Adresse")
            continue
        hinweis = zahlung_hinweis(z)
        if hinweis:
            erg.details.append(f"{ref}: {hinweis}")
            log.warning("%s: %s", ref, hinweis)
        try:
            d = mail_daten(z, k, events, heute)
            mail = render_mail(d)
        except Exception as exc:
            erg.fehler += 1
            erg.details.append(f"{ref}: Rendern fehlgeschlagen: {exc}")
            log.error("%s: Rendern fehlgeschlagen: %s", ref, exc)
            if modus == "live":
                _log_planen(conn, z["id"])
                _log_setzen(conn, z["id"], "fehler", fehler=f"Rendern: {exc}")
            continue

        if modus == "dry":
            erg.vorschau += 1
            if k.vorschau_dir:
                k.vorschau_dir.mkdir(parents=True, exist_ok=True)
                (k.vorschau_dir / f"{z['reg_nummer']}_{d.sprache}.html").write_text(mail["html"], encoding="utf-8")
            log.info("TROCKENLAUF %s: %s, Zahlung=%s", ref, d.sprache.upper(), zahlung_variante(d) or "nein")
            continue

        empfaenger = test_an if modus == "test" else z["email"]
        msg = baue_mime(k.absender, empfaenger, mail["betreff"], mail["html"], mail["text"])
        if modus == "live":
            _log_planen(conn, z["id"])
            with conn:  # Versuch VOR dem Senden zählen, damit ein Absturz erkennbar bleibt
                conn.execute("UPDATE mail_log SET versuche = versuche + 1 WHERE arrival_id=? AND vorlage=?",
                             (z["id"], VORLAGE))
        try:
            mid = sender(msg)
        except VersandFehler as exc:
            erg.fehler += 1
            erg.details.append(f"{ref}: {exc}")
            log.error("%s: Versand fehlgeschlagen: %s", ref, exc)
            if modus == "live":
                _log_setzen(conn, z["id"], "uebersprungen" if exc.dauerhaft else "fehler", fehler=str(exc)[:500])
            continue
        erg.gesendet += 1
        if modus == "live":
            _log_setzen(conn, z["id"], "gesendet", message_id=mid)
        log.info("%s: gesendet%s", ref, " (TEST)" if modus == "test" else "")
    return erg


def lese_konfig() -> SendKonfig:
    e = os.environ.get
    k = SendKonfig(
        absender=e("SEND_ABSENDER", "info@babelfish-hostel.de"),
        tage_vorher=int(e("SEND_TAGE_VORHER", "3")),
        sprache_standard=e("SPRACHE_STANDARD", "de").lower(),
        storno_tage=int(e("STORNO_TAGE", "2")),
    )
    k.postfach = e("GRAPH_POSTFACH", k.absender)
    if k.sprache_standard not in ("de", "en"):
        raise RuntimeError("SPRACHE_STANDARD muss de oder en sein.")
    return k


def main(argv: Optional[List[str]] = None, env_pfad: Optional[Path] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="echter Versand an die Gäste")
    ap.add_argument("--test-an", metavar="ADRESSE", help="alle fälligen Mails an diese Adresse senden")
    ap.add_argument("--vorschau", metavar="ORDNER", help="Trockenlauf: HTML-Vorschauen hierhin schreiben")
    ap.add_argument("--heute", metavar="JJJJ-MM-TT", help="Stichtag überschreiben (Test)")
    a = ap.parse_args(argv)
    if a.live and a.test_an:
        ap.error("--live und --test-an schließen sich aus")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    lade_env(env_pfad or STANDARD_ENV)
    modus = "live" if a.live else "test" if a.test_an else "dry"
    try:
        k = lese_konfig()
        db_pfad = pfad_aus_env(os.environ["DB_PATH"])
        sender = None
        if modus != "dry":
            tenant, cid, sec = (os.environ.get(n, "").strip() for n in
                                ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"))
            if not (tenant and cid and sec):
                raise RuntimeError("AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET fehlen.")
            token = hole_graph_token(tenant, cid, sec)
            sender = lambda msg: sende_mime(msg, k.postfach, token())  # noqa: E731
    except (RuntimeError, KeyError) as exc:
        log.error("Konfiguration unvollständig: %s", exc)
        return 2
    if a.vorschau:
        k.vorschau_dir = Path(a.vorschau)

    events = EventQuelle(os.environ.get("EVENTS_SHEET_ID", ""), os.environ.get("EVENTSEITE_URL", ""),
                         os.environ.get("EVENTS_TAB", "Veranstaltungen"))
    heute = date.fromisoformat(a.heute) if a.heute else date.today()
    conn = verbinde(db_pfad)
    try:
        init_db(conn)
        erg = verarbeite(conn, k, events, heute, modus, sender, a.test_an)
    finally:
        conn.close()
    log.info("Modus %s: %d gesendet, %d Vorschau, %d übersprungen, %d Fehler",
             modus, erg.gesendet, erg.vorschau, erg.uebersprungen, erg.fehler)
    for zeile in erg.details:
        log.warning(zeile)
    return 1 if erg.fehler else 0


if __name__ == "__main__":
    sys.exit(main())
