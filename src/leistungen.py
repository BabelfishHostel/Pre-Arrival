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
from datetime import date, timedelta
from typing import List, Optional, Tuple

POSITION = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (-?\d+) x (.+?) (-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)$"
)

# Artikelnamen aus ASSD, die für Gäste verständlicher benannt werden
ARTIKEL_NAMEN = {
    "internet deposit": {"de": "Bereits über das Portal bezahlt", "en": "Already paid via the platform"},
    "appartement": {"de": "Apartment für 4", "en": "Apartment for 4"},
    "apartment": {"de": "Apartment für 4", "en": "Apartment for 4"},
    "apartement": {"de": "Apartment für 4", "en": "Apartment for 4"},    # Tippfehler in ASSD (Spalte Pfade)
    "einzelzimmer": {"de": "Einzelzimmer", "en": "Single room"},
    "doppelzimmer": {"de": "Doppelzimmer", "en": "Double room"},
    "doppelzimmer studio": {"de": "Doppelzimmer Studio", "en": "Double room Studio"},
}
DE_WOERTER = (("mixed", "gemischt"), ("female", "weiblich"), ("male", "männlich"))

# Mehrbettzimmer: ASSD liefert "10-Bettzimmer mixed" (DE) bzw. "10 bed mixed room" (EN); gebucht werden Betten.
MEHRBETT_DE = re.compile(r"^(\d+)-Bettzimmer[ _](mixed|female|male)$", re.I)
MEHRBETT_EN = re.compile(r"^(\d+) bed (?:room )?(mixed|female|male)(?: only)?(?: room)?$", re.I)
MEHRBETT_NAME = {
    "de": {"mixed": "Bett im {n}-Bettzimmer gender-mixed", "female": "Bett im {n}-Bettzimmer für Frauen",
           "male": "Bett im {n}-Bettzimmer für Männer"},
    "en": {"mixed": "Bed in {n}-bed gender-mixed room", "female": "Bed in {n}-bed female only room",
           "male": "Bed in {n}-bed male only room"},
}
# Private Mehrbettzimmer (Pfade: "4-Bettzimmer_private")
PRIVAT_DE = re.compile(r"^(\d+)-Bettzimmer[ _]private$", re.I)
PRIVAT_NAME = {"de": "{n}-Bettzimmer privat", "en": "{n}-bed private room"}
# Rate -> Zusatz hinter jedem Zimmer (NRF = nicht erstattbar, FLEX = erstattbar, sonst nichts)
RATE_ZUSATZ = {
    "nrf": {"de": "nicht erstattbar", "en": "non-refundable"},
    "flex": {"de": "erstattbar", "en": "refundable"},
}


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


def _mehrbett(name: str):
    m = MEHRBETT_DE.match(name.strip()) or MEHRBETT_EN.match(name.strip())
    return m.groups() if m else None


def ist_mehrbettzimmer(name: str) -> bool:
    return _mehrbett(name) is not None


def bezeichnung(name: str, sprache: str) -> str:
    """Gästetaugliche Bezeichnung: Mehrbettzimmer als 'Bett im 10-Bettzimmer gender-mixed', Portal-Anzahlung und
    Apartment umbenannt, alles andere (Einzelzimmer, Doppelzimmer, 4-Bettzimmer privat ...) unverändert.
    Unbekannte Namen: im Deutschen 'mixed' -> 'gemischt' usw., sonst wie geliefert."""
    bekannt = ARTIKEL_NAMEN.get(name.strip().casefold())
    if bekannt:
        return bekannt.get(sprache, bekannt["en"])
    name = name.replace("_", " ")
    bekannt = ARTIKEL_NAMEN.get(name.strip().casefold())
    if bekannt:
        return bekannt.get(sprache, bekannt["en"])
    pm = PRIVAT_DE.match(name.strip())
    if pm:
        return PRIVAT_NAME.get(sprache, PRIVAT_NAME["en"]).format(n=pm.group(1))
    mb = _mehrbett(name)
    if mb:
        n, art = mb
        return MEHRBETT_NAME.get(sprache, MEHRBETT_NAME["en"])[art.lower()].format(n=n)
    if sprache == "de":
        for alt, neu in DE_WOERTER:
            name = re.sub(rf"\b{alt}\b", neu, name)
    return name


ZIMMER = re.compile(r"zimmer|room|apartment|appartement|studio|bettzimmer|\bbed\b", re.I)


def ist_zimmer(name: str) -> bool:
    """Zimmerartikel (alle Zimmertypen), nicht Frühstück, Schlafcouch, Portal-Abzug o. ä."""
    return bool(ZIMMER.search(name)) or name.strip().casefold() in ("appartement", "apartment")


def rate_zusatz(name: str, rate: Optional[str], sprache: str) -> str:
    """', nicht erstattbar' / ', erstattbar' hinter jedem Zimmer, je nach Rate (NRF/FLEX), sonst leer."""
    z = RATE_ZUSATZ.get(rate or "")
    return f", {z.get(sprache, z['en'])}" if z and ist_zimmer(name) else ""


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


def gesamtrabatt(positionen: List[Position]) -> float:
    """Summe aller Rabatte in EUR: Anzahl x Einzelpreis minus rabattierter Gesamtpreis (nur Positionen mit Rabatt)."""
    return round(sum(p.anzahl * p.einzelpreis - p.gesamt for p in positionen
                     if p.rabatt and p.anzahl > 0 and p.gesamt >= 0), 2)


def leistungen_html(positionen: List[Position], zahlbetrag: Optional[float], sprache: str,
                    rate: Optional[str] = None) -> str:
    """Tabelle (Datum | Leistung | Betrag) mit Inline-Stilen für Mailclients."""
    zelle = "padding:6px 0; border-top:1px solid #e0ddd3; vertical-align:top;"
    zeilen = []
    for p in positionen:
        name = html.escape(bezeichnung(p.name, sprache))
        if p.gesamt < 0 or p.anzahl < 0:
            titel, zusatz = name, ""
        else:
            titel = f"{p.anzahl} &times; {name}{html.escape(rate_zusatz(p.name, rate, sprache))}"
            teile = []
            if p.anzahl > 1:
                ohne = _preis(round(p.anzahl * p.einzelpreis, 2), sprache)
                teile.append(f"{p.anzahl} &times; {_preis(p.einzelpreis, sprache)}" + (f" = {ohne}" if p.rabatt else ""))
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
        rabatt = gesamtrabatt(positionen)
        if rabatt > 0:
            text = "darin enthaltener Rabatt" if sprache == "de" else "discount included"
            zeilen.append(
                f'<tr><td colspan="2" style="padding:2px 0 6px 0; font-size:12px; color:#6b7a3a;">{text}</td>'
                f'<td align="right" style="padding:2px 0 6px 0; font-size:12px; color:#6b7a3a; white-space:nowrap;">'
                f"{_preis(rabatt, sprache)}</td></tr>"
            )
    return '<table width="100%" border="0" cellpadding="0" cellspacing="0" style="font-size:14px;">' + "".join(zeilen) + "</table>"


def aufenthalte(positionen: List[Position]) -> List[Tuple[date, date]]:
    """Zusammenhängende Zimmer-Nächte als (erste Nacht, letzte Nacht).

    Ein Zimmerwechsel innerhalb des Aufenthalts (mehrere Pfade in ASSD) bleibt lückenlos und ist ein Aufenthalt.
    Mehr als einer bedeutet: Es fehlen Nächte dazwischen, die Buchung enthält vermutlich einen zweiten,
    späteren Aufenthalt. Frühstück, Abzüge usw. zählen nicht.
    """
    tage = sorted({p.datum for p in positionen if p.anzahl > 0 and p.gesamt >= 0 and ist_zimmer(p.name)})
    ergebnis: List[Tuple[date, date]] = []
    for t in tage:
        if ergebnis and t - ergebnis[-1][1] <= timedelta(days=1):
            ergebnis[-1] = (ergebnis[-1][0], t)
        else:
            ergebnis.append((t, t))
    return ergebnis


# ---------------------------------------------------------------------------------------------------------
# Spalte `Pfade` (ein Pfad = ein Zimmerblock der Buchung):
#   "Anreise Abreise Zimmerkategorie1 Zimmerkategorie2 ...", mehrere Pfade getrennt mit "|".
# Mehr als ein Pfad: Die Mail zeigt die Pfade statt Anreise/Abreise/Dauer/Gäste.
# ---------------------------------------------------------------------------------------------------------
@dataclass
class Pfad:
    anreise: date
    abreise: date
    zimmer: List[str]       # Kategorien wie geliefert (z. B. "4-Bettzimmer_private"), Wiederholungen = mehrere Betten/Zimmer


def parse_pfade(text: Optional[str]) -> Optional[List[Pfad]]:
    """Zerlegt die Spalte `Pfade`. None, wenn leer oder ein Pfad nicht lesbar ist."""
    if not text or not text.strip():
        return None
    pfade: List[Pfad] = []
    for teil in text.split("|"):
        teil = teil.strip()
        if not teil:
            continue
        tokens = teil.split()
        if len(tokens) < 2:
            return None
        try:
            a, b = date.fromisoformat(tokens[0]), date.fromisoformat(tokens[1])
        except ValueError:
            return None
        pfade.append(Pfad(a, b, tokens[2:]))
    return pfade or None


def mehrere_pfade(text: Optional[str]) -> bool:
    p = parse_pfade(text)
    return bool(p) and len(p) > 1


def pfade_html(pfade: List[Pfad], sprache: str) -> str:
    """Eine Zeile je Pfad: 'Do, 26.11. – Mo, 30.11.2026: 1 × ..., 2 × ...'."""
    wt = {"de": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"], "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}[sprache]

    def tag(d: date, jahr: bool) -> str:
        return f"{wt[d.weekday()]}, {d.day:02d}.{d.month:02d}." + (str(d.year) if jahr else "")

    zeilen = []
    for p in sorted(pfade, key=lambda x: (x.anreise, x.abreise)):
        gezaehlt: dict = {}
        for z in p.zimmer:
            m = re.match(r"^(\d+)x(.+)$", z)      # Dorms: "6x6-Bettzimmer_mixed" = 6 Betten im 6-Bettzimmer
            anz, art = (int(m.group(1)), m.group(2)) if m else (1, z)
            gezaehlt[art] = gezaehlt.get(art, 0) + anz
        zimmer = ", ".join(f"{n} &times; {html.escape(bezeichnung(z, sprache))}" for z, n in gezaehlt.items())
        titel = f"{tag(p.anreise, False)} &ndash; {tag(p.abreise, True)}"
        zeilen.append(f'<p style="margin:0 0 6px 0;"><strong>{titel}</strong>' + (f"<br>{zimmer}" if zimmer else "") + "</p>")
    kopf = "Deine Aufenthalte:" if sprache == "de" else "Your stays:"
    return f'<p style="margin:0 0 4px 0;"><strong>{kopf}</strong></p>' + "".join(zeilen)
