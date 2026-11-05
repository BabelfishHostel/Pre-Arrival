# Pre-Arrival-Mails Babelfish Hostel – Stand 05.11.2026

Dieses Dokument beschreibt die Mailvorlagen und die Regeln, nach denen der Versand-Job sie füllt. Technik, Einrichtung und Betrieb stehen in der `README.md` im Projektstamm.

## Wo liegt was

| Ort | Inhalt |
|---|---|
| `src/templates/PreArrival_DE.html`, `PreArrival_EN.html` | **Maßgebliche** Vorlagen (Deutsch, Englisch), mit allen Blöcken und Platzhaltern |
| `src/render.py` | füllt die Vorlage: Blöcke, Anrede, Datums- und Betragsformate, Text-Alternative |
| `src/events.py` | Veranstaltungen aus dem Google Sheet |
| `src/run_send.py` | wählt fällige Gäste, sendet oder erzeugt Vorschauen |
| `docs/` | Referenzmaterial aus der Entwurfsphase (Kopien der Vorlagen, Beispiel-Bestätigung als Stilvorlage, Vorschaubilder). Die Kopien der Vorlagen hier sind **nicht** maßgeblich. |

Vorschau erzeugen: `python3 src/run_send.py --vorschau data/vorschau` (sendet nichts, schreibt HTML-Dateien mit Gästedaten und Paylinks).

## Datenquelle: die CSV von ASSD

Die CSV „Anreise in 2 Tagen“ kommt täglich um 06:00 Uhr per Mail. Verwendete Spalten:

| Spalte | Verwendung |
|---|---|
| `RegNummer` | interne Kennung (eindeutig), Fallback für die Buchungsnummer |
| `Buchungsnummer` | die Nummer, die der Gast sieht (z. B. `410200-20-12913`) |
| `Anreise`, `Abreise`, `AnzahlPersonen` | Datumsangaben, Nächte, Gäste |
| `Vorname` | Anrede |
| `Email` | Empfänger |
| `Sprache` | `D` → Deutsch, `E` → Englisch |
| `Kanal` | `EGB` (eigene Buchungsstrecke), `BHC` (Booking.com), `EHC` (Expedia); leer = noch nicht geklärt |
| `amou1`, `Paylink` | offener Zahlbetrag und persönlicher Zahlungslink; **beide leer = bereits bezahlt** |
| `total` | Buchungssumme (wird gespeichert, aktuell nicht in der Mail verwendet) |

Adresse, PLZ und Stadt werden nicht gespeichert. Die Stadt dient beim Import nur dazu, einen als Vorname eingetragenen Ortsnamen zu erkennen.

## Betreffzeilen

- DE: „Deine Anreise im Babelfish Hostel – alles Wichtige auf einen Blick"
- EN: „Your arrival at Babelfish Hostel – everything at a glance"

Bei einer späteren Zahlungserinnerung bewusst anders formulieren, sonst wird sie für dieselbe Mail gehalten.

## Platzhalter

`{{anrede}}` `{{buchungsnummer}}` `{{anreise}}` `{{abreise}}` `{{naechte}}` `{{gaeste}}` `{{events}}` `{{stornofrist}}` `{{betrag}}` `{{zahlungsfrist}}` `{{paymentlink}}`

`{{stornofrist}}` steht nur im Block `storno_flex_vor_frist`, `{{betrag}}`, `{{zahlungsfrist}}` und `{{paymentlink}}` nur im Zahlungsblock. Formate: DE `Fr, 9. Okt 2026`, `528,00 EUR`, `2 Nächte`, `1 Gast` / `4 Gäste`; EN `Fri, 9 Oct 2026`, `EUR 528.00`, `2 nights`, `1 guest` / `4 guests`. Wochentage und Monate sind fest hinterlegt und unabhängig von der Systemsprache.

## Blöcke und wann sie erscheinen

Jeder austauschbare Teil steht zwischen `<!-- BLOCK:name:START -->` und `<!-- BLOCK:name:END -->`. Der Job behält pro Gast genau einen Storno-Block und entfernt die übrigen samt Markern. Nach dem Rendern bleiben weder Kommentare noch Platzhalter übrig, sonst bricht der Versand für diesen Gast mit einem Fehler ab.

**Storno-Block** (abhängig von der Rate):

| Block | Wann |
|---|---|
| `storno_flex_vor_frist` | Rate Flex und heute liegt **vor** dem Ende der kostenfreien Stornofrist (Anreise minus `STORNO_TAGE`, Standard 2 Tage) |
| `storno_flex_nach_frist` | Rate Flex, Frist erreicht oder abgelaufen |
| `storno_fix` | Rate Fix **oder Rate unbekannt** (sichere Variante ohne Stornozusage) |

Da die CSV bisher **keine Rate** liefert, gilt `RATE_STANDARD=fix`, und alle Gäste bekommen `storno_fix`. Die Unterscheidung Flex/Fix wirkt erst, wenn eine Rate-Spalte vorliegt. Mit der CSV „Anreise in 2 Tagen“ ist die Stornofrist beim Versand ohnehin meist schon erreicht, Flex-Gäste würden dann `storno_flex_nach_frist` bekommen.

**Zahlungsblock `zahlung`** erscheint nur, wenn alle Punkte zutreffen:

- Kanal ist `EGB`
- Betrag (`amou1`) und `Paylink` sind gefüllt (= noch nicht bezahlt)
- Rate Flex **oder** `ZAHLUNG_REGEL=paylink` (dann unabhängig von der Rate)

Portalbuchungen (`BHC`, `EHC`) bekommen nie einen Zahlungsblock. Die `@guest.booking.com`-Adressen sind Weiterleitungen von Booking.com, eine Zahlungsaufforderung darüber verstößt gegen deren Regeln. Die Zahlungsfrist ist optional: Ohne `ZAHLUNGSFRIST_TAGE` entfällt der Satzteil „bis …“ bzw. „by …“ in der Mail.

## Anrede

- Vorname leer, mit Ziffern oder gleich der Stadt → `Hallo zusammen,` / `Hi everyone,` (die Stadt prüft der Import, danach ist der Vorname leer)
- sonst das erste Wort des Vornamens (`Ruth - Edith` → `Ruth`)
- durchgehend großgeschriebene Namen werden in Normalschreibweise gewandelt
- Namen werden für HTML maskiert

## Sprache

`Sprache` aus der CSV (`D`/`E`). Fehlt die Spalte ganz, gilt ersatzweise: fünfstellige PLZ = Deutsch, sonst Englisch. Fehlt beides, gilt `SPRACHE_STANDARD` (Standard `de`).

## Events

Quelle: öffentliches Google Sheet, Tab „Veranstaltungen“, als CSV-Ausgabe über `https://docs.google.com/spreadsheets/d/<ID>/gviz/tq?tqx=out:csv&sheet=Veranstaltungen`. Der Tab wird über den Namen angesprochen, ein Verschieben schadet nicht. Konfiguration: `EVENTS_SHEET_ID`, `EVENTS_TAB`, `EVENTSEITE_URL`. Das Sheet wird je Lauf einmal geladen (Zeitlimit 8 Sekunden).

Regeln (umgesetzt in `src/events.py`):

- nur Status **freigegeben**
- Treffer, wenn Start bis Ende den Anreisetag oder den Folgetag berührt (Ende leer = eintägig)
- Dubletten über Titel und Startdatum entfernen
- höchstens 3 Einträge, chronologisch; bei mehr Treffern haben Konzert, Ausstellung und Sport Vorrang vor Messe und „Uni & Kongress“
- höchstens ein Termin je Kategorie und Tag, höchstens zwei pro Tag
- fehlende Uhrzeit oder fehlender Ort: Angabe weglassen, kein leeres Trennzeichen
- Kategorie Sport: Enddatum ignorieren (es ist das offene Spieltermin-Fenster des Verbands, kein Zeitraum)
- Ortsfilter nur als Ausschlussliste (aktuell Schweinfurt, Bamberg, Nürnberg, Frankfurt, Aschaffenburg). Nicht positiv auf „Würzburg“ filtern: Viele Termine haben das Wort gar nicht im Ortsfeld oder keinen Ort.
- Kurztext wird nicht verwendet
- englische Titel aus „Titel EN“, wenn leer der deutsche Titel
- Uhrzeit: DE `20:00 Uhr`, EN `8 pm`
- keine Treffer, Sheet nicht erreichbar oder nicht lesbar → Fallback-Satz mit Link auf die Veranstaltungsseite. Die Mail scheitert nie an einem Sheet-Problem.

Hinweis (Stand 13.10.): Getestet gegen den damaligen Sheet-Stand (131 Zeilen, 42 freigegeben) bekamen von 68 Anreisetagen 24 gar keinen Termin, 26 genau einen und nur 6 die vollen drei. Der Engpass ist die Freigabe, nicht der Filter.

**Annahme zu den Spaltenköpfen:** Der Code erwartet `Status`, `Startdatum`, `Enddatum`, `Uhrzeit`, `Titel DE`, `Titel EN`, `Ort`, `Kategorie` (Groß-/Kleinschreibung egal, mehrere Schreibweisen werden erkannt). Sind die Köpfe im echten Sheet anders, greift der Fallback-Satz. Das ist noch nicht gegen das echte Sheet geprüft.

## Versandzeitpunkt

Fällig sind Anreisen von heute bis heute + `SEND_TAGE_VORHER` (Standard 2 Tage). Da die CSV Anreisen in zwei Tagen enthält, geht die Mail normalerweise zwei Tage vor Anreise raus. Jeder Gast bekommt sie nur einmal (`mail_log`). Solange `SEND_MODUS=vorschau` gilt, wird nichts versendet.

## Technische Festlegungen

- Stil 1:1 aus den Bestätigungsmails, keine neuen Farben außer der Aktionsfarbe `#f0bc12` für den Zahlungs-Button
- Logo über dieselbe Adresse wie in der Bestätigungsmail: `https://hostels.assd.com/img/email/410200-logo-quer-invers.png` (180 × 94 px)
- Button als Tabelle, nicht als Link – Outlook stellt gestylte Links sonst als blassen Text dar
- wichtige Stile zusätzlich inline, weil einzelne Clients den `<style>`-Block im Kopf verwerfen
- keine CSS-Variablen, kein Flexbox/Grid, kein SVG, keine externen Fonts
- die Mail enthält auch eine Text-Alternative (`text/plain`), automatisch aus dem HTML erzeugt
- kein Abmeldelink nötig: Mails zum bestehenden Vertrag, keine Werbung

## Erledigt seit dem Entwurf

- Sprachfeld: kommt jetzt aus der CSV (`Sprache`)
- Buchungsnummer: eigene Spalte `Buchungsnummer`
- Logo: Adresse korrigiert (Bestätigungsmail)
- Zahlungsdaten: `amou1` und `Paylink` aus der CSV, „bezahlt“ = beide leer

## Offen

1. **Rate (Flex/Fix):** fehlt weiterhin in der CSV. Ohne sie bekommen alle Gäste den Fix-Text.
2. **Events:** Sheet-Adresse, Tab-Name und Spaltenköpfe prüfen, `EVENTSEITE_URL` festlegen (aktuelles Thema).
3. **Zahlungsfrist:** mit ASSD / intern klären; eventuell entfällt sie, und der Satz wird ohne Datum formuliert.
4. **Kanal leer:** Was bedeutet ein leeres Kanalfeld (vermutlich Direktbuchung)? Betrifft die Zahlungslogik.
5. **Spätanreise-Frist:** „bis zum Tag vor der Anreise“ ist ein Vorschlag und noch nicht entschieden. Mit einem Versand zwei Tage vor Anreise ist die Frist knapp.
6. **SPF, DKIM, DMARC** für `babelfish-hostel.de` prüfen, bevor Mails über den eigenen Server laufen.
7. **Kurzfristbuchungen:** Wer weniger als zwei Tage vor Anreise bucht, steht in keiner CSV und bekommt keine Mail. Regel nötig.
8. **Livegang:** Beim ersten Lauf mit `SEND_MODUS=live` wären auch Gäste fällig, die heute anreisen. Vorher festlegen, wie der Start aussieht.
9. **Azure:** eigene App nur mit `Mail.Send`, per RBAC auf `info@babelfish-hostel.de` beschränkt (bewusst hinten angestellt).
