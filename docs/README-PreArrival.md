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
| `Buchungsnummer` | die Nummer, die der Gast sieht (z. B. `410200-20-12913`). **Leer bei Buchungen vor Ort oder am Telefon**, dann zeigt die Mail ersatzweise die `RegNummer` (damit die Rezeption die Buchung bei Rückfragen schnell findet, auch wenn der Gast diese Nummer noch nicht kannte). |
| `Anreise`, `Abreise`, `AnzahlPersonen` | Datumsangaben, Nächte, Gäste |
| `Vorname` | Anrede |
| `Email` | Empfänger |
| `Sprache` | `D` → Deutsch, `E` → Englisch |
| `Kanal` | `EGB` (eigene Buchungsstrecke), `BHC` (Booking.com), `EHC` (Expedia); leer = Buchung vor Ort oder am Telefon (dann ist auch die `Buchungsnummer` leer) |
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

**Zahlungsblock `zahlung`** erscheint, wenn die CSV für den Gast sowohl Betrag (`amou1`) als auch `Paylink` enthält. Ob eine Zahlung offen ist, entscheidet ASSD beim Erzeugen der CSV (bezahlt = beide leer); Kanal und Rate spielen keine Rolle. Das gilt auch für Buchungen vor Ort oder am Telefon.

Warnung im Log (die Mail wird trotzdem gesendet): Paylink ohne Betrag (der Block entfällt) sowie Paylink bei Portalbuchungen (`BHC`, `EHC`, `@guest.booking.com`). Diese Adressen sind Weiterleitungen von Booking.com, eine Zahlungsaufforderung darüber verstößt gegen deren Regeln. Die Zahlungsfrist ist optional: Ohne `ZAHLUNGSFRIST_TAGE` entfällt der Satzteil „bis …“ bzw. „by …“ in der Mail.

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
- höchstens 4 Einträge, chronologisch; bei mehr Treffern haben alle Kategorien außer „Uni & Kongress“ und „Messe“ Vorrang (Konzert, Ausstellung, Sport, Theater, Sonstiges). Das Sheet hat keine eigene Kategorie „Theater“, Theater liegt unter „Sonstiges“ und ist damit gleichgestellt.
- höchstens ein Termin je Kategorie und Tag, höchstens zwei pro Tag
- fehlende Uhrzeit oder fehlender Ort: Angabe weglassen, kein leeres Trennzeichen
- Kategorie Sport: Enddatum ignorieren (es ist das offene Spieltermin-Fenster des Verbands, kein Zeitraum)
- Ortsfilter nur als Ausschlussliste (aktuell Schweinfurt, Bamberg, Nürnberg, Frankfurt, Aschaffenburg). Nicht positiv auf „Würzburg“ filtern: Viele Termine haben das Wort gar nicht im Ortsfeld oder keinen Ort.
- Kurztext wird nicht verwendet
- englische Titel aus „Titel EN“, wenn leer der deutsche Titel
- Uhrzeit: DE `20:00 Uhr`, EN `8 pm`
- mit Terminen schließt der Bereich mit einem Link zur Veranstaltungsseite (`EVENTSEITE_URL`): „Mehr Tipps findest Du im Eventbereich unserer Website.“ bzw. „You'll find more tips in the events section of our website.“ Ohne gesetzte URL entfällt der Link.
- keine Treffer, Sheet nicht erreichbar oder nicht lesbar → Fallback-Satz mit Link auf die Veranstaltungsseite. Die Mail scheitert nie an einem Sheet-Problem.

Hinweis (Stand 13.10.): Getestet gegen den damaligen Sheet-Stand (131 Zeilen, 42 freigegeben) bekamen von 68 Anreisetagen 24 gar keinen Termin, 26 genau einen und nur 6 die vollen drei. Der Engpass ist die Freigabe, nicht der Filter.

**Spalten des echten Sheets** (am 05.11.2026 geprüft): `Titel (DE)`, `Titel (EN)`, `Start-Datum`, `Start-Uhrzeit`, `End-Datum (optional)`, `Ort`, `Status`, `Kategorie`, `Kurztext/Tipp (DE)`, `Kurztext/Tipp (EN)`, `Quelle`, `Erfasst am`. Der Code erkennt die Köpfe unabhängig von Klammern, Bindestrichen und Groß-/Kleinschreibung. Datum im Format `TT.MM.JJJJ`, Uhrzeit `HH:MM` mit oder ohne „Uhr“. Status: `Freigegeben` / `Entwurf`. Kategorien: Sonstiges, Konzert, Sport, Uni & Kongress, Ausstellung (Stand 05.11.: 130 Zeilen, davon 34 freigegeben). Bei Köpfen, die der Code nicht erkennt, greift der Fallback-Satz.

Zur Priorisierung: Bis auf „Uni & Kongress“ sind alle Kategorien gleichberechtigt, inklusive „Sonstiges“ (dort liegt u. a. Theater). Da „Sonstiges“ auch Kleinkram enthält (z. B. Spieleabend, Kickerturnier), kann es sich lohnen, im Sheet eine Kategorie „Theater“ anzulegen. Der Code behandelt sie bereits gleichrangig. Mit höchstens zwei Terminen pro Tag und einem je Kategorie und Tag ergeben Anreisetag und Folgetag zusammen höchstens vier Einträge.

## Versandzeitpunkt

Die Mail bekommen alle Gäste mit gültiger E-Mail-Adresse, auch Buchungen vor Ort oder am Telefon (entschieden am 06.11.2026). Fällig sind Anreisen von heute bis heute + `SEND_TAGE_VORHER` (Standard 2 Tage). Da die CSV Anreisen in zwei Tagen enthält, geht die Mail normalerweise zwei Tage vor Anreise raus. Jeder Gast bekommt sie nur einmal (`mail_log`). Solange `SEND_MODUS=vorschau` gilt, wird nichts versendet.

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
- Zahlungsblock: erscheint, sobald Paylink und Betrag in der CSV stehen (Kanal/Rate egal); `ZAHLUNG_REGEL` entfällt, Warnung bei Portalbuchungen mit Paylink (06.11.2026)
- Kanal leer: bedeutet Buchung vor Ort oder am Telefon (zusammen mit leerer Buchungsnummer), Mail geht trotzdem raus, Buchungsnummer-Fallback auf die `RegNummer` bleibt (06.11.2026)
- Events: Sheet angebunden (Spaltenköpfe des echten Sheets geprüft), `EVENTS_SHEET_ID` und `EVENTSEITE_URL` gesetzt, Theater gleichberechtigt, höchstens 4 Einträge, Abschlusslink „Mehr Tipps findest Du im Eventbereich unserer Website.“ (Vorschau auf dem NAS geprüft, 05.11.2026)

## Offen

1. **Rate (Flex/Fix):** fehlt weiterhin in der CSV. Ohne sie bekommen alle Gäste den Fix-Text.
2. **Zahlungsfrist:** mit ASSD / intern klären; eventuell entfällt sie, und der Satz wird ohne Datum formuliert.
3. **Spätanreise-Frist:** „bis zum Tag vor der Anreise“ ist ein Vorschlag und noch nicht entschieden. Mit einem Versand zwei Tage vor Anreise ist die Frist knapp.
4. **SPF, DKIM, DMARC** für `babelfish-hostel.de` prüfen, bevor Mails über den eigenen Server laufen.
5. **Kurzfristbuchungen:** Wer weniger als zwei Tage vor Anreise bucht, steht in keiner CSV und bekommt keine Mail. Regel nötig.
6. **Livegang:** Beim ersten Lauf mit `SEND_MODUS=live` wären auch Gäste fällig, die heute anreisen. Vorher festlegen, wie der Start aussieht.
7. **Gebuchte Betten/Zimmer anzeigen:** Im Bereich „Deine Buchung“ sollen die gebuchten Betten bzw. Zimmer erscheinen. Voraussetzung ist eine Erweiterung der CSV durch ASSD (neue Spalte(n) mit Zimmer-/Bettentyp und Anzahl). Danach: Feld in der Datenbank, Import, neuer Platzhalter in beiden Vorlagen (DE/EN). Zu klären: Format der Angabe (z. B. „2 × Bett im 6er-Zimmer“), Buchungen mit mehreren Zimmertypen, Sprache der Bezeichnungen. *(aufgenommen am 06.11.2026)*
8. **Azure:** eigene App nur mit `Mail.Send`, per RBAC auf `info@babelfish-hostel.de` beschränkt (bewusst hinten angestellt).
