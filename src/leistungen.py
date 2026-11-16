"""Leistungsliste aus der CSV-Spalte `room` (Zimmer, Frühstück, Rabatte, Portal-Anzahlung).

Format einer Zelle (ASSD, ab 11/2026), Einträge getrennt mit `|`:

    Datum Anzahl x Artikelname Einzelpreis Rabatt rabattierter-Gesamtpreis

Beispiel:  2026-11-18 3 x 10-Bettzimmer mixed 21.89 10.00 59.10

* Rabatt ist ein Prozentwert, der Gesamtpreis gilt für alle Einheiten (Anzahl x Einzelpreis, abzüglich Rabatt).
* Eine negative Anzahl mit negativem Gesamtpreis ist ein Abzug (z. B. "Internet Deposit" = vom Portal schon kassiert).
* Die Summe aller Gesamtpreise entspricht dem `Zahlbetrag`.

Die Spalte wird roh in der Datenbank gespeichert und erst beim Erzeugen der Mail zerlegt.
"""
import html
import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

POSITION = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (-?\d+) x (.+?) (-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)$"
)

# Artikelnamen aus ASSD, die für Gäste verständlicher benannt werden
ARTIKEL_NAMEN = {
    "internet deposit": {"de": "Bereits über das Portal bezahlt", "en": "Already paid via the platform"},
}
DE_WOERTER = (("mixed", "gemischt"), ("female", "weiblich"), ("male", "männlich"))


@dataclass
class Position:
    datum: date
    anzahl: int
    name: str
    einzelpreis: float
    rabatt: float       # Prozent
    gesamt: float


def parse_leistungen(text: Optional[str]) -> Optional[List[Position]]:
    """Zerlegt die Spalte `room`. None, wenn leer oder auch nur ein Eintrag nicht lesbar ist
    (lieber keine Liste als eine unvollständige)."""
    if not text or not text.strip():
        return None
    positionen: List[Position] = []
    for teil in text.split("|"):
        teil = teil.strip()
        if not teil:
            continue
        m = POSITION.match(teil)
        if not m:
            return None
        d, n, name, ep, rb, gp = m.groups()
        try:
            positionen.append(Position(date.fromisoformat(d), int(n), name.strip(), float(ep), float(rb), float(gp)))
        except ValueError:
            return None
    if not positionen:
        return None
    return sorted(positionen, key=lambda p: p.datum)    # stabil: Reihenfolge innerhalb eines Tags bleibt


def bezeichnung(name: str, sprache: str) -> str:
    """Gästetaugliche Bezeichnung: Portal-Anzahlung umbenannt, im Deutschen 'mixed' -> 'gemischt' usw."""
    bekannt = ARTIKEL_NAMEN.get(name.casefold())
    if bekannt:
        return bekannt.get(sprache, bekannt["en"])
    if sprache == "de":
        for alt, neu in DE_WOERTER:
            name = re.sub(rf"\b{alt}\b", neu, name)
    return name


def _preis(betrag: float, sprache: str) -> str:
    """'24,90 EUR' bzw. 'EUR 24.90'; negativ mit Minuszeichen davor."""
    vorz = "−" if betrag < 0 else ""
    zahl = f"{abs(betrag):,.2f}"
    if sprache == "de":
        return f"{vorz}{zahl.replace(',', 'X').replace('.', ',').replace('X', '.')} EUR"
    return f"{vorz}EUR {zahl}"


def _prozent(rabatt: float, sprache: str) -> str:
    s = f"{rabatt:g}"
    return (s.replace(".", ",") if sprache == "de" else s) + " %"


def _datum(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def gesamtsumme(positionen: List[Position], zahlbetrag: Optional[float]) -> Optional[float]:
    """Zahlbetrag aus der CSV, sonst die Summe der Positionen (beide stimmen in den Testdaten überein)."""
    if zahlbetrag and zahlbetrag > 0:
        return zahlbetrag
    summe = round(sum(p.gesamt for p in positionen), 2)
    return summe if summe > 0 else None


def leistungen_html(positionen: List[Position], zahlbetrag: Optional[float], sprache: str) -> str:
    """Tabelle (Datum | Leistung | Betrag) mit Inline-Stilen für Mailclients."""
    zelle = "padding:6px 0; border-top:1px solid #e0ddd3; vertical-align:top;"
    zeilen = []
    for p in positionen:
        name = html.escape(bezeichnung(p.name, sprache))
        if p.gesamt < 0 or p.anzahl < 0:
            titel, zusatz = name, ""
        else:
            titel = f"{p.anzahl} &times; {name}"
            teile = []
            if p.anzahl > 1:
                teile.append(f"{p.anzahl} &times; {_preis(p.einzelpreis, sprache)}")
            elif p.rabatt:
                teile.append(_preis(p.einzelpreis, sprache))
            if p.rabatt:
                teile.append(("Rabatt " if sprache == "de" else "discount ") + _prozent(p.rabatt, sprache))
            zusatz = (f'<br><span style="font-size:12px; color:#6b7a3a;">{", ".join(teile)}</span>' if teile else "")
        zeilen.append(
            f'<tr><td style="{zelle} padding-right:10px; white-space:nowrap;">{_datum(p.datum)}</td>'
            f'<td style="{zelle} padding-right:10px;">{titel}{zusatz}</td>'
            f'<td align="right" style="{zelle} white-space:nowrap;">{_preis(p.gesamt, sprache)}</td></tr>'
        )
    summe = gesamtsumme(positionen, zahlbetrag)
    if summe is not None:
        label = "Gesamtbetrag" if sprache == "de" else "Total"
        zeilen.append(
            f'<tr><td colspan="2" style="{zelle} border-top:2px solid #6b7a3a;"><strong>{label}</strong></td>'
            f'<td align="right" style="{zelle} border-top:2px solid #6b7a3a; white-space:nowrap;">'
            f"<strong>{_preis(summe, sprache)}</strong></td></tr>"
        )
    return '<table width="100%" border="0" cellpadding="0" cellspacing="0" style="font-size:14px;">' + "".join(zeilen) + "</table>"
