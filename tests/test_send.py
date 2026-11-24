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
from db import init_db, letzter_import, verbinde
from graph_mail import VersandFehler, baue_mime, sende_mime
from render import (MailDaten, anrede, betrag_text, datum_lang, gaeste_text, naechte_text,
                    render_mail, zahlung_aktiv, zahlung_variante, storno_variante)
from run_send import SendKonfig, VORLAGE, faellige_gaeste, import_pruefung, verarbeite

HEUTE = date(2026, 10, 7)
ANREISE = date(2026, 10, 9)


def daten(**kw) -> MailDaten:
    basis = dict(vorname="Nico", buchungsnummer="410200-20-12058", anreise=ANREISE, abreise=date(2026, 10, 11),
                 personen=4, sprache="de", heute=HEUTE, events_html="<p>EVT</p>",
                 paymentlink="https://pay.example/x?a=1&b=2", voraus_datum=date(2026, 10, 5), schon_eingegangen=None)
    basis.update(kw)
    return MailDaten(**basis)


def test_rabatt_pfade_kuenstler():
    from render import render_html, lade_vorlage, zahlung_variante
    from leistungen import (gesamtrabatt, leistungen_html, parse_leistungen, parse_pfade, mehrere_pfade,
                            pfade_html, bezeichnung)
    pos = parse_leistungen("2026-11-18 6 x 10-Bettzimmer mixed 7.90 10.00 42.66|2026-11-18 1 x Frühstück 10.00 0.00 10.00")
    h = leistungen_html(pos, None, "de")
    assert "6 &times; 7,90 EUR = 47,40 EUR, Rabatt 10 %" in h, h
    assert gesamtrabatt(pos) == 4.74
    assert "darin enthaltener Rabatt" in h and "4,74 EUR" in h
    he = leistungen_html(pos, None, "en")
    assert "6 &times; EUR 7.90 = EUR 47.40, discount 10 %" in he and "discount included" in he and "EUR 4.74" in he
    # ohne Rabatt: keine Rabattzeile, kein "="
    pos2 = parse_leistungen("2026-11-18 2 x Frühstück 10.00 0.00 20.00")
    h2 = leistungen_html(pos2, None, "de")
    assert "Rabatt" not in h2 and " = " not in h2

    ein = "2026-11-26 2026-11-28 Doppelzimmer"
    mehr = ("2026-11-26 2026-11-30 6-Bettzimmer_mixed 4-Bettzimmer_private 4-Bettzimmer_private"
            "|2026-11-27 2026-11-28 Apartement")
    assert not mehrere_pfade(ein) and mehrere_pfade(mehr) and not mehrere_pfade(None) and not mehrere_pfade("kaputt")
    assert parse_pfade("2026-13-01 2026-11-02 X") is None
    ph = pfade_html(parse_pfade(mehr), "de")
    assert "Do, 26.11. &ndash; Mo, 30.11.2026" in ph and "2 &times; 4-Bettzimmer privat" in ph
    assert "Apartment für 4" in ph
    dorm = pfade_html(parse_pfade("2026-11-26 2026-11-27 6x6-Bettzimmer_mixed 1x4-Bettzimmer_female 6x6-Bettzimmer_mixed"), "de")
    assert "12 &times; Bett im 6-Bettzimmer gender-mixed" in dorm and "1 &times; Bett im 4-Bettzimmer für Frauen" in dorm, dorm and "Deine Aufenthalte" in ph
    assert "Your stays" in pfade_html(parse_pfade(mehr), "en") and "Thu, 26.11." in pfade_html(parse_pfade(mehr), "en")
    assert bezeichnung("Doppelzimmer_studio", "en") == "Double room Studio"
    for sp in ("de", "en"):
        a = render_html(lade_vorlage(sp), daten(sprache=sp, pfade=ein))
        b = render_html(lade_vorlage(sp), daten(sprache=sp, pfade=mehr))
        c = render_html(lade_vorlage(sp), daten(sprache=sp, pfade=None))
        assert ("Dauer:" if sp == "de" else "Length of stay:") in a and ("Dauer:" if sp == "de" else "Length of stay:") in c
        assert ("Dauer:" if sp == "de" else "Length of stay:") not in b
        assert ("Anreise:" if sp == "de" else "Arrival:") not in b and "Mo, 30.11.2026" in b.replace("Mon, 30.11.2026", "Mo, 30.11.2026")

    # Künstler: eigener Anzahlungstext, kein AGB-Satz
    d = daten(rate="kuenstler", kanal=None)
    assert zahlung_variante(d) == "anzahlung"
    de = render_html(lade_vorlage("de"), d)
    assert "Zahle gerne &uuml;ber den Paymentlink" in de and "Anzahlung fällig" not in de and "AGB" not in de
    assert "Jetzt bezahlen" in de
    en = render_html(lade_vorlage("en"), daten(sprache="en", rate="kuenstler", kanal=None))
    assert "payment link if that" in en and "deposit is due" not in en and "General Terms" not in en
    normal = render_html(lade_vorlage("de"), daten())
    assert "Anzahlung fällig" in normal and "AGB" in normal and "Zahle gerne" not in normal

    # Rezeptionstext
    assert "Von November bis März ist die Rezeption sonntags bis donnerstags" in normal
    assert 'href="tel:+499313040430"' in normal and "Self-Check-in" in normal
    ene = render_html(lade_vorlage("en"), daten(sprache="en"))
    assert "from November to March" in ene and "self check-in" in ene


def test_import_pfade_kuenstler_checkio():
    from csv_import import importiere_csv, _rate
    assert _rate("Künstle") == "kuenstler" and _rate("Künstler") == "kuenstler" and _rate("FLEX") == "flex"
    kopf = "RegNummer;Buchungsnummer;Anreise;Abreise;AnzahlPersonen;Vorname;Email;Rate;Pfade"
    with tempfile.TemporaryDirectory() as tmp:
        conn = verbinde(Path(tmp) / "p.sqlite"); init_db(conn)
        # Bestand: Gast mit eingecheckt = 1
        f1 = Path(tmp) / "1.csv"
        f1.write_text("RegNummer;Anreise;Abreise;Email;CHECKIO\n1;2026-11-26;2026-11-27;a@x.de;1\n", encoding="utf-8")
        importiere_csv(conn, f1)
        assert conn.execute("SELECT eingecheckt FROM arrivals WHERE reg_nummer='1'").fetchone()[0] == 1
        # neue CSV ohne CHECKIO: Stand bleibt, Hinweis kommt, pfade/rate werden gespeichert
        f2 = Path(tmp) / "2.csv"
        f2.write_text(kopf + "\n1;B1;2026-11-26;2026-11-27;2;Ann;a@x.de;Künstle;2026-11-26 2026-11-27 Doppelzimmer\n", encoding="utf-8")
        e = importiere_csv(conn, f2)
        r = conn.execute("SELECT eingecheckt, rate, pfade FROM arrivals WHERE reg_nummer='1'").fetchone()
        assert tuple(r) == (1, "kuenstler", "2026-11-26 2026-11-27 Doppelzimmer"), tuple(r)
        assert any("CHECKIO fehlt" in h for h in e.hinweise)


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
    # "Stimmt alles?": fester Teil bleibt, Storno-Satz richtet sich nach dem Versandtag (letzter kostenfreier Tag = Anreise - 3, die 2 Tage davor sind kostenpflichtig)
    fest = {"de": ("Bitte prüfe Datum und Personenzahl.", "Falls Du nicht kommen kannst, sag uns bitte kurz Bescheid &ndash; dann wissen wir Bescheid"),
            "en": ("Please check the dates and the number of guests.", "If you can't make it, please let us know")}
    satz = {"de": {"morgen": "läuft morgen ab", "heute": "läuft heute ab", "datum": "läuft am <strong>Di, 6. Okt</strong> ab"},
            "en": {"morgen": "ends tomorrow", "heute": "ends today", "datum": "ends on <strong>Tue, 6 Oct</strong>"}}
    for tage_vor, erwartet in ((5, "datum"), (4, "morgen"), (3, "heute"), (2, None), (1, None), (0, None)):
        d0 = daten(heute=ANREISE - timedelta(days=tage_vor))
        assert storno_variante(d0) == erwartet, tage_vor
        # Rate NRF: nie kostenfrei stornierbar -> Satz entfällt in jedem Fall, FLEX/FIX/leer unverändert
        for r in ("nrf", "NRF"):
            assert storno_variante(daten(heute=d0.heute, rate=r)) is None, (tage_vor, r)
        for r in ("flex", "fix", None):
            assert storno_variante(daten(heute=d0.heute, rate=r)) == erwartet, (tage_vor, r)
        for sprache, text in (("de", "kostenfreie Stornierung"), ("en", "free cancellation")):
            h = render_mail(daten(heute=d0.heute, sprache=sprache, rate="nrf"))["html"]
            assert text not in h and "{{" not in h and "BLOCK" not in h, (tage_vor, sprache)
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
    # Entscheidung allein aus Paylink, Vorausdatum und Schon_eingegangen (Kanal und Rate spielen keine Rolle)
    faelle = {
        "anzahlung": [dict(), dict(schon_eingegangen=0.0)],                      # Vorauszahlung nötig, nichts bezahlt
        "rest": [dict(schon_eingegangen=20.0),                                   # schon etwas bezahlt
                 dict(schon_eingegangen=5.0, voraus_datum=None)],                # auch ohne Vorausdatum
        "gesamt": [dict(voraus_datum=None), dict(voraus_datum=None, schon_eingegangen=0.0)],
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
    # Kein Paylink (alles bezahlt) -> kein Zahlungsbereich, egal was sonst in der CSV steht
    for kw in ({"paymentlink": None}, {"paymentlink": ""}, {"paymentlink": None, "schon_eingegangen": 50.0},
               {"paymentlink": None, "voraus_datum": None}):
        d = daten(**kw)
        assert not zahlung_aktiv(d) and "Jetzt bezahlen" not in render_mail(d)["html"], kw
    # Keine Marker/Platzhalter/Entwicklerkommentar im Ergebnis
    for r in (None, 0.0, 20.0):
        for s in ("de", "en"):
            m = render_mail(daten(schon_eingegangen=r, sprache=s))
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


def _gast(conn, reg, vorname, anreise="2026-10-09", email_="a@example.com", sprache="de", rate="flex", kanal=None,
          import_id=1):
    """Legt einen Gast an, der im Import `import_id` steht (Import 1 = Stand von HEUTE, wird bei Bedarf angelegt)."""
    conn.execute("INSERT OR IGNORE INTO import_log (id, dateiname, sha256, importiert_am) VALUES (1, 't', 't', ?)",
                 (HEUTE.isoformat() + " 06:00:00",))
    conn.execute(
        "INSERT INTO arrivals (reg_nummer, vorname, nachname, email, anreise, abreise, anzahl_personen, kanal, sprache, rate,"
        " import_id, erstmals_importiert, zuletzt_gesehen) VALUES (?,?,?,?,?,?,?,?,?,?,?,'x','x')",
        (reg, vorname, "N", email_, anreise, "2026-10-11", 2, kanal, sprache, rate, import_id))
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
        spalten = {r["name"] for r in conn.execute("PRAGMA table_info(arrivals)")}
        assert {"rate", "vorauszahlung", "vorausdatum", "schon_eingegangen", "restbetrag", "zahlbetrag", "leistungen"} <= spalten
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
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
    z = lambda **kw: {"paylink": "https://p", "restbetrag": 10.0, **kw}
    assert zahlung_hinweis(z()) is None
    assert zahlung_hinweis(z(paylink=None, restbetrag=None)) is None        # bezahlt
    assert zahlung_hinweis(z(paylink=None, restbetrag=0.0)) is None
    assert "Restbetrag 0" in zahlung_hinweis(z(restbetrag=None))            # Link, aber nichts offen
    assert "kein Paylink" in zahlung_hinweis(z(paylink=None))               # offen, aber kein Link


KOPF_NEU = ("RegNummer;Buchungsnummer;Ident;Anreise;Abreise;Buchungsdatum;AnzahlPersonen;Vorname;Nachname;Strasse;Postleitzahl;"
            "Stadt;Email;Sprache;Kanal;Rate;Vorauszahlung_1;Vorausdatum_1;Schon_eingegangen;Restbetrag;Zahlbetrag;Paylink")
_ZEILEN_NEU = [
    # 90001: Vorauszahlung nötig, nichts bezahlt -> Anzahlung (Kanal und Rate leer)
    "     90001;;111;2026-11-13 00:00:00;2026-11-14 00:00:00;2026-11-02;1;Anna;Test;Weg 5;10119;Ort;a@example.com;E;;;134.82;2026-11-11;0.00;134.82;134.82;https://pay.example/1",
    # 90002: Vorauszahlung bezahlt, Rest offen -> Rest (Buchung vor Ort/Telefon: Buchungsnummer leer)
    "     90002;;222;2026-11-13 00:00:00;2026-11-14 00:00:00;2026-10-10;11;Bea;Test;Str 8;02977;Ort;b@example.com;D;;;364.60;2026-11-12;364.60;20;384.60;https://pay.example/2",
    # 90003: EGB/FLEX, kein Vorausdatum, nichts bezahlt -> Anzahlung (Sonderfall Website-Flex)
    "     90003;410200-20-1;I01;2026-11-13 00:00:00;2026-11-15 00:00:00;2026-07-01;2;Carl;Test;Str 1;86637;Ort;c@example.com;D;EGB;FLEX;0.00;0000-00-00;0.00;179;179.00;https://pay.example/3",
    # 90004: zwei Adressen im Feld Email -> die erste gilt; Portal, Gesamt
    "     90004;5000000004;IBOO4;2026-11-14 00:00:00;2026-11-15 00:00:00;2026-10-17;5;Dora;Test;Str 2;;Ort;d@example.com, d2@example.com;D;BHC;OTA;0.00;0000-00-00;0.00;155.23;155.23;https://pay.example/4",
    # 90005: keine E-Mail, Zeile endet früh (leere Endfelder fehlen)
    "     90005;;333;2026-11-13 00:00:00;2026-11-16 00:00:00;2026-10-08;1;Emil;Test;;;;;D;;;0.00;0000-00-00;0.00;",
    # 90006: vollständig bezahlt, kein Paylink, kurze Zeile -> Zahlungsbereich mit Text 'bereits komplett bezahlt', ohne Button
    "     90006;410200-20-3;I03;2026-11-15 00:00:00;2026-11-19 00:00:00;2026-11-06;1;Fia;Test;Str 3;91052;Ort;f@example.com;D;EGB;NRF;0.00;0000-00-00;103.60;0;103.60",
    # 90007: schon etwas bezahlt, aber kein Vorausdatum -> Rest
    "     90007;410200-20-4;I04;2026-11-13 00:00:00;2026-11-14 00:00:00;2026-11-01;1;Gus;Test;Str 4;;Ort;g@example.com;E;EGB;FLEX;0.00;0000-00-00;30.00;20;50.00;https://pay.example/7",
]
NEUE_CSV_2026_11 = "\r\n".join([KOPF_NEU] + _ZEILEN_NEU) + "\r\n\r\n"


def test_csv_2026_11():
    """CSV-Format ab 12.11.2026: Vorauszahlung_1, Vorausdatum_1, Schon_eingegangen, Restbetrag, Zahlbetrag."""
    from csv_import import importiere_csv
    from run_send import SendKonfig, mail_daten
    with tempfile.TemporaryDirectory() as tmp:
        pfad = Path(tmp) / "a.csv"
        pfad.write_bytes(NEUE_CSV_2026_11.encode("utf-8"))
        conn = verbinde(Path(tmp) / "n.sqlite")
        init_db(conn)
        e = importiere_csv(conn, pfad)
        assert (e.gesamt, e.neu, e.fehler, e.ohne_email) == (7, 6, 0, 1), e
        assert any("keine E-Mail" in h for h in e.hinweise)
        assert any("CHECKIO fehlt" in h for h in e.hinweise)
        q = ev.EventQuelle("")
        k = SendKonfig()
        heute = date(2026, 11, 10)    # Versand 3 Tage vor Anreise
        erwartet = {"90001": "anzahlung", "90002": "rest", "90003": "anzahlung", "90004": "gesamt", "90006": "bezahlt", "90007": "rest"}
        for reg, var in erwartet.items():
            z = conn.execute("SELECT * FROM arrivals WHERE reg_nummer=?", (reg,)).fetchone()
            d = mail_daten(z, k, q, heute)
            assert zahlung_variante(d) == var, (reg, zahlung_variante(d))
            h = render_mail(d)["html"]
            assert ("Jetzt bezahlen" in h or "Pay now" in h) == (var not in (None, "bezahlt")), reg
            assert ("komplett bezahlt" in h or "paid in full" in h) == (var == "bezahlt"), reg
            ota = reg == "90004"       # Kanal BHC
            assert ("auf der Du gebucht hast" in h or "the website you booked on" in h) == ota, reg
            # letzter kostenfreier Tag = Anreise - 3: Versand 10.11. -> Anreise 13.11. "heute", 14.11. "morgen", später Datum
            soll = {date(2026, 11, 13): "heute", date(2026, 11, 14): "morgen"}.get(d.anreise, "datum")
            if d.rate == "nrf":
                soll = None            # NRF: nie kostenfrei stornierbar
            assert storno_variante(d) == soll, reg
            assert ("läuft heute ab" in h) == (soll == "heute" and d.sprache == "de"), reg
            assert "{{" not in h
        # Felder
        a = conn.execute("SELECT * FROM arrivals WHERE reg_nummer='90002'").fetchone()
        assert (a["vorauszahlung"], a["vorausdatum"], a["schon_eingegangen"], a["restbetrag"], a["zahlbetrag"]) == \
            (364.6, "2026-11-12", 364.6, 20.0, 384.6)
        assert mail_daten(a, k, q, heute).buchungsnummer == "90002"        # Fallback auf RegNummer
        c = conn.execute("SELECT * FROM arrivals WHERE reg_nummer='90003'").fetchone()
        assert c["vorausdatum"] is None and c["vorauszahlung"] is None and c["schon_eingegangen"] is None   # 0000-00-00 / 0.00
        assert c["rate"] == "flex" and c["kanal"] == "EGB" and c["buchungsnummer"] == "410200-20-1"
        assert conn.execute("SELECT rate FROM arrivals WHERE reg_nummer='90006'").fetchone()["rate"] == "nrf"     # Rate NRF
        assert conn.execute("SELECT rate FROM arrivals WHERE reg_nummer='90004'").fetchone()["rate"] is None      # OTA: ohne Zusatz
        assert conn.execute("SELECT email FROM arrivals WHERE reg_nummer='90004'").fetchone()["email"] == "d@example.com"
        assert conn.execute("SELECT sprache FROM arrivals WHERE reg_nummer='90001'").fetchone()["sprache"] == "en"
        assert k.tage_vorher == 3 and k.storno_tage == 2
        conn.close()


def test_csv_schutz():
    """Kopfzeile ohne Paylink: Abbruch statt stillem Verlust; Zeile ohne E-Mail ist nur ein Hinweis."""
    from csv_import import ImportFehler, importiere_csv
    kopf_ohne = KOPF_NEU.rsplit(";Paylink", 1)[0]
    with tempfile.TemporaryDirectory() as tmp:
        conn = verbinde(Path(tmp) / "s.sqlite")
        init_db(conn)
        # 1. Paylink-Feld vorhanden, aber nicht in der Kopfzeile -> nichts importiert, nichts vermerkt
        pfad = Path(tmp) / "a.csv"
        pfad.write_bytes(("\r\n".join([kopf_ohne] + _ZEILEN_NEU)).encode("utf-8"))
        try:
            importiere_csv(conn, pfad)
            raise AssertionError("ImportFehler erwartet")
        except ImportFehler as exc:
            assert "gefüllte" in str(exc) and "Paylink" in str(exc)
        assert conn.execute("SELECT COUNT(*) FROM arrivals").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM import_log").fetchone()[0] == 0
    with tempfile.TemporaryDirectory() as tmp:
        conn = verbinde(Path(tmp) / "t.sqlite")
        init_db(conn)
        # 2. Ungültige Adresse bleibt Fehler, fehlende Adresse nur Hinweis
        pfad = Path(tmp) / "b.csv"
        pfad.write_bytes(NEUE_CSV_2026_11.replace("b@example.com", "kaputt").encode("utf-8"))
        e = importiere_csv(conn, pfad)
        assert (e.gesamt, e.neu, e.fehler, e.ohne_email) == (7, 5, 1, 1), e
        assert any("keine E-Mail" in h for h in e.hinweise)
        assert any("CHECKIO fehlt" in h for h in e.hinweise) and "kaputt" in e.fehler_details[0]
    with tempfile.TemporaryDirectory() as tmp:
        conn = verbinde(Path(tmp) / "u.sqlite")
        init_db(conn)
        # 3. Nur Zeilen ohne E-Mail: kein ImportFehler
        pfad = Path(tmp) / "c.csv"
        pfad.write_bytes("\r\n".join([KOPF_NEU, _ZEILEN_NEU[4]]).encode("utf-8"))
        e = importiere_csv(conn, pfad)
        assert (e.gesamt, e.neu, e.fehler, e.ohne_email) == (1, 0, 0, 1), e


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




def test_bezahlt_und_ota():
    """Komplett bezahlt: Bereich ohne Button; OTA-Buchung: anderer Nichtanreise-Satz."""
    from render import komplett_bezahlt, ota_buchung
    bez = dict(paymentlink=None, voraus_datum=None, schon_eingegangen=103.6, zahlbetrag=103.6, restbetrag=None)
    for sprache, text in (("de", "Deine Buchung ist bereits komplett bezahlt."), ("en", "Your booking has already been paid in full.")):
        for kanal in (None, "EGB", "BHC", "EHC"):
            d = (daten(sprache=sprache, kanal=kanal, **bez))
            assert zahlung_variante(d) == "bezahlt"
            h = render_mail(d)["html"]
            assert text in h and "pay.example" not in h and "Jetzt bezahlen" not in h and "Pay now" not in h
            assert "§" not in h and "&sect;" not in h          # kein AGB-Satz
    # nicht komplett bezahlt: kein Bereich
    for abw in (dict(zahlbetrag=None), dict(schon_eingegangen=None), dict(restbetrag=20.0), dict(zahlbetrag=0.0)):
        d = (daten(**{**bez, **abw}))
        assert zahlung_variante(d) is None and not komplett_bezahlt(d), abw
        assert "<h3" in render_mail(d)["html"] and "komplett bezahlt" not in render_mail(d)["html"]
    # Direkt (EGB/leer) vs. OTA
    for kanal, ota in ((None, False), ("EGB", False), ("BHC", True), ("EHC", True), ("TRIP", True)):
        d = (daten(kanal=kanal))
        assert ota_buchung(d) == ota
        h_de = render_mail(d)["html"]
        assert ("auf der Du gebucht hast" in h_de) == ota and ("sag uns bitte kurz Bescheid" in h_de) == (not ota)
        h_en = render_mail((daten(kanal=kanal, sprache="en")))["html"]
        assert ("the website you booked on" in h_en) == ota and ("please let us know" in h_en) == (not ota)


ROOM_DE = ("2026-11-18 3 x Frühstück 7.90 0.00 23.70|2026-11-18 3 x 10-Bettzimmer mixed 21.89 10.00 59.10"
           "|2026-11-19 1 x 4-Bettzimmer female 27.20 0.00 27.20")
ROOM_EN = ("2026-11-17 -1 x Internet Deposit 9.09 0.00 -9.09|2026-11-17 1 x 10 bed mixed room 19.85 0.00 19.85"
           "|2026-11-18 1 x 10 bed mixed room 19.85 0.00 19.85|2026-11-19 1 x 10 bed mixed room 20.90 0.00 20.90")


def test_leistungen():
    """Spalte room: Zerlegen, Darstellung DE/EN, Rabatt, Anzahl > 1, Portal-Abzug, Gesamtbetrag."""
    from leistungen import (bezeichnung, gesamtsumme, ist_mehrbettzimmer, leistungen_html, parse_leistungen,
                            rate_zusatz)
    pos = parse_leistungen(ROOM_DE)
    assert [(p.anzahl, p.name, p.gesamt) for p in pos] == [
        (3, "Frühstück", 23.7), (3, "10-Bettzimmer mixed", 59.1), (1, "4-Bettzimmer female", 27.2)]
    assert pos[1].rabatt == 10.0 and abs(pos[1].anzahl * pos[1].einzelpreis * 0.9 - pos[1].gesamt) < 0.01
    # unlesbar/leer -> None (keine halbe Liste)
    for kaputt in (None, "", "  ", "kaputt", ROOM_DE + "|2026-11-19 x", "2026-11-19 1 x Zimmer abc 0.00 1"):
        assert parse_leistungen(kaputt) is None, kaputt
    # Zimmerliste: DE-Namen wie vom Hostel festgelegt
    assert bezeichnung("10-Bettzimmer mixed", "de") == "Bett im 10-Bettzimmer gender-mixed"
    assert bezeichnung("6-Bettzimmer mixed", "de") == "Bett im 6-Bettzimmer gender-mixed"
    assert bezeichnung("4-Bettzimmer mixed", "de") == "Bett im 4-Bettzimmer gender-mixed"
    assert bezeichnung("4-Bettzimmer female", "de") == "Bett im 4-Bettzimmer für Frauen"
    assert bezeichnung("4-Bettzimmer male", "de") == "Bett im 4-Bettzimmer für Männer"
    for unveraendert in ("Einzelzimmer", "Doppelzimmer", "Doppelzimmer Studio", "4-Bettzimmer privat", "6-Bettzimmer privat",
                         "Schlafcouch", "Frühstück"):
        assert bezeichnung(unveraendert, "de") == unveraendert, unveraendert
    assert bezeichnung("Appartement", "de") == "Apartment für 4" and bezeichnung("Appartement", "en") == "Apartment for 4"
    # EN: Mehrbettzimmer als Bett im Zimmer, Rest wie geliefert
    assert bezeichnung("10 bed mixed room", "en") == "Bed in 10-bed gender-mixed room"
    assert bezeichnung("4 bed female room", "en") == "Bed in 4-bed female only room"
    assert bezeichnung("4 bed room female only", "en") == "Bed in 4-bed female only room"
    assert bezeichnung("4 bed room male only", "en") == "Bed in 4-bed male only room"
    assert bezeichnung("4 bed room female only", "de") == "Bett im 4-Bettzimmer für Frauen"
    assert ist_mehrbettzimmer("4 bed room female only")
    assert bezeichnung("Single room", "en") == "Single room" and bezeichnung("Double room", "en") == "Double room"
    # ASSD-Name in der anderen Sprache geliefert: trotzdem passend
    assert bezeichnung("6 bed mixed room", "de") == "Bett im 6-Bettzimmer gender-mixed"
    # Unbekannter Name: im Deutschen mixed/female ersetzen, sonst unverändert
    assert bezeichnung("Suite mixed", "de") == "Suite gemischt" and bezeichnung("Suite mixed", "en") == "Suite mixed"
    assert ist_mehrbettzimmer("10-Bettzimmer mixed") and ist_mehrbettzimmer("4 bed female room")
    assert not ist_mehrbettzimmer("4-Bettzimmer privat") and not ist_mehrbettzimmer("Doppelzimmer")
    assert rate_zusatz("10-Bettzimmer mixed", "nrf", "de") == ", nicht erstattbar"
    assert rate_zusatz("10-Bettzimmer mixed", "flex", "de") == ", erstattbar"
    assert rate_zusatz("10 bed mixed room", "nrf", "en") == ", non-refundable"
    assert rate_zusatz("10 bed mixed room", "flex", "en") == ", refundable"
    for keins in (None, "fix", "ota"):
        assert rate_zusatz("10-Bettzimmer mixed", keins, "de") == "", keins
    assert rate_zusatz("Doppelzimmer", "nrf", "de") == ", nicht erstattbar"
    assert rate_zusatz("Einzelzimmer", "flex", "de") == ", erstattbar"
    assert rate_zusatz("Apartment", "nrf", "en") == ", non-refundable" and rate_zusatz("Appartement", "flex", "de") == ", erstattbar"
    assert rate_zusatz("6-Bettzimmer privat", "nrf", "de") == ", nicht erstattbar"
    assert rate_zusatz("Double room", "flex", "en") == ", refundable"
    for kein in ("Frühstück", "Schlafcouch", "Internet Deposit", "Breakfast"):
        assert rate_zusatz(kein, "nrf", "de") == "" and rate_zusatz(kein, "flex", "en") == "", kein
    assert bezeichnung("Internet Deposit", "de") == "Bereits über das Portal bezahlt"
    assert bezeichnung("Internet Deposit", "en") == "Already paid via the platform"
    de = leistungen_html(pos, 109.9, "de")
    assert "18.11.2026" in de and "3 &times; Bett im 10-Bettzimmer gender-mixed<br>" in de
    assert "3 &times; Bett im 10-Bettzimmer gender-mixed, erstattbar<br>" in leistungen_html(pos, 109.9, "de", "flex")
    assert "3 &times; Bett im 10-Bettzimmer gender-mixed, nicht erstattbar<br>" in leistungen_html(pos, 109.9, "de", "nrf")
    assert "1 &times; Bett im 4-Bettzimmer für Frauen, nicht erstattbar</td>" in leistungen_html(pos, 109.9, "de", "nrf")
    assert "3 &times; Frühstück<br>" in leistungen_html(pos, 109.9, "de", "nrf")      # kein Zusatz bei Frühstück
    assert "3 &times; 21,89 EUR = 65,67 EUR, Rabatt 10 %" in de and "59,10 EUR" in de and "3 &times; 7,90 EUR" in de
    assert "Gesamtbetrag" in de and "109,90 EUR" in de and "mixed" not in de.replace("gender-mixed", "")
    assert "Rabatt" not in leistungen_html(parse_leistungen("2026-11-18 1 x Einzelzimmer 46.90 0.00 46.90"), None, "de")
    en = leistungen_html(parse_leistungen(ROOM_EN), 51.51, "en")
    assert "Already paid via the platform" in en and "\u2212EUR 9.09" in en and "EUR 19.85" in en
    assert "Total" in en and "EUR 51.51" in en and "&times; Already" not in en
    assert en.index("17.11.2026") < en.index("19.11.2026")
    # Rabatt-Format, Gesamtbetrag aus der Summe, wenn Zahlbetrag fehlt
    assert "Rabatt 12,5 %" in leistungen_html(parse_leistungen("2026-11-18 1 x Zimmer 10.00 12.50 8.75"), None, "de")
    assert gesamtsumme(parse_leistungen(ROOM_EN), None) == 51.51 and gesamtsumme(pos, 5.0) == 5.0
    assert gesamtsumme(parse_leistungen("2026-11-18 -1 x Internet Deposit 9.09 0.00 -9.09"), None) is None
    # HTML-Escape
    assert "&lt;b&gt;" in leistungen_html(parse_leistungen("2026-11-18 1 x <b> 1.00 0.00 1.00"), None, "de")


def test_leistungen_mail_und_import():
    """Bereich in der Mail nur bei lesbarer Spalte; Import speichert den Rohtext."""
    from csv_import import importiere_csv
    from run_send import SendKonfig, leistungen_hinweis, mail_daten
    h = render_mail(daten(leistungen=ROOM_DE, zahlbetrag=109.9))
    assert "Leistungen:" in h["html"] and "3 &times; Bett im 10-Bettzimmer gender-mixed" in h["html"] and "109,90 EUR" in h["html"]
    assert "nicht erstattbar" not in h["html"] and "erstattbar" not in h["html"]            # daten(): keine Rate
    assert "gender-mixed, nicht erstattbar" in render_mail(daten(leistungen=ROOM_DE, zahlbetrag=109.9, rate="nrf"))["html"]
    assert "gender-mixed, erstattbar" in render_mail(daten(leistungen=ROOM_DE, zahlbetrag=109.9, rate="flex"))["html"]
    assert "Leistungen:" in h["text"] and "Gesamtbetrag" in h["text"] and "18.11.2026 3" in h["text"]
    he = render_mail(daten(leistungen=ROOM_EN, zahlbetrag=51.51, sprache="en"))
    assert "Services:" in he["html"] and "EUR 51.51" in he["html"]
    for kein in (None, "", "kaputt"):
        hh = render_mail(daten(leistungen=kein))["html"]
        assert "Leistungen:" not in hh and "{{" not in hh and "BLOCK:" not in hh
    kopf = KOPF_NEU.replace(";Paylink", ";room;Paylink")
    zeile = ("     90100;410200-20-1;I01;2026-11-13 00:00:00;2026-11-14 00:00:00;2026-07-01;3;Anna;Test;;;;a@example.com;D;EGB;FLEX;"
             "0.00;0000-00-00;0.00;82.8;82.80;" + ROOM_DE.split("|")[0] + "|" + ROOM_DE.split("|")[1] + ";https://pay.example/1")
    kaputt = zeile.replace("90100", "90101").replace("a@example.com", "b@example.com").replace("Frühstück 7.90", "Frühstück sieben")
    ohne = "     90102;;I03;2026-11-13 00:00:00;2026-11-14 00:00:00;2026-07-01;1;Fia;Test;;;;c@example.com;D;;;0.00;0000-00-00;0.00;;;;"
    with tempfile.TemporaryDirectory() as tmp:
        pfad = Path(tmp) / "r.csv"
        pfad.write_bytes("\r\n".join([kopf, zeile, kaputt, ohne]).encode("utf-8"))
        conn = verbinde(Path(tmp) / "r.sqlite")
        init_db(conn)
        e = importiere_csv(conn, pfad)
        assert (e.gesamt, e.neu, e.fehler) == (3, 3, 0), e
        z = conn.execute("SELECT * FROM arrivals WHERE reg_nummer='90100'").fetchone()
        assert z["leistungen"].startswith("2026-11-18 3 x Frühstück") and z["zahlbetrag"] == 82.8
        d = mail_daten(z, SendKonfig(), ev.EventQuelle(""), date(2026, 11, 10))
        assert "3 &times; Bett im 10-Bettzimmer gender-mixed, erstattbar" in render_mail(d)["html"]      # Rate FLEX
        assert leistungen_hinweis(z) is None
        k = conn.execute("SELECT * FROM arrivals WHERE reg_nummer='90101'").fetchone()
        assert "nicht lesbar" in leistungen_hinweis(k)
        assert "Leistungen:" not in render_mail(mail_daten(k, SendKonfig(), ev.EventQuelle(""), date(2026, 11, 10)))["html"]
        o = conn.execute("SELECT * FROM arrivals WHERE reg_nummer='90102'").fetchone()
        assert o["leistungen"] is None and leistungen_hinweis(o) is None
        conn.close()


def test_storno_und_aktualitaet():
    """Gäste, die in der letzten CSV fehlen (storniert), und veraltete Importe verhindern den Versand."""
    from datetime import timedelta
    with tempfile.TemporaryDirectory() as tmp:
        conn = verbinde(Path(tmp) / "s.sqlite")
        init_db(conn)
        _gast(conn, "1", "Bleibt")                          # Import 1 (Vortag-Stand), dann auch in Import 2
        _gast(conn, "2", "Storniert", email_="b@example.com")   # nur in Import 1
        conn.execute("INSERT INTO import_log (id, dateiname, sha256, importiert_am) VALUES (2, 't2', 't2', ?)",
                     (HEUTE.isoformat() + " 06:10:00",))
        conn.execute("UPDATE arrivals SET import_id = 2 WHERE reg_nummer = '1'")
        conn.commit()
        k = SendKonfig(vorschau_dir=Path(tmp) / "v")
        q = ev.EventQuelle("")
        assert [z["reg_nummer"] for z in faellige_gaeste(conn, HEUTE, 3)] == ["1"]
        assert [z["reg_nummer"] for z in faellige_gaeste(conn, HEUTE, 3, nur_letzter_import=False)] == ["1", "2"]
        e = verarbeite(conn, k, q, HEUTE, "dry")
        assert e.vorschau == 1 and any("Gast 2" in d and "nicht mehr in der aktuellen CSV" in d for d in e.details), e
        assert not (Path(tmp) / "v" / "2_de.html").exists()
        gesendet = []
        def sender(msg):
            gesendet.append(msg)
            return msg["Message-ID"]
        e = verarbeite(conn, k, q, HEUTE, "live", sender)
        assert e.gesendet == 1 and gesendet[0]["To"] == "a@example.com", e
        assert conn.execute("SELECT COUNT(*) FROM mail_log").fetchone()[0] == 1       # Stornierter ohne Log
        # Veralteter Import: live sendet nichts und meldet Fehler, Trockenlauf warnt nur
        _gast(conn, "3", "Neu", import_id=2)
        morgen = HEUTE + timedelta(days=1)
        e = verarbeite(conn, k, q, morgen, "live", sender)
        assert e.gesendet == 0 and e.fehler == 1 and "1 Tag(e) alt" in e.details[0], e
        e = verarbeite(conn, k, q, morgen, "dry")
        assert e.fehler == 0 and e.vorschau >= 0 and "1 Tag(e) alt" in e.details[0]
        # Toleranz einstellbar
        assert import_pruefung(conn, morgen, 1) is None and import_pruefung(conn, morgen, 0) is not None
        assert import_pruefung(conn, HEUTE, 0) is None
        # Ohne Import gar kein Versand
        leer = verbinde(Path(tmp) / "leer.sqlite")
        init_db(leer)
        assert "keinen CSV-Import" in import_pruefung(leer, HEUTE, 0) and faellige_gaeste(leer, HEUTE, 3) == []
        conn.close(); leer.close()


def test_gleiche_csv_erneut():
    """Dieselbe Datei nochmals: Zeitstempel wird aufgefrischt; Wiederkehr eines älteren Stands wird neu eingelesen."""
    from csv_import import importiere_csv
    with tempfile.TemporaryDirectory() as tmp:
        conn = verbinde(Path(tmp) / "g.sqlite")
        init_db(conn)
        a, b = Path(tmp) / "a.csv", Path(tmp) / "b.csv"
        a.write_bytes((KOPF_NEU + "\r\n" + _ZEILEN_NEU[0] + "\r\n" + _ZEILEN_NEU[1] + "\r\n").encode("utf-8"))
        b.write_bytes((KOPF_NEU + "\r\n" + _ZEILEN_NEU[1] + "\r\n").encode("utf-8"))      # Gast 90001 fehlt
        e1 = importiere_csv(conn, a)
        conn.execute("UPDATE import_log SET importiert_am = '2020-01-01 06:00:00'")
        conn.commit()
        e2 = importiere_csv(conn, a)                         # gleiche Datei, ist der letzte Import
        assert e2.bereits_importiert and e2.import_id == e1.import_id
        assert letzter_import(conn)["importiert_am"][:4] != "2020"
        e3 = importiere_csv(conn, b)                          # anderer Stand
        assert not e3.bereits_importiert and letzter_import(conn)["id"] == e3.import_id
        e4 = importiere_csv(conn, a)                          # alter Stand kehrt zurück: neu einlesen
        assert not e4.bereits_importiert and letzter_import(conn)["id"] == e4.import_id
        assert {r["import_id"] for r in conn.execute("SELECT import_id FROM arrivals")} == {e4.import_id}
        assert importiere_csv(conn, a).bereits_importiert
        conn.close()


def test_mehrfach_aufenthalt():
    """Zwei getrennte Aufenthalte in einer Buchung: keine Gästemail, Warn-Mail an die Absenderadresse, einmalig."""
    from leistungen import aufenthalte, parse_leistungen
    from run_send import mehrfach_aufenthalte
    zimmer = "{d} 1 x 10-Bettzimmer mixed 29.40 10.00 26.46"
    spaet = "|".join(zimmer.format(d=d) for d in ("2026-10-09", "2026-10-10", "2026-12-12"))
    lueckenlos = "|".join(zimmer.format(d=d) for d in ("2026-10-09", "2026-10-10", "2026-10-11"))
    wechsel = ("2026-10-09 1 x Einzelzimmer 50.00 0.00 50.00|2026-10-09 2 x Frühstück 8.00 0.00 16.00"
               "|2026-10-10 1 x Doppelzimmer 70.00 0.00 70.00|2026-10-10 -1 x Internet Deposit 9.09 0.00 -9.09")
    assert aufenthalte(parse_leistungen(spaet)) == [(date(2026, 10, 9), date(2026, 10, 10)), (date(2026, 12, 12), date(2026, 12, 12))]
    assert len(aufenthalte(parse_leistungen(lueckenlos))) == 1
    assert len(aufenthalte(parse_leistungen(wechsel))) == 1            # Zimmerwechsel ohne Lücke, Frühstück/Abzug zählen nicht
    with tempfile.TemporaryDirectory() as tmp:
        conn = verbinde(Path(tmp) / "t.sqlite")
        init_db(conn)
        _gast(conn, "1", "Normal")
        _gast(conn, "2", "Doppelt", email_="d@example.com")
        _gast(conn, "3", "Wechsel", email_="w@example.com")
        for reg, roh in (("1", lueckenlos), ("2", spaet), ("3", wechsel)):
            conn.execute("UPDATE arrivals SET leistungen=?, buchungsnummer=? WHERE reg_nummer=?", (roh, "13" + reg, reg))
        conn.commit()
        assert mehrfach_aufenthalte(conn.execute("SELECT * FROM arrivals WHERE reg_nummer='2'").fetchone())
        k = SendKonfig(absender="info@hostel.example", vorschau_dir=Path(tmp) / "v")
        q = ev.EventQuelle("")
        e = verarbeite(conn, k, q, HEUTE, "dry")
        assert (e.vorschau, e.warnungen, e.fehler) == (2, 1, 0), e
        warn = (Path(tmp) / "v" / "WARNUNG_2.txt").read_text(encoding="utf-8")
        assert "132" in warn and "12.12.2026" in warn and "NICHT" in warn and "d@example.com" not in warn
        assert not (Path(tmp) / "v" / "2_de.html").exists()

        gesendet = []
        def sender(msg):
            gesendet.append(msg)
            return msg["Message-ID"]
        e = verarbeite(conn, k, q, HEUTE, "test", sender, "test@example.com")
        assert e.gesendet == 2 and e.warnungen == 1 and all(m["To"] == "test@example.com" for m in gesendet)
        assert any("nicht verschickt" in m["Subject"] and m["Subject"].startswith("[TEST]") for m in gesendet)
        gesendet.clear()

        e = verarbeite(conn, k, q, HEUTE, "live", sender)
        an = sorted(m["To"] for m in gesendet)
        assert an == ["a@example.com", "info@hostel.example", "w@example.com"], an       # Gast 2 bekommt keine Mail
        assert not any(m["To"] == "d@example.com" for m in gesendet)
        st = conn.execute("SELECT status, fehler FROM mail_log m JOIN arrivals a ON a.id=m.arrival_id WHERE a.reg_nummer='2'").fetchone()
        assert st["status"] == "uebersprungen" and st["fehler"].startswith("Mehrere Aufenthalte"), dict(st)
        # einmalig: zweiter Lauf schickt keine weitere Warnung
        gesendet.clear()
        e = verarbeite(conn, k, q, HEUTE, "live", sender)
        assert gesendet == [] and e.warnungen == 0, e
        # in ASSD korrigiert (Lücke weg): Sperre fällt, Gast bekommt die Mail
        conn.execute("UPDATE arrivals SET leistungen=? WHERE reg_nummer='2'", (lueckenlos,))
        conn.commit()
        e = verarbeite(conn, k, q, HEUTE, "live", sender)
        assert [m["To"] for m in gesendet] == ["d@example.com"] and e.warnungen == 0, [m["To"] for m in gesendet]
        # Versand der Warn-Mail schlägt fehl: Gast bleibt offen (fehler), kein Gästemail
        _gast(conn, "4", "Doppelt2", email_="x@example.com")
        conn.execute("UPDATE arrivals SET leistungen=? WHERE reg_nummer='4'", (spaet,))
        conn.commit()
        def kaputt(msg):
            raise VersandFehler("HTTP 503", False)
        e = verarbeite(conn, k, q, HEUTE, "live", kaputt)
        assert e.fehler == 1 and e.gesendet == 0, e
        assert conn.execute("SELECT status FROM mail_log m JOIN arrivals a ON a.id=m.arrival_id WHERE a.reg_nummer='4'").fetchone()[0] == "fehler"


def test_vcc():
    from run_send import mail_daten, zahlung_hinweis
    """CSV-Spalte VCC = yes: Buchung ist mit virtueller Kreditkarte bezahlt -> Bereich 'bezahlt', kein Paylink/Button."""
    from csv_import import _vcc, importiere_csv
    assert [_vcc(x) for x in ("yes", "Yes", "ja", "1", "no", "", None, "nein")] == [1, 1, 1, 1, 0, 0, 0, 0]
    # Paylink und offener Restbetrag in der CSV, aber VCC: bezahlt
    d = daten(vcc=True, schon_eingegangen=None, restbetrag=17.74, zahlbetrag=17.74, voraus_datum=None, kanal="CTR")
    assert zahlung_variante(d) == "bezahlt" and zahlung_aktiv(d)
    d_de = render_mail(d)["html"]
    assert "Deine Buchung ist bereits komplett bezahlt." in d_de and "Jetzt bezahlen" not in d_de and "pay.example" not in d_de
    h_en = render_mail(daten(vcc=True, sprache="en", voraus_datum=None))["html"]
    assert "paid in full" in h_en and "Pay now" not in h_en and "pay.example" not in h_en
    # ohne VCC bleibt alles wie bisher
    assert zahlung_variante(daten(vcc=False, voraus_datum=None, restbetrag=17.74, zahlbetrag=17.74)) == "gesamt"
    # Import: Spalte VCC zwischen Schon_eingegangen und Restbetrag, Reihenfolge der Spalten egal
    kopf = KOPF_NEU.replace(";Restbetrag", ";VCC;Restbetrag")
    z1 = "     91001;;111;2026-11-22 00:00:00;2026-11-23 00:00:00;2026-10-19;1;Pia;Test;;;;a@example.com;E;CTR;OTA;0.00;0000-00-00;0.00;yes;17.74;17.74;https://pay.example/1"
    z2 = "     91002;;112;2026-11-22 00:00:00;2026-11-23 00:00:00;2026-10-19;1;Paul;Test;;;;b@example.com;E;BHC;OTA;0.00;0000-00-00;0.00;no;20.16;20.16;https://pay.example/2"
    with tempfile.TemporaryDirectory() as tmp:
        conn = verbinde(Path(tmp) / "v.sqlite")
        init_db(conn)
        pfad = Path(tmp) / "v.csv"
        pfad.write_bytes("\r\n".join([kopf, z1, z2]).encode("utf-8"))
        e = importiere_csv(conn, pfad)
        assert (e.neu, e.fehler) == (2, 0), e
        v = {r["reg_nummer"]: r["vcc"] for r in conn.execute("SELECT reg_nummer, vcc FROM arrivals")}
        assert v == {"91001": 1, "91002": 0}, v
        k = SendKonfig()
        q = ev.EventQuelle("")
        for reg, soll in (("91001", "bezahlt"), ("91002", "gesamt")):
            row = conn.execute("SELECT * FROM arrivals WHERE reg_nummer=?", (reg,)).fetchone()
            dd = mail_daten(row, k, q, date(2026, 11, 19))
            assert zahlung_variante(dd) == soll, (reg, zahlung_variante(dd))
            assert zahlung_hinweis(row) is None, reg


def test_eingecheckt():
    """CHECKIO = 1: Gast ist schon eingecheckt, keine Mail (im Live-Lauf als 'uebersprungen' protokolliert)."""
    from csv_import import _eingecheckt, importiere_csv
    assert [_eingecheckt(z) for z in ({"CHECKIO": "1"}, {"CHECKIN": "O", "CHECKIO": "1"}, {"CHECKIN": "O", "CHECKIO": "0"},
                                      {"CHECKIN": "O", "CHECKIO": ""}, {"CHECKIN": "1"}, {})] == [1, 1, 0, 0, None, None]
    kopf = KOPF_NEU.replace(";Restbetrag", ";CHECKIN;CHECKIO;Restbetrag")
    z = "     {r};;1;2026-10-09 00:00:00;2026-10-11 00:00:00;2026-10-02;1;{n};Test;;;;{m}@example.com;E;;;0.00;0000-00-00;0.00;{ci};{io};20.00;20.00;https://pay.example/{r}"
    zeilen = [z.format(r="92001", n="Drin", m="a", ci="O", io="1"), z.format(r="92002", n="Drin2", m="b", ci="", io="1"),
              z.format(r="92003", n="Draussen", m="c", ci="O", io="0")]
    with tempfile.TemporaryDirectory() as tmp:
        conn = verbinde(Path(tmp) / "c.sqlite")
        init_db(conn)
        pfad = Path(tmp) / "c.csv"
        pfad.write_bytes("\r\n".join([kopf] + zeilen).encode("utf-8"))
        e = importiere_csv(conn, pfad)
        assert (e.neu, e.fehler) == (3, 0), e
        assert {r["reg_nummer"]: r["eingecheckt"] for r in conn.execute("SELECT reg_nummer, eingecheckt FROM arrivals")} == \
            {"92001": 1, "92002": 1, "92003": 0}
        k = SendKonfig(vorschau_dir=Path(tmp) / "v")
        q = ev.EventQuelle("")
        e = verarbeite(conn, k, q, HEUTE, "dry")
        assert (e.vorschau, e.uebersprungen) == (1, 2), e
        assert (Path(tmp) / "v" / "92003_en.html").is_file() and not (Path(tmp) / "v" / "92001_en.html").exists()
        gesendet = []
        def sender(msg):
            gesendet.append(msg)
            return msg["Message-ID"]
        e = verarbeite(conn, k, q, HEUTE, "live", sender)
        assert [m["To"] for m in gesendet] == ["c@example.com"] and e.gesendet == 1 and e.uebersprungen == 2, e
        st = {r["reg_nummer"]: (r["status"], r["fehler"]) for r in conn.execute(
            "SELECT a.reg_nummer, m.status, m.fehler FROM mail_log m JOIN arrivals a ON a.id=m.arrival_id")}
        assert st["92001"] == ("uebersprungen", "bereits eingecheckt") and st["92003"][0] == "gesendet", st
        gesendet.clear()
        assert verarbeite(conn, k, q, HEUTE, "live", sender).gesendet == 0 and gesendet == []


def test_ausnahmeliste():
    """Adressen und Domains auf der Ausnahmeliste bekommen keine Mail (ohne Log-Eintrag, wirkt sofort wieder nach Streichen)."""
    from run_send import ist_ausgenommen, lade_ausnahmen
    with tempfile.TemporaryDirectory() as tmp:
        liste = Path(tmp) / "aus.txt"
        assert lade_ausnahmen(liste) == frozenset()                       # Datei fehlt: keine Ausnahmen
        liste.write_bytes("\ufeff# Kommentar\r\n\r\nSupport@Example.com  # Trip.com\r\n@domain.example\r\n   \r\n#nur@kommentar.de\r\n".encode("utf-8"))
        aus = lade_ausnahmen(liste)
        assert aus == frozenset({"support@example.com", "@domain.example"}), aus
        assert ist_ausgenommen("SUPPORT@example.com ", aus) and ist_ausgenommen("x@Domain.Example", aus)
        assert not ist_ausgenommen("a@example.com", aus) and not ist_ausgenommen("", aus) and not ist_ausgenommen(None, aus)
        conn = verbinde(Path(tmp) / "t.sqlite")
        init_db(conn)
        _gast(conn, "1", "Normal")
        _gast(conn, "2", "Support", email_="support@example.com")
        _gast(conn, "3", "Domain", email_="y@domain.example")
        k = SendKonfig(vorschau_dir=Path(tmp) / "v", ausnahmen=aus)
        q = ev.EventQuelle("")
        e = verarbeite(conn, k, q, HEUTE, "dry")
        assert (e.vorschau, e.uebersprungen) == (1, 2), e
        gesendet = []
        def sender(msg):
            gesendet.append(msg)
            return msg["Message-ID"]
        e = verarbeite(conn, k, q, HEUTE, "live", sender)
        assert [m["To"] for m in gesendet] == ["a@example.com"] and e.uebersprungen == 2, e
        assert conn.execute("SELECT COUNT(*) FROM mail_log").fetchone()[0] == 1       # nur der gesendete Gast
        # Eintrag gestrichen: die beiden bekommen ihre Mail beim nächsten Lauf
        gesendet.clear()
        k.ausnahmen = frozenset()
        e = verarbeite(conn, k, q, HEUTE, "live", sender)
        assert sorted(m["To"] for m in gesendet) == ["support@example.com", "y@domain.example"], [m["To"] for m in gesendet]


def test_startregel():
    """SEND_ANREISE_AB: Gäste mit früherer Anreise bekommen keine Mail, die übrigen schon; ungültiges Datum = Konfigfehler."""
    import os
    from run_send import lese_konfig
    with tempfile.TemporaryDirectory() as tmp:
        conn = verbinde(Path(tmp) / "t.sqlite")
        init_db(conn)
        _gast(conn, "1", "Heute", anreise="2026-10-07", email_="h@example.com")
        _gast(conn, "2", "Morgen", anreise="2026-10-08", email_="m@example.com")
        _gast(conn, "3", "Spaeter", anreise="2026-10-10", email_="s@example.com")
        q = ev.EventQuelle("")
        gesendet = []
        def sender(msg):
            gesendet.append(msg)
            return msg["Message-ID"]
        k = SendKonfig(anreise_ab=date(2026, 10, 8))
        assert [z["reg_nummer"] for z in faellige_gaeste(conn, HEUTE, 3, anreise_ab=k.anreise_ab)] == ["2", "3"]
        e = verarbeite(conn, k, q, HEUTE, "live", sender)
        assert sorted(m["To"] for m in gesendet) == ["m@example.com", "s@example.com"] and e.gesendet == 2, e
        assert any("vor 2026-10-08" in d for d in e.details), e.details
        # ohne Startregel (oder vergangenes Datum) ist der Gast von heute wieder fällig
        gesendet.clear()
        e = verarbeite(conn, SendKonfig(anreise_ab=date(2026, 1, 1)), q, HEUTE, "live", sender)
        assert [m["To"] for m in gesendet] == ["h@example.com"], [m["To"] for m in gesendet]
    alt = {n: os.environ.get(n) for n in ("SEND_ANREISE_AB", "DB_PATH")}
    try:
        os.environ["SEND_ANREISE_AB"] = "2026-12-01"
        assert lese_konfig().anreise_ab == date(2026, 12, 1)
        os.environ["SEND_ANREISE_AB"] = ""
        assert lese_konfig().anreise_ab is None
        os.environ["SEND_ANREISE_AB"] = "morgen"
        try:
            lese_konfig()
            raise AssertionError("RuntimeError erwartet")
        except RuntimeError as exc:
            assert "JJJJ-MM-TT" in str(exc)
    finally:
        for n, v in alt.items():
            os.environ.pop(n, None) if v is None else os.environ.__setitem__(n, v)


def test_graph_zugang():
    """Eigene Versand-App (GRAPH_*) hat Vorrang vor AZURE_*, kein Mix aus beiden Apps."""
    import os
    from run_send import graph_zugang
    namen = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET")
    alt = {n: os.environ.get(n) for n in namen}
    def setze(**kw):
        for n in namen:
            os.environ.pop(n, None)
        os.environ.update(kw)
    try:
        setze(AZURE_TENANT_ID="t", AZURE_CLIENT_ID="a", AZURE_CLIENT_SECRET="as")
        assert graph_zugang() == ("t", "a", "as")                                            # nur AZURE_*: wie bisher
        setze(AZURE_TENANT_ID="t", AZURE_CLIENT_ID="a", AZURE_CLIENT_SECRET="as", GRAPH_CLIENT_ID="g", GRAPH_CLIENT_SECRET="gs")
        assert graph_zugang() == ("t", "g", "gs")                                            # GRAPH_* gewinnt, Tenant von AZURE
        setze(GRAPH_TENANT_ID="t2", GRAPH_CLIENT_ID="g", GRAPH_CLIENT_SECRET="gs")
        assert graph_zugang() == ("t2", "g", "gs")
        setze(AZURE_TENANT_ID="t", AZURE_CLIENT_ID="a", AZURE_CLIENT_SECRET="as", GRAPH_CLIENT_ID="g")
        for fall in ("GRAPH_CLIENT_ID",):                                                    # Secret fehlt: Fehler statt Mix
            try:
                graph_zugang()
                raise AssertionError("RuntimeError erwartet")
            except RuntimeError as exc:
                assert "GRAPH_CLIENT_SECRET" in str(exc)
        setze()
        try:
            graph_zugang()
            raise AssertionError("RuntimeError erwartet")
        except RuntimeError as exc:
            assert "fehlen" in str(exc)
    finally:
        for n, v in alt.items():
            os.environ.pop(n, None) if v is None else os.environ.__setitem__(n, v)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
    print("Alle Tests bestanden.")
