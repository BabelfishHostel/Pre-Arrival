#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
events.py - Veranstaltungen aus dem öffentlichen Google Sheet für die Mail.

Die Mail darf nie an einem Sheet-Problem scheitern: jede Störung führt zum
Fallback-Satz mit Link auf die Veranstaltungsseite.

ANNAHME zu den Spaltennamen (bitte mit dem echten Sheet abgleichen, siehe SPALTEN):
Status, Startdatum, Enddatum, Uhrzeit, Titel (DE), Titel EN, Ort, Kategorie.
"""

import csv
import html
import io
import logging
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional

from render import MONAT, WOCHENTAG

log = logging.getLogger("events")

# Mögliche Kopfzeilen je Feld (erste Übereinstimmung gewinnt, Groß-/Kleinschreibung egal)
SPALTEN = {
    "status": ["status"],
    "start": ["startdatum", "start", "datum"],
    "ende": ["enddatum", "ende"],
    "zeit": ["uhrzeit", "zeit", "beginn"],
    "titel": ["titel de", "titel", "veranstaltung"],
    "titel_en": ["titel en"],
    "ort": ["ort", "location", "veranstaltungsort"],
    "kategorie": ["kategorie"],
}
BEVORZUGT = ("konzert", "ausstellung", "sport")          # Vorrang vor Messe / Uni & Kongress
AUSGESCHLOSSENE_ORTE = ("schweinfurt", "bamberg", "nürnberg", "frankfurt", "aschaffenburg")
MAX_EINTRAEGE, MAX_PRO_TAG, MAX_PRO_KATEGORIE_TAG = 3, 2, 1
ABRUF_TIMEOUT = 8
DATUMSFORMATE = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d.%m.")


def sheet_url(sheet_id: str, tab: str = "Veranstaltungen") -> str:
    q = urllib.parse.urlencode({"tqx": "out:csv", "sheet": tab})
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?{q}"


def lade_csv(url: str) -> str:
    with urllib.request.urlopen(url, timeout=ABRUF_TIMEOUT) as r:  # noqa: S310 (feste Google-URL)
        return r.read().decode("utf-8-sig")


def _datum(text: str, jahr: int) -> Optional[date]:
    text = (text or "").strip().split(" ")[0]
    for fmt in DATUMSFORMATE:
        try:
            d = datetime.strptime(text, fmt)
            return d.date().replace(year=jahr) if fmt == "%d.%m." else d.date()
        except ValueError:
            continue
    return None


def _spaltenmap(kopf: List[str]) -> Dict[str, Optional[int]]:
    norm = [k.strip().lower() for k in kopf]
    return {feld: next((norm.index(n) for n in namen if n in norm), None) for feld, namen in SPALTEN.items()}


def lese_events(csv_text: str, jahr: int) -> List[dict]:
    """Parst die Sheet-CSV in Dicts (nur Status 'freigegeben', gültiges Startdatum)."""
    zeilen = list(csv.reader(io.StringIO(csv_text)))
    if not zeilen:
        return []
    m = _spaltenmap(zeilen[0])
    if m["status"] is None or m["start"] is None or m["titel"] is None:
        raise ValueError(f"Sheet-Kopfzeile nicht erkannt: {zeilen[0]}")

    def feld(z, name):
        i = m[name]
        return z[i].strip() if i is not None and i < len(z) else ""

    out = []
    for z in zeilen[1:]:
        if feld(z, "status").lower() != "freigegeben":
            continue
        start = _datum(feld(z, "start"), jahr)
        if not start:
            continue
        kat = feld(z, "kategorie")
        ende = _datum(feld(z, "ende"), jahr) or start
        if kat.lower().startswith("sport"):
            ende = start  # Sport: Enddatum ist das offene Spieltermin-Fenster
        out.append({
            "start": start, "ende": max(ende, start), "zeit": feld(z, "zeit"),
            "titel": feld(z, "titel"), "titel_en": feld(z, "titel_en"),
            "ort": feld(z, "ort"), "kategorie": kat,
        })
    return out


def waehle_events(events: List[dict], anreise: date) -> List[dict]:
    """Filtert und priorisiert die Treffer für Anreisetag und Folgetag."""
    tage = (anreise, anreise + timedelta(days=1))
    treffer, gesehen = [], set()
    for e in events:
        if not any(e["start"] <= t <= e["ende"] for t in tage):
            continue
        if any(x in e["ort"].lower() for x in AUSGESCHLOSSENE_ORTE):
            continue
        schluessel = (e["titel"].lower(), e["start"])
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        treffer.append(e)

    def tag(e):  # Anzeigetag: Beginn, bei laufenden Mehrtagesevents der Anreisetag
        return max(e["start"], anreise)

    treffer.sort(key=lambda e: (not e["kategorie"].lower().startswith(BEVORZUGT), tag(e), e["zeit"]))
    pro_tag, pro_kat, auswahl = {}, {}, []
    for e in treffer:
        d, k = tag(e), (tag(e), e["kategorie"].lower())
        if pro_tag.get(d, 0) >= MAX_PRO_TAG or pro_kat.get(k, 0) >= MAX_PRO_KATEGORIE_TAG:
            continue
        pro_tag[d] = pro_tag.get(d, 0) + 1
        pro_kat[k] = pro_kat.get(k, 0) + 1
        auswahl.append(e)
        if len(auswahl) == MAX_EINTRAEGE:
            break
    return sorted(auswahl, key=lambda e: (tag(e), e["zeit"]))


def _zeit_text(zeit: str, sprache: str) -> str:
    m = re.match(r"^(\d{1,2})[:.](\d{2})", zeit.strip())
    if not m:
        return ""
    h, mi = int(m.group(1)), m.group(2)
    if sprache == "de":
        return f"{h}:{mi} Uhr"
    if mi == "00":
        return f"{h % 12 or 12} {'am' if h < 12 else 'pm'}"
    return f"{h % 12 or 12}:{mi} {'am' if h < 12 else 'pm'}"


def events_html(auswahl: List[dict], anreise: date, sprache: str, eventseite_url: str = "") -> str:
    """Fertiges HTML für {{events}} (oder der Fallback-Satz ohne Treffer)."""
    if not auswahl:
        return fallback_html(sprache, eventseite_url)
    teile = []
    for e in auswahl:
        d = max(e["start"], anreise)
        wt, mo = WOCHENTAG[sprache][d.weekday()], MONAT[sprache][d.month - 1]
        tagtext = f"{wt}, {d.day}. {mo}" if sprache == "de" else f"{wt}, {d.day} {mo}"
        zeit = _zeit_text(e["zeit"], sprache)
        kopf = tagtext + (f" &middot; {zeit}" if zeit else "")
        titel = (e["titel_en"] if sprache == "en" and e["titel_en"] else e["titel"])
        ort = f'<br>\n  <span style="font-size:12px; color:#6b7a3a;">{html.escape(e["ort"])}</span>' if e["ort"] else ""
        teile.append(
            '<p style="margin:0 0 10px 0;">\n'
            f"  <strong>{kopf}</strong><br>\n  {html.escape(titel)}{ort}\n</p>"
        )
    return "\n".join(teile)


def fallback_html(sprache: str, url: str = "") -> str:
    if sprache == "de":
        satz = "Was in Deinem Reisezeitraum in Würzburg los ist, findest Du auf "
        link = f'<a href="{html.escape(url, quote=True)}">unserer Veranstaltungsseite</a>.' if url else "unserer Website."
    else:
        satz = "You can find out what's on in Würzburg during your stay on "
        link = f'<a href="{html.escape(url, quote=True)}">our events page</a>.' if url else "our website."
    return f'<p style="margin:0;">{satz}{link}</p>'


class EventQuelle:
    """Lädt das Sheet einmal pro Lauf (gecacht) und liefert das HTML je Anreisetag."""

    def __init__(self, sheet_id: str = "", eventseite_url: str = "", tab: str = "Veranstaltungen",
                 lader: Callable[[str], str] = lade_csv):
        self.sheet_id, self.url, self.tab, self.lader = sheet_id, eventseite_url, tab, lader
        self._events: Optional[List[dict]] = None
        self._fehler = False

    def _laden(self, jahr: int) -> List[dict]:
        if self._events is None:
            self._events = []
            if self.sheet_id:
                try:
                    self._events = lese_events(self.lader(sheet_url(self.sheet_id, self.tab)), jahr)
                except Exception as exc:  # bewusst breit: die Mail darf nie daran scheitern
                    self._fehler = True
                    log.warning("Event-Sheet nicht nutzbar, Fallback-Satz wird verwendet: %s", exc)
        return self._events

    def html(self, anreise: date, sprache: str) -> str:
        auswahl = waehle_events(self._laden(anreise.year), anreise)
        return events_html(auswahl, anreise, sprache, self.url)
