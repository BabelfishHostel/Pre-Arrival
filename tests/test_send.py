#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests für Rendern, Events und Versand-Job. Aufruf: python test_send.py"""

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "src"))

import base64
import email
import json
import tempfile
import threading
from datetime import date, timedelta
from email import policy
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import events as ev
from db import init_db, verbinde
from graph_mail import VersandFehler, baue_mime, sende_mime
from render import (MailDaten, anrede, betrag_text, datum_lang, gaeste_text, naechte_text,
                    render_mail, zahlung_aktiv, zahlung_variante, storno_variante)
from run_send import SendKonfig, VORLAGE, verarbeite

HEUTE = date(2026, 10, 7)
ANREISE = date(2026, 10, 9)


def daten(**kw) -> MailDaten:
    basis = dict(vorname="Nico", buchungsnummer="410200-20-12058", anreise=ANREISE, abreise=date(2026, 10, 11),
                 personen=4, sprache="de", rate="flex", kanal="EGB", heute=HEUTE, events_html="<p>EVT</p>",
                 betrag=528.0, gesamtbetrag=528.0, paymentlink="https://pay.example/x?a=1&b=2")
    basis.update(kw)
    return MailDaten(**basis)


def test_format():
    assert datum_lang(ANREISE, "de") == "Fr, 9. Okt 2026" and datum_lang(ANREISE, "en") == "Fri, 9 Oct 2026"
    assert betrag_text(528, "de") == "528,00 EUR" and betrag_text(528, "en") == "EUR 528.00"
    assert betrag_text(1528.5, "de") == "1.528,50 EUR"
    assert naechte_text(2, "de") == "2 Nächte" and naechte_text(1, "en") == "1 night"
    assert gaeste_text(1, "de") == "1 Gast" and gaeste_text(4, "en") == "4 guests"
    assert anrede("Ruth - Edith", "de") == "Hallo Ruth," and anrede("ANNA-LENA", "en") == "Hi Anna-Lena,"
    for leer in (None, "", "  ", "Zimmer 12", "4711"):
        assert anrede(leer, "de") == "Hallo zusammen," and anrede(leer, "en") == "Hi everyone,"
    assert anrede("<b>x", "de") == "Hallo &lt;b&gt;x,"


def test_bloecke():
    # "Stimmt alles?": fester Teil bleibt, Storno-Satz richtet sich nach dem Versandtag (Frist = Anreise - 2 Tage)
    fest = {"de": ("Schau kurz drüber, ob Datum und Personenzahl passen.", "Falls Du nicht kommen kannst, sag uns bitte kurz Bescheid &ndash; dann wissen wir Bescheid"),
            "en": ("check that the dates and the number of guests are correct", "If you can't make it, please let us know")}
    satz = {"de": {"morgen": "läuft morgen ab", "heute": "läuft heute ab", "datum": "läuft am <strong>Mi, 7. Okt</strong> ab"},
            "en": {"morgen": "ends tomorrow", "heute": "ends today", "datum": "ends on <strong>Wed, 7 Oct</strong>"}}
    for tage_vor, erwartet in ((4, "datum"), (3, "morgen"), (2, "heute"), (1, None), (0, None)):
        d0 = daten(heute=ANREISE - timedelta(days=tage_vor))
        assert storno_variante(d0) == erwartet, tage_vor
        for sprache in ("de", "en"):
            h = render_mail(daten(heute=d0.heute, sprache=sprache))["html"]
            assert all(t in h for t in fest[sprache]), (tage_vor, sprache)
            for name, t in satz[sprache].items():
                assert (t in h) == (name == erwartet), (tage_vor, sprache, name)
            assert "{{" not in h and "BLOCK:" not in h and "90 %" not in h and "90&nbsp;%" not in h
    # Zahlungsvarianten: nur EGB + FLEX unterscheidet Anzahlung / Rest, alle anderen mit Paylink: gesamt
    texte = {"anzahlung": ("Für Deine Buchung ist eine Anzahlung fällig und wurde noch nicht vorgenommen. Bitte benutze dazu folgenden Bezahllink, ansonsten können wir die Buchung nicht garantieren.",
                           "A deposit is due for your booking and has not been paid yet. Please use the following payment link, otherwise we cannot guarantee your booking."),
             "rest": ("Du hast bereits eine Anzahlung geleistet, Deine Buchung ist garantiert. Mit folgendem Bezahllink kannst Du den Restbetrag bezahlen.",
                      "You have already paid a deposit, so your booking is guaranteed. You can use the following payment link to pay the remaining balance."),
             "gesamt": ("Deine Buchung ist garantiert. Mit folgendem Bezahllink kannst Du die Buchung im Voraus bezahlen.",
                        "Your booking is guaranteed. You can use the following payment link to pay for your booking in advance.")}
    faelle = {
        "anzahlung": [dict(), dict(betrag=700.0, gesamtbetrag=528.0)],          # nichts bezahlt (auch bei amou1 > total)
        "rest": [dict(betrag=300.0, gesamtbetrag=528.0)],
        "gesamt": [dict(kanal="BHC"), dict(kanal=None, betrag=None, gesamtbetrag=None), dict(kanal=""),
                   dict(rate="fix"), dict(rate=None), dict(kanal="BHC", rate="flex"),
                   dict(rate="fix", betrag=300.0, gesamtbetrag=528.0)],
    }
    for variante, liste in faelle.items():
        for kw in liste:
            assert zahlung_variante(daten(**kw)) == variante, (variante, kw)
            for i, sprache in enumerate(("de", "en")):
                h = render_mail(daten(sprache=sprache, **kw))["html"]
                assert texte[variante][i] in h and "pay.example" in h and "a=1&amp;b=2" in h, (variante, kw, sprache)
                for andere, t in texte.items():
                    assert andere == variante or t[i] not in h
                assert (("AGB" in h or "General Terms" in h) == (variante == "anzahlung")), (variante, kw, sprache)
                assert "{{" not in h and "BLOCK:" not in h and "528" not in h   # Betrag steht nicht in der Mail
    # Kein Paylink -> kein Zahlungsbereich; EGB+FLEX ohne Betrag/total ist nicht entscheidbar -> kein Bereich
    for kw in ({"betrag": None}, {"gesamtbetrag": None}, {"paymentlink": None}, {"paymentlink": ""}):
        d = daten(**kw)
        assert not zahlung_aktiv(d) and "Jetzt bezahlen" not in render_mail(d)["html"], kw
    # ohne Total/Betrag, aber nicht EGB+FLEX: Paylink genügt
    assert zahlung_variante(daten(kanal="BHC", betrag=None, gesamtbetrag=None)) == "gesamt"
    # Keine Marker/Platzhalter/Entwicklerkommentar im Ergebnis
    for r in ("flex", "fix"):
        for s in ("de", "en"):
            m = render_mail(daten(rate=r, sprache=s))
            assert "<!--" not in m["html"] and "Elemente sonst" not in m["text"]
            assert "BLOCK:" not in m["html"] and "{{" not in m["html"] and "HINWEISE" not in m["html"]
            assert "EVT" in m["html"] and "410200-20-12058" in m["html"] and m["text"].strip()
    en = render_mail(daten(sprache="en"))
    for sprache in ("de", "en"):  # Logo wie in der Bestätigungsmail (funktionierende Adresse)
        h = render_mail(daten(sprache=sprache))["html"]
        assert 'src="https://hostels.assd.com/img/email/410200-logo-quer-invers.png"' in h and "babelfish-hostel.de/images" not in h
    assert en["betreff"].startswith("Your arrival") and "Fri, 9 Oct 2026" in en["html"] and "4 guests" in en["html"]
    assert render_mail(daten())["betreff"].startswith("Deine Anreise")
    assert "Hallo Nico," in render_mail(daten())["text"] or "Hallo Nico" in render_mail(daten())["text"]


SHEET = (
    "Status,Startdatum,Enddatum,Uhrzeit,Titel DE,Titel EN,Ort,Kategorie\n"
    "freigegeben,09.10.2026,,20:00,Jazzabend,Jazz night,Cairo,Konzert\n"
    "freigegeben,09.10.2026,,20:00,Jazzabend,Jazz night,Cairo,Konzert\n"          # Dublette
    "entwurf,09.10.2026,,19:00,Nicht frei,,Posthalle,Konzert\n"                    # nicht freigegeben
    "freigegeben,10.10.2026,,,Folkfest,,,Konzert\n"                                  # Folgetag, ohne Zeit/Ort
    "freigegeben,01.10.2026,30.10.2026,,Kunstschau,,Museum,Ausstellung\n"           # läuft über Anreisetag
    "freigegeben,09.10.2026,,10:00,Messe X,,Congress,Messe\n"
    "freigegeben,09.10.2026,,15:00,Spiel in Schweinfurt,,Schweinfurt,Sport\n"       # Ortsausschluss
    "freigegeben,01.10.2026,31.12.2026,,Heimspiel,,Arena,Sport\n"                   # Sport: Ende ignorieren
    "freigegeben,12.10.2026,,,Zu spät,,Laby,Konzert\n"
)


REAL_SHEET = (
    '"Titel (DE)","Titel (EN)","Start-Datum","Start-Uhrzeit","End-Datum (optional)","Ort","Status","Kategorie",'
    '"Kurztext/Tipp (DE)","Kurztext/Tipp (EN)","Quelle","Erfasst am"\n'
    '"Konzert Alpha","Concert Alpha","09.10.2026","20:00 Uhr","","Keller Z87","Freigegeben","Konzert","Text, mit Komma","","https://x.example","46300"\n'
    '"Ausstellung Beta","","01.10.2026","","30.10.2026","Hubland Süd, Geb. Z6","Freigegeben","Ausstellung","","","","46301"\n'
    '"Entwurf Gamma","","09.10.2026","19:30","","","Entwurf","Konzert","","","","46302"\n'
    '"Theater Delta","Play Delta","10.10.2026","19:30","","","Freigegeben","Sonstiges","","","","46303"\n'
)


def test_events_abschlusslink():
    """Mit Terminen folgt ein Link zur Veranstaltungsseite; ohne URL nicht; ohne Termine bleibt der Fallback."""
    ev1 = [{"start": ANREISE, "ende": ANREISE, "zeit": "20:00", "titel": "A", "titel_en": "", "ort": "", "kategorie": "Konzert"}]
    url = "https://www.babelfish-hostel.de/wuerzburg-tipps/"
    de = ev.events_html(ev1, ANREISE, "de", url)
    en = ev.events_html(ev1, ANREISE, "en", url)
    assert de.rstrip().endswith("</p>") and f'Mehr Tipps findest Du im <a href="{url}">Eventbereich unserer Website</a>.' in de
    assert f'You&rsquo;ll find more tips in the <a href="{url}">events section of our website</a>.' in en
    assert de.index("20:00 Uhr") < de.index("Eventbereich")                    # Link steht unter der Liste
    assert "Eventbereich" not in ev.events_html(ev1, ANREISE, "de", "")        # ohne URL kein Link
    assert "Eventbereich" not in ev.events_html([], ANREISE, "de", url)        # Fallback unverändert
    assert "unserer Veranstaltungsseite" in ev.events_html([], ANREISE, "de", url)
    assert 'a"b' not in ev.events_html(ev1, ANREISE, "de", 'https://x.de/?a"b')  # URL wird maskiert
    q = ev.EventQuelle("ID", url, lader=lambda u: REAL_SHEET)
    assert "Eventbereich unserer Website" in q.html(ANREISE, "de") and "events section" in q.html(ANREISE, "en")


def test_events_vorrang_und_maximum():
    """Sonstiges (Theater) ist gleichberechtigt; Uni & Kongress steht bei Überfüllung zurück; höchstens 4."""
    def e(titel, kat, tag, zeit):
        return {"start": tag, "ende": tag, "zeit": zeit, "titel": titel, "titel_en": "", "ort": "", "kategorie": kat}
    t1, t2 = ANREISE, date(2026, 10, 10)
    liste = [
        e("Konzert1", "Konzert", t1, "20:00"), e("Theater1", "Sonstiges", t1, "19:00"), e("Kongress1", "Uni & Kongress", t1, "09:00"),
        e("Sport2", "Sport", t2, "15:00"), e("Theater2", "Sonstiges", t2, "19:30"), e("Kongress2", "Uni & Kongress", t2, "10:00"),
        e("Ausstellung2", "Ausstellung", t2, ""),
    ]
    titel = [x["titel"] for x in ev.waehle_events(liste, ANREISE)]
    assert len(titel) == 4, titel
    # Tag 1: Theater (19:00) und Konzert (20:00) kommen beide, Sonstiges wird nicht benachteiligt
    assert "Theater1" in titel and "Konzert1" in titel
    # Tag 2: vier Kandidaten, höchstens zwei pro Tag -> chronologisch entscheidet (Ausstellung ohne Zeit, Sport 15:00)
    assert "Ausstellung2" in titel and "Sport2" in titel and "Theater2" not in titel
    assert not any(x.startswith("Kongress") for x in titel)                          # Kongresse stehen zurück
    assert sum(1 for x in titel if x.endswith("2")) <= 2                             # höchstens 2 pro Tag
    # Reicht es nicht, rücken Kongresse nach (zwei Kategorien am Tag)
    nur_kongress = [e("K1", "Uni & Kongress", t1, "09:00"), e("K2", "Uni & Kongress", t2, "10:00")]
    assert len(ev.waehle_events(nur_kongress, ANREISE)) == 2


def test_events_echte_kopfzeile():
    """Kopfzeile wie im echten Sheet (mit Klammern und Bindestrichen) wird erkannt."""
    liste = ev.lese_events(REAL_SHEET, 2026)
    assert [e["titel"] for e in liste] == ["Konzert Alpha", "Ausstellung Beta", "Theater Delta"]
    k = liste[0]
    assert k["titel_en"] == "Concert Alpha" and k["zeit"] == "20:00 Uhr" and k["ort"] == "Keller Z87"
    assert liste[1]["ende"] == date(2026, 10, 30) and liste[1]["ort"] == "Hubland Süd, Geb. Z6"
    wahl = ev.waehle_events(liste, ANREISE)
    assert [e["titel"] for e in wahl] == ["Ausstellung Beta", "Konzert Alpha", "Theater Delta"] or len(wahl) == 3
    h = ev.events_html(wahl, ANREISE, "en")
    assert "Concert Alpha" in h and "8 pm" in h and "Play Delta" in h and "Ausstellung Beta" in h
    hd = ev.events_html(wahl, ANREISE, "de")
    assert "Konzert Alpha" in hd and "20:00 Uhr" in hd and "Entwurf Gamma" not in hd
    q = ev.EventQuelle("ID", "https://www.babelfish-hostel.de/wuerzburg-tipps/", lader=lambda u: REAL_SHEET)
    assert "Konzert Alpha" in q.html(ANREISE, "de") and "wuerzburg-tipps" in q.html(ANREISE, "de")  # Abschlusslink


def test_events():
    liste = ev.lese_events(SHEET, 2026)
    wahl = ev.waehle_events(liste, ANREISE)
    titel = [e["titel"] for e in wahl]
    assert len(wahl) <= 4 and "Jazzabend" in titel and "Nicht frei" not in titel
    assert titel.count("Jazzabend") == 1 and "Spiel in Schweinfurt" not in titel and "Heimspiel" not in titel
    assert "Messe X" not in titel or len(wahl) < 3
    h = ev.events_html(wahl, ANREISE, "en")
    assert "Jazz night" in h and "8 pm" in h and "Fri, 9 Oct" in h
    hd = ev.events_html(wahl, ANREISE, "de")
    assert "Jazzabend" in hd and "20:00 Uhr" in hd and "Fr, 9. Okt" in hd
    assert "&middot; </strong>" not in hd and "<span" not in ev.events_html(
        [{"start": ANREISE, "ende": ANREISE, "zeit": "", "titel": "Ohne", "titel_en": "", "ort": "", "kategorie": "x"}],
        ANREISE, "de")
    # Quelle: Fehler und leeres Sheet -> Fallback, nie Exception
    def kaputt(url):
        raise OSError("offline")
    q = ev.EventQuelle("SHEETID", "https://example.org/events", lader=kaputt)
    assert "unserer Veranstaltungsseite" in q.html(ANREISE, "de") and "example.org/events" in q.html(ANREISE, "de")
    assert "our events page" in ev.EventQuelle("", "https://e.org").html(ANREISE, "en")
    assert "unserer Website" in ev.EventQuelle("").html(ANREISE, "de")
    q = ev.EventQuelle("X", lader=lambda u: "Foo,Bar\n1,2\n")  # unbekannte Kopfzeile
    assert "<p" in q.html(ANREISE, "de")
    assert ev.sheet_url("ID", "Veranstaltungen").endswith("tqx=out%3Acsv&sheet=Veranstaltungen")


def _gast(conn, reg, vorname, anreise="2026-10-09", email_="a@example.com", sprache="de", rate="flex", kanal=None):
    conn.execute(
        "INSERT INTO arrivals (reg_nummer, vorname, nachname, email, anreise, abreise, anzahl_personen, kanal, sprache, rate,"
        " erstmals_importiert, zuletzt_gesehen) VALUES (?,?,?,?,?,?,?,?,?,?,'x','x')",
        (reg, vorname, "N", email_, anreise, "2026-10-11", 2, kanal, sprache, rate))
    conn.commit()


def test_versand():
    with tempfile.TemporaryDirectory() as tmp:
        conn = verbinde(Path(tmp) / "t.sqlite")
        init_db(conn)
        _gast(conn, "1", "Anna")
        _gast(conn, "2", "Ben", sprache="en", email_="b@example.com")
        _gast(conn, "3", "Kaputt", email_="keine-adresse")
        _gast(conn, "4", "Spät", anreise="2026-10-20")      # noch nicht fällig
        _gast(conn, "5", "Vorbei", anreise="2026-10-05")    # schon vorbei
        k = SendKonfig(vorschau_dir=Path(tmp) / "v")
        q = ev.EventQuelle("")

        # Trockenlauf: nichts gesendet, nichts protokolliert
        e = verarbeite(conn, k, q, HEUTE, "dry")
        assert (e.vorschau, e.gesendet, e.fehler, e.uebersprungen) == (2, 0, 0, 1), e
        assert conn.execute("SELECT COUNT(*) FROM mail_log").fetchone()[0] == 0
        assert (Path(tmp) / "v" / "2_en.html").is_file()

        gesendet = []
        def sender(msg):
            gesendet.append(msg)
            return msg["Message-ID"]

        # Testmodus: alles an die Testadresse, kein Log
        e = verarbeite(conn, k, q, HEUTE, "test", sender, "test@example.com")
        assert e.gesendet == 2 and all(m["To"] == "test@example.com" for m in gesendet)
        assert conn.execute("SELECT COUNT(*) FROM mail_log").fetchone()[0] == 0
        gesendet.clear()

        # Live: 2 gesendet, 1 übersprungen (ungültige Adresse)
        e = verarbeite(conn, k, q, HEUTE, "live", sender)
        assert e.gesendet == 2 and e.uebersprungen == 1 and e.fehler == 0, e
        assert sorted(m["To"] for m in gesendet) == ["a@example.com", "b@example.com"]
        s = {r["reg_nummer"]: r["status"] for r in conn.execute(
            "SELECT a.reg_nummer, m.status FROM mail_log m JOIN arrivals a ON a.id=m.arrival_id")}
        assert s == {"1": "gesendet", "2": "gesendet", "3": "uebersprungen"}, s
        assert conn.execute("SELECT message_id FROM mail_log WHERE status='gesendet'").fetchone()[0].startswith("<")
        m = gesendet[0]
        assert m.get_body(("plain",)) is not None and m.get_body(("html",)) is not None

        # Idempotenz: zweiter Lauf sendet nichts
        gesendet.clear()
        e = verarbeite(conn, k, q, HEUTE, "live", sender)
        assert e.gesendet == 0 and not gesendet

        # Fehler -> 'fehler', nächster Lauf versucht erneut, nach MAX_VERSUCHE Schluss
        _gast(conn, "6", "Fehl", email_="f@example.com")
        def schlecht(msg):
            raise VersandFehler("HTTP 503", False)
        for lauf in range(1, 5):
            e = verarbeite(conn, k, q, HEUTE, "live", schlecht)
            r = conn.execute("SELECT status, versuche FROM mail_log WHERE arrival_id=(SELECT id FROM arrivals WHERE reg_nummer='6')").fetchone()
            assert r["status"] == "fehler" and r["versuche"] == min(lauf, 3), (lauf, dict(r))
        e = verarbeite(conn, k, q, HEUTE, "live", sender)   # Graph geht wieder - aber MAX_VERSUCHE erreicht
        assert e.gesendet == 0
        conn.execute("UPDATE mail_log SET versuche=1 WHERE status='fehler'"); conn.commit()
        e = verarbeite(conn, k, q, HEUTE, "live", sender)
        assert e.gesendet == 1
        # Dauerhafter Fehler -> übersprungen
        _gast(conn, "7", "Hart", email_="h@example.com")
        def hart(msg):
            raise VersandFehler("HTTP 403", True)
        e = verarbeite(conn, k, q, HEUTE, "live", hart)
        assert conn.execute("SELECT status FROM mail_log WHERE arrival_id=(SELECT id FROM arrivals WHERE reg_nummer='7')").fetchone()[0] == "uebersprungen"
        # Hängengebliebener Versand ('geplant' mit Versuch) wird nicht wiederholt
        _gast(conn, "8", "Crash", email_="c@example.com")
        aid = conn.execute("SELECT id FROM arrivals WHERE reg_nummer='8'").fetchone()[0]
        conn.execute("INSERT INTO mail_log (arrival_id, vorlage, status, versuche, erstellt_am) VALUES (?,?,'geplant',1,'x')", (aid, VORLAGE))
        conn.commit()
        gesendet.clear()
        e = verarbeite(conn, k, q, HEUTE, "live", sender)
        assert not gesendet and e.uebersprungen == 1
        conn.close()


def test_migration():
    with tempfile.TemporaryDirectory() as tmp:
        conn = verbinde(Path(tmp) / "alt.sqlite")
        conn.executescript("CREATE TABLE arrivals (id INTEGER PRIMARY KEY, reg_nummer TEXT NOT NULL UNIQUE, vorname TEXT,"
                           " nachname TEXT, email TEXT NOT NULL, anreise TEXT NOT NULL, abreise TEXT NOT NULL, buchungsdatum TEXT,"
                           " anzahl_personen INTEGER, kanal TEXT, sprache TEXT, import_id INTEGER, erstmals_importiert TEXT NOT NULL,"
                           " zuletzt_gesehen TEXT NOT NULL);")
        init_db(conn)
        assert "rate" in {r["name"] for r in conn.execute("PRAGMA table_info(arrivals)")}
        init_db(conn)  # idempotent
        conn.close()


class _Graph(BaseHTTPRequestHandler):
    empfangen = []

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        _Graph.empfangen.append((self.path, self.headers["Authorization"], self.headers["Content-Type"], self.rfile.read(n)))
        code = 202 if "ok@" in self.path else 403
        self.send_response(code)
        self.end_headers()
        if code != 202:
            self.wfile.write(json.dumps({"error": {"message": "ErrorAccessDenied"}}).encode())

    def log_message(self, *a):
        pass


def test_graph():
    srv = HTTPServer(("127.0.0.1", 0), _Graph)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    basis = f"http://127.0.0.1:{srv.server_port}/v1.0"
    msg = baue_mime("info@babelfish-hostel.de", "gast@example.com", "Betreff Ü", "<p>Hallo Ä</p>", "Hallo Ä")
    mid = sende_mime(msg, "ok@babelfish-hostel.de", "TOKEN", basis)
    pfad, auth, ctype, body = _Graph.empfangen[-1]
    assert pfad == "/v1.0/users/ok@babelfish-hostel.de/sendMail" and auth == "Bearer TOKEN" and ctype == "text/plain"
    roh = email.message_from_bytes(base64.b64decode(body), policy=policy.default)
    assert roh["Subject"] == "Betreff Ü" and roh["Message-ID"] == mid and roh["To"] == "gast@example.com"
    assert "Hallo Ä" in roh.get_body(("html",)).get_content() and "Hallo Ä" in roh.get_body(("plain",)).get_content()
    try:
        sende_mime(msg, "nein@babelfish-hostel.de", "TOKEN", basis)
        raise AssertionError("403 hätte Fehler auslösen müssen")
    except VersandFehler as exc:
        assert exc.dauerhaft and "403" in str(exc) and "ErrorAccessDenied" in str(exc)
    srv.shutdown()
    try:
        sende_mime(msg, "ok@x", "T", "http://127.0.0.1:1/v1.0", timeout=2)
        raise AssertionError("Netzwerkfehler erwartet")
    except VersandFehler as exc:
        assert not exc.dauerhaft


def test_zahlung_hinweis():
    from run_send import zahlung_hinweis
    z = lambda **kw: {"paylink": "https://p", "betrag": 10.0, "gesamtbetrag": 10.0, "kanal": "EGB", "rate": "flex", **kw}
    assert zahlung_hinweis(z()) is None
    assert "fehlt" in zahlung_hinweis(z(betrag=None)) and "fehlt" in zahlung_hinweis(z(gesamtbetrag=None))
    assert zahlung_hinweis(z(betrag=None, paylink=None)) is None            # bezahlt: keine Warnung
    assert zahlung_hinweis(z(betrag=None, kanal="BHC")) is None             # Gesamt braucht keinen Betrag
    assert zahlung_hinweis(z(gesamtbetrag=None, rate="fix")) is None
    assert zahlung_hinweis(z(gesamtbetrag=None, rate=None), "flex") is not None  # RATE_STANDARD=flex


NEUE_CSV_2026_11 = (
    "RegNummer;Buchungsnummer;Ident;Anreise;Abreise;Buchungsdatum;AnzahlPersonen;Vorname;Nachname;Strasse;Postleitzahl;"
    "Stadt;Email;Sprache;Kanal;Rate;Anzahlung;Anzahlung_bis;total;desc1;amou1;desc2;amou2;desc3;amou3;Paylink\r\n"
    "     90001;6000000001;111;2026-11-10 00:00:00;2026-11-11 00:00:00;2026-10-21;1;Anna;Test;Weg 5, 12345 Ort;;Ort;a@example.com;D;BHC;FLEX;26.90;0000-00-00;26.90;;26.90;;0.00;;0.00;https://pay.example/1\r\n"
    "     90002;;222;2026-11-10 00:00:00;2026-11-11 00:00:00;2026-11-03;1;Bea;Test;Str 8;93437;Ort;b@example.com;D;;;22.90;2026-11-08;22.90;;22.90;;0.00;;0.00;https://pay.example/2\r\n"
    "     90003;410200-20-1;I01;2026-11-10 00:00:00;2026-11-12 00:00:00;2026-07-08;1;Carl;Test;Str 1;12055;Ort;c@example.com;D;EGB;FLEX;0.00;0000-00-00;173.80;;173.80;;0.00;;0.00;https://pay.example/3\r\n"
    "     90004;410200-20-2;I02;2026-11-10 00:00:00;2026-11-12 00:00:00;2026-07-08;1;Dora;Test;Str 1;12055;Ort;d@example.com;D;EGB;FLEX;66.99;0000-00-00;173.80;;100.00;;0.00;;0.00;https://pay.example/4\r\n"
    "     90005;410200-20-3;I03;2026-11-10 00:00:00;2026-11-11 00:00:00;2026-11-03;1;Emil;Test;Str 2;F92;Ort;e@example.com;E;EGB;FLEX;66.99;0000-00-00;;;;;;;\r\n"
    "     90006;5000000006;IBOO6;2026-11-10 00:00:00;2026-11-11 00:00:00;2026-10-23;1;Fia;Test;Str 3;97816;Ort;f.1@guest.booking.com;D;BHC;OTA;0.00;0000-00-00;52.92;;52.92;;0.00;;0.00;https://pay.example/6\r\n"
    "\r\n"
)


def test_csv_2026_11():
    """CSV-Format vom 08.11.2026: neue Spalten, Zeile mit 25 statt 26 Feldern (letztes leeres Feld fehlt)."""
    from csv_import import importiere_csv
    from run_send import SendKonfig, mail_daten
    with tempfile.TemporaryDirectory() as tmp:
        pfad = Path(tmp) / "a.csv"
        pfad.write_bytes(NEUE_CSV_2026_11.encode("utf-8"))
        conn = verbinde(Path(tmp) / "n.sqlite")
        init_db(conn)
        e = importiere_csv(conn, pfad)
        assert (e.gesamt, e.neu, e.fehler) == (6, 6, 0), e
        q = ev.EventQuelle("")
        k = SendKonfig()
        heute = date(2026, 11, 7)    # Versand 3 Tage vor Anreise
        erwartet = {"90001": "gesamt", "90002": "gesamt", "90003": "anzahlung", "90004": "rest", "90005": None, "90006": "gesamt"}
        for reg, var in erwartet.items():
            z = conn.execute("SELECT * FROM arrivals WHERE reg_nummer=?", (reg,)).fetchone()
            d = mail_daten(z, k, q, heute)
            assert zahlung_variante(d) == var, (reg, zahlung_variante(d))
            h = render_mail(d)["html"]
            assert "läuft morgen ab" in h or "ends tomorrow" in h
            assert ("Jetzt bezahlen" in h or "Pay now" in h) == (var is not None)
        z = conn.execute("SELECT * FROM arrivals WHERE reg_nummer='90002'").fetchone()
        assert mail_daten(z, k, q, heute).buchungsnummer == "90002"       # Fallback auf RegNummer
        assert k.tage_vorher == 3 and k.storno_tage == 2
        conn.close()


NEUE_CSV = (
    "RegNummer;Buchungsnummer;Ident;Anreise;Abreise;Buchungsdatum;AnzahlPersonen;Vorname;Nachname;Strasse;Postleitzahl;"
    "Stadt;Email;Sprache;Kanal;total;desc1;amou1;desc2;amou2;desc3;Paylink\r\n"
    "     13724;410200-20-12913;246;2026-10-25 00:00:00;2026-11-01 00:00:00;2026-09-05;1;Lautaro;M;Calle 8;47194;Fuensalda\u00f1a;"
    "l@example.com;E;EGB;146.25;;146.25;;0.00;;https://hostels.assd.com/wci/pay/AbC-_=\r\n"
    "     13729;410200-20-13149;378;2026-10-25 00:00:00;2026-10-26 00:00:00;2026-10-19;1;Kerstin;A;;;;k@example.com;D;EGB;18.00;;;;;;\r\n"
    "\r\n"
)


def test_neue_csv():
    from csv_import import importiere_csv
    with tempfile.TemporaryDirectory() as tmp:
        pfad = Path(tmp) / "a.csv"
        pfad.write_bytes(NEUE_CSV.encode("utf-8"))
        conn = verbinde(Path(tmp) / "n.sqlite")
        init_db(conn)
        e = importiere_csv(conn, pfad)
        assert (e.gesamt, e.neu, e.fehler) == (2, 2, 0), e
        a = conn.execute("SELECT * FROM arrivals WHERE reg_nummer='13724'").fetchone()
        assert a["buchungsnummer"] == "410200-20-12913" and a["sprache"] == "en" and a["kanal"] == "EGB"
        assert a["betrag"] == 146.25 and a["gesamtbetrag"] == 146.25 and a["paylink"].endswith("AbC-_=")
        b = conn.execute("SELECT * FROM arrivals WHERE reg_nummer='13729'").fetchone()
        assert b["sprache"] == "de" and b["betrag"] is None and b["paylink"] is None and b["gesamtbetrag"] == 18.0
        # Versand: echte Buchungsnummer; Lautaro (Paylink + Betrag) bekommt Zahlung, Kerstin (bezahlt) nicht
        from run_send import SendKonfig, mail_daten
        q = ev.EventQuelle("")
        d = mail_daten(a, SendKonfig(), q, date(2026, 10, 23))
        assert d.buchungsnummer == "410200-20-12913" and d.sprache == "en"
        assert zahlung_aktiv(d) and "Pay now" in render_mail(d)["html"]
        d = mail_daten(b, SendKonfig(), q, date(2026, 10, 23))
        assert not zahlung_aktiv(d) and "Pay now" not in render_mail(d)["html"]
        conn.close()


def test_env_kommentare():
    import os
    import run_ingest
    w = run_ingest.wert_ohne_kommentar
    assert w("paylink          # flex: Zahlungsblock nur bei Rate Flex") == "paylink"
    assert w("vorschau         # vorschau (Standard)") == "vorschau"
    assert w("  data/x.sqlite  ") == "data/x.sqlite" and w("") == "" and w("   # nur Kommentar") == ""
    assert w('"mit # im Wert"  # Kommentar') == "mit # im Wert" and w("'a b'") == "a b"
    assert w("geheim#ohneleerzeichen") == "geheim#ohneleerzeichen"      # '#' ohne Leerzeichen bleibt Teil des Werts
    assert w("a~b.c-d_e") == "a~b.c-d_e"
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "t.env"
        f.write_text("# Kommentar\r\nTESTKEY_A=paylink     # Erklaerung\r\nTESTKEY_B = \"x # y\"\r\nTESTKEY_C=\r\n", encoding="utf-8")
        for k in ("TESTKEY_A", "TESTKEY_B", "TESTKEY_C"):
            os.environ.pop(k, None)
        run_ingest.lade_env(f)
        assert (os.environ["TESTKEY_A"], os.environ["TESTKEY_B"], os.environ["TESTKEY_C"]) == ("paylink", "x # y", "")
        for k in ("TESTKEY_A", "TESTKEY_B", "TESTKEY_C"):
            del os.environ[k]


def test_pfade():
    import run_ingest
    assert run_ingest.STANDARD_ENV == run_ingest.PROJEKT / "conf" / ".env"
    assert (run_ingest.PROJEKT / "src" / "run_ingest.py").is_file()
    assert run_ingest.pfad_aus_env("data/x.sqlite") == run_ingest.PROJEKT / "data" / "x.sqlite"
    absolut = Path(tempfile.gettempdir()) / "a.sqlite"
    assert run_ingest.pfad_aus_env(str(absolut)) == absolut
    from render import VORLAGEN_DIR
    assert (VORLAGEN_DIR / "PreArrival_DE.html").is_file() and (VORLAGEN_DIR / "PreArrival_EN.html").is_file()


def test_daily_vorschau():
    import os, time
    import run_daily
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        alt, neu = d / "alt.html", d / "neu.html"
        alt.write_text("x"); neu.write_text("x"); (d / "bleibt.txt").write_text("x")
        os.utime(alt, (time.time() - 10 * 86400,) * 2)
        assert run_daily.loesche_alte_vorschauen(d, 7) == 1 and not alt.exists() and neu.exists() and (d / "bleibt.txt").exists()
        assert run_daily.loesche_alte_vorschauen(d / "gibtsnicht", 7) == 0
        assert run_daily.send_argumente("live", d) == ["--live"]
        for modus in ("vorschau", "", "irgendwas"):  # alles außer 'live' ist sicher
            assert run_daily.send_argumente(modus, d) == ["--vorschau", str(d)]


def test_daily():
    import run_daily
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "l.lock"
        assert run_daily._lock_holen(lock) and not run_daily._lock_holen(lock)
        import os, time
        os.utime(lock, (time.time() - 3 * 3600,) * 2)
        assert run_daily._lock_holen(lock)  # altes Lock wird übernommen
        lock.unlink()
        run_daily.ping("")  # ohne URL: nichts passiert
        conn = verbinde(Path(tmp) / "d.sqlite")
        init_db(conn)
        conn.close()
        assert not run_daily.heute_importiert(Path(tmp) / "d.sqlite")


def test_import_felder():
    from csv_import import _rate, _sprache, _zeile_lesen
    assert _rate("Flex") == "flex" and _rate("Fix-Rate") == "fix" and _rate("") is None and _rate("x") is None
    assert _sprache({"Postleitzahl": "97070"}) == "de" and _sprache({"Postleitzahl": "47"}) == "en"
    assert _sprache({"Sprache": "EN", "Postleitzahl": "97070"}) == "en"
    z = _zeile_lesen({"RegNummer": "1", "Email": "a@b.de", "Anreise": "09.10.26 00:00", "Abreise": "11.10.26 00:00",
                      "Vorname": "Berlin", "Stadt": "Berlin", "Postleitzahl": "10437"})
    assert z["vorname"] is None and z["sprache"] == "de" and "strasse" not in z and "stadt" not in z


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
    print("Alle Tests bestanden.")
