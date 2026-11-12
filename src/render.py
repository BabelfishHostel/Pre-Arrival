#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render.py - baut aus Vorlage und Gastdaten die fertige Mail.

Reine Funktionen ohne Netzwerk und ohne Datenbank, damit sie leicht testbar sind.
Wochentage und Monate sind fest hinterlegt (unabhängig von der Systemsprache).
"""

import html
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

VORLAGEN_DIR = Path(__file__).resolve().parent / "templates"

BETREFF = {
    "de": "Deine Anreise im Babelfish Hostel – alles Wichtige auf einen Blick",
    "en": "Your arrival at Babelfish Hostel – everything at a glance",
}
WOCHENTAG = {
    "de": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}
MONAT = {
    "de": ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}


def datum_lang(d: date, sprache: str) -> str:
    """'Fr, 9. Okt 2026' bzw. 'Fri, 9 Oct 2026'."""
    wt, mo = WOCHENTAG[sprache][d.weekday()], MONAT[sprache][d.month - 1]
    return f"{wt}, {d.day}. {mo} {d.year}" if sprache == "de" else f"{wt}, {d.day} {mo} {d.year}"


def datum_kurz(d: date, sprache: str) -> str:
    """'Mi, 7. Okt' bzw. 'Wed, 7 Oct' (ohne Jahr)."""
    wt, mo = WOCHENTAG[sprache][d.weekday()], MONAT[sprache][d.month - 1]
    return f"{wt}, {d.day}. {mo}" if sprache == "de" else f"{wt}, {d.day} {mo}"


def datum_ohne_wochentag(d: date, sprache: str) -> str:
    """'7. Okt 2026' bzw. '7 Oct 2026'."""
    mo = MONAT[sprache][d.month - 1]
    return f"{d.day}. {mo} {d.year}" if sprache == "de" else f"{d.day} {mo} {d.year}"


def betrag_text(betrag: float, sprache: str) -> str:
    """'528,00 EUR' bzw. 'EUR 528.00'."""
    if sprache == "de":
        return f"{betrag:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " EUR"
    return f"EUR {betrag:,.2f}"


def naechte_text(n: int, sprache: str) -> str:
    if sprache == "de":
        return f"{n} Nächte" if n != 1 else "1 Nacht"
    return f"{n} nights" if n != 1 else "1 night"


def gaeste_text(n: Optional[int], sprache: str) -> str:
    n = n or 1
    if sprache == "de":
        return f"{n} Gäste" if n != 1 else "1 Gast"
    return f"{n} guests" if n != 1 else "1 guest"


def anrede(vorname: Optional[str], sprache: str) -> str:
    """Anredezeile inkl. Fallback ('Hallo zusammen,' / 'Hi everyone,')."""
    gruss, fallback = ("Hallo", "Hallo zusammen,") if sprache == "de" else ("Hi", "Hi everyone,")
    name = (vorname or "").strip()
    if not name or any(c.isdigit() for c in name):
        return fallback
    erstes = re.split(r"\s+", name)[0].strip("-,.")
    if not erstes:
        return fallback
    if erstes.isupper() and len(erstes) > 1:
        erstes = erstes.title()
    return f"{gruss} {html.escape(erstes)},"


@dataclass
class MailDaten:
    """Alles, was die Vorlage eines Gasts braucht."""

    vorname: Optional[str]
    buchungsnummer: str
    anreise: date
    abreise: date
    personen: Optional[int]
    sprache: str            # 'de' | 'en'
    heute: date
    events_html: str
    paymentlink: Optional[str] = None       # leer = alles bezahlt, kein Zahlungsbereich
    voraus_datum: Optional[date] = None     # gesetzt = Vorauszahlung nötig (CSV Vorausdatum_1)
    schon_eingegangen: Optional[float] = None   # bisher bezahlt (CSV Schon_eingegangen), leer = 0
    kanal: Optional[str] = None             # CSV Kanal (EGB = eigene Website)
    rate: Optional[str] = None              # 'flex' / 'fix' / None (CSV Rate)
    storno_tage: int = 2                    # kostenfreie Stornierung bis N Tage vor Anreise


def zahlung_variante(d: MailDaten) -> Optional[str]:
    """Wählt den Zahlungsbereich: None, 'anzahlung', 'rest' oder 'gesamt'.

    Die Zahlungslogik liegt weitgehend bei ASSD (Spalten der CSV); Kanal und Rate zählen nur
    für den Sonderfall Website-Flex (siehe 'anzahlung').

    * Kein Paylink (alles bezahlt): kein Bereich.
    * Schon etwas bezahlt (Schon_eingegangen > 0): 'rest' (Restbetrag offen).
    * Noch nichts bezahlt, aber Vorauszahlung nötig: 'anzahlung'. Das gilt, wenn Vorausdatum gesetzt ist
      ODER (Sonderfall) die Buchung über die eigene Website kommt (Kanal EGB) und die Rate FLEX ist.
    * Sonst: 'gesamt' (keine Anzahlung nötig, Buchung kann im Voraus bezahlt werden).
    """
    if not d.paymentlink:
        return None
    if (d.schon_eingegangen or 0) > 0:
        return "rest"
    if d.voraus_datum is not None or (d.kanal == "EGB" and d.rate == "flex"):
        return "anzahlung"
    return "gesamt"


def zahlung_aktiv(d: MailDaten) -> bool:
    return zahlung_variante(d) is not None


def anzahlung_offen(d: MailDaten) -> bool:
    return zahlung_variante(d) == "anzahlung"


def storno_variante(d: MailDaten) -> Optional[str]:
    """Satz zur kostenfreien Stornierung: 'morgen', 'heute', 'datum' (später) oder None (abgelaufen)."""
    tage = (d.anreise - timedelta(days=d.storno_tage) - d.heute).days
    if tage < 0:
        return None
    return {0: "heute", 1: "morgen"}.get(tage, "datum")


def _block_behalten(text: str, name: str) -> str:
    muster = rf"[ \t]*<!-- BLOCK:{name}:(START|END) -->[ \t]*\n?"
    return re.sub(muster, "", text)


def _block_entfernen(text: str, name: str) -> str:
    muster = rf"[ \t]*<!-- BLOCK:{name}:START -->.*?<!-- BLOCK:{name}:END -->[ \t]*\n?"
    return re.sub(muster, "", text, flags=re.S)


def lade_vorlage(sprache: str) -> str:
    return (VORLAGEN_DIR / f"PreArrival_{sprache.upper()}.html").read_text(encoding="utf-8")


def render_html(vorlage: str, d: MailDaten) -> str:
    """Füllt die Vorlage. Wirft ValueError, wenn Platzhalter übrig bleiben."""
    s = d.sprache
    # Entwickler-Kommentar (enthält Beispiel-Platzhalter) entfernen
    text = re.sub(r"<!-- =+.*?=+ -->\s*", "", vorlage, flags=re.S)

    variante = zahlung_variante(d)
    if variante:
        text = _block_behalten(text, "zahlung")
        for name in ("anzahlung", "rest", "gesamt"):
            blk = f"zahlung_{name}"
            text = _block_behalten(text, blk) if name == variante else _block_entfernen(text, blk)
        text = _block_behalten(text, "zahlung_agb") if variante == "anzahlung" else _block_entfernen(text, "zahlung_agb")
    else:
        text = _block_entfernen(text, "zahlung")
    storno = storno_variante(d)
    for name in ("morgen", "heute", "datum"):
        blk = f"storno_{name}"
        text = _block_behalten(text, blk) if name == storno else _block_entfernen(text, blk)

    werte: Dict[str, str] = {
        "anrede": anrede(d.vorname, s),
        "buchungsnummer": html.escape(d.buchungsnummer),
        "anreise": datum_lang(d.anreise, s),
        "abreise": datum_lang(d.abreise, s),
        "naechte": naechte_text(max((d.abreise - d.anreise).days, 1), s),
        "gaeste": gaeste_text(d.personen, s),
        "events": d.events_html,
        "stornofrist": datum_kurz(d.anreise - timedelta(days=d.storno_tage), s),
    }
    if variante:
        werte["paymentlink"] = html.escape(d.paymentlink, quote=True)
    for key, wert in werte.items():
        text = text.replace("{{" + key + "}}", wert)

    text = re.sub(r"[ \t]*<!--.*?-->[ \t]*\n?", "", text, flags=re.S)  # restliche Kommentare entfernen
    rest = re.findall(r"\{\{\w+\}\}|BLOCK:\w+:(?:START|END)", text)
    if rest:
        raise ValueError(f"Nicht ersetzte Platzhalter/Marker: {sorted(set(rest))}")
    return text


def html_zu_text(h: str) -> str:
    """Einfache Text-Alternative aus dem HTML (für Mailclients ohne HTML)."""
    t = re.sub(r"(?s)<!--.*?-->", "", h)
    t = re.sub(r"(?is)<(style|head|script).*?</\1>", "", t)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)<a\s[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", r"\2 (\1)", t, flags=re.S)
    t = re.sub(r"(?i)</(p|h1|h2|h3|tr|li|table|div)>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", "", t)
    t = html.unescape(t).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip() + "\n"


def render_mail(d: MailDaten) -> Dict[str, str]:
    """Liefert {'betreff', 'html', 'text'} für einen Gast."""
    h = render_html(lade_vorlage(d.sprache), d)
    return {"betreff": BETREFF[d.sprache], "html": h, "text": html_zu_text(h)}
