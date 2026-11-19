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

Die CSV „Anreise in 3 Tagen“ kommt täglich um 06:00 Uhr per Mail. Sie enthält seit 10.11.2026 alle Anreisen von heute bis heute + 3 Tage, ein Gast steht also bis zu 4-mal darin (der Import aktualisiert dieselbe `RegNummer`, das Mail-Log erlaubt nur einen Versand pro Gast). Verwendete Spalten (23 Spalten seit 16.11.2026, die letzte ist `Paylink`; zusätzliche Spalten werden ignoriert, der Import arbeitet über die Kopfzeile):

| Spalte | Verwendung |
|---|---|
| `RegNummer` | interne Kennung (eindeutig), Fallback für die Buchungsnummer |
| `Buchungsnummer` | die Nummer, die der Gast sieht (z. B. `410200-20-12913`). **Leer bei Buchungen vor Ort oder am Telefon**, dann zeigt die Mail ersatzweise die `RegNummer` (damit die Rezeption die Buchung bei Rückfragen schnell findet, auch wenn der Gast diese Nummer noch nicht kannte). |
| `Anreise`, `Abreise`, `AnzahlPersonen` | Datumsangaben, Nächte, Gäste |
| `Vorname` | Anrede |
| `Email` | Empfänger; stehen mehrere Adressen im Feld, gilt nur die erste |
| `Sprache` | `D` → Deutsch, `E` → Englisch |
| `Kanal` | `EGB` (Website), `BHC`, `EHC`, leer = vor Ort/Telefon; steuert nur den Sonderfall Website-Flex |
| `Rate` | `FLEX` zählt zusammen mit Kanal `EGB` als Anzahlung fällig; `FLEX`/`NRF` bestimmen außerdem den Zusatz „erstattbar“/„nicht erstattbar“ hinter jedem Zimmer, z. B. Mehrbettzimmern in der Leistungsliste; alles andere ohne Wirkung (gespeichert als `flex`, `nrf`, `fix` oder leer) |
| `Vorauszahlung_1` | geforderte Vorauszahlung (kann manuell für die Paylink-Erstellung gesetzt sein); wird gespeichert, nicht im Mailtext angezeigt |
| `Vorausdatum_1` | Datum, bis zu dem die Vorauszahlung fällig ist; immer gesetzt, wenn eine Vorauszahlung nötig ist; `0000-00-00` = nicht gesetzt |
| `Schon_eingegangen` | bisher bezahlter Betrag |
| `VCC` | `yes` = Buchung ist mit virtueller Kreditkarte bereits bezahlt (alles andere, auch fehlende Spalte: nein); wird als 0/1 gespeichert (Schema v6, Migration automatisch) |
| `Zahlbetrag`, `Restbetrag` | Gesamtbetrag und offener Rest (`Zahlbetrag` − `Schon_eingegangen`); werden gespeichert, nicht angezeigt |
| `room` | Leistungsliste, Einträge getrennt mit `\|`: `Datum Anzahl x Artikelname Einzelpreis Rabatt rabattierter-Gesamtpreis`, z. B. `2026-11-18 3 x 10-Bettzimmer mixed 21.89 10.00 59.10`. Rabatt in Prozent, Gesamtpreis gilt für alle Einheiten. Artikel sind Zimmer, Frühstück und Abzüge (negative Menge, z. B. `Internet Deposit` = vom Portal schon kassiert). Die Summe aller Gesamtpreise ergibt `Zahlbetrag` (an 36 Testzeilen geprüft). Wird roh gespeichert, in der Mail als Tabelle angezeigt |
| `Paylink` | persönlicher Zahlungslink; **leer = kein Zahlungsbereich** |

Adresse, PLZ und Stadt werden nicht gespeichert. Die Stadt dient beim Import nur dazu, einen als Vorname eingetragenen Ortsnamen zu erkennen.

## Betreffzeilen

- DE: „Deine Anreise im Babelfish Hostel – alles Wichtige auf einen Blick"
- EN: „Your arrival at Babelfish Hostel – everything at a glance"

Bei einer späteren Zahlungserinnerung bewusst anders formulieren, sonst wird sie für dieselbe Mail gehalten.

## Platzhalter

`{{anrede}}` `{{buchungsnummer}}` `{{anreise}}` `{{abreise}}` `{{naechte}}` `{{gaeste}}` `{{events}}` `{{stornofrist}}` `{{paymentlink}}`

`{{stornofrist}}` steht nur im Block `storno_datum`, `{{paymentlink}}` nur im Zahlungsbereich. Der Betrag steht nicht mehr in der Mail (er erscheint im Zahlungsfenster). Formate: DE `Fr, 9. Okt 2026`, `528,00 EUR`, `2 Nächte`, `1 Gast` / `4 Gäste`; EN `Fri, 9 Oct 2026`, `EUR 528.00`, `2 nights`, `1 guest` / `4 guests`. Wochentage und Monate sind fest hinterlegt und unabhängig von der Systemsprache.

## Blöcke und wann sie erscheinen

Jeder austauschbare Teil steht zwischen `<!-- BLOCK:name:START -->` und `<!-- BLOCK:name:END -->`. Der Job entfernt nicht benötigte Blöcke samt Markern. Nach dem Rendern bleiben weder Kommentare noch Platzhalter übrig, sonst bricht der Versand für diesen Gast mit einem Fehler ab.

**Bereich „Stimmt alles?“** ist bis auf den letzten Satz für alle Gäste gleich (der Nichtanreise-Satz unterscheidet Direkt- und OTA-Buchung, siehe unten): „Bitte prüfe Datum und Personenzahl. [Frist-Satz] Falls Du nicht kommen kannst, sag uns bitte kurz Bescheid – dann wissen wir Bescheid und halten Dir das Bett nicht unnötig frei.“ Für alle Buchungen der Liste sind die letzten `STORNO_TAGE` (Standard 2) Tage vor Anreise nicht mehr kostenfrei, der **letzte kostenfreie Tag ist also Anreise minus 3 Tage** (Anreise 20.11.: 19. und 18.11. kostenpflichtig, letzter kostenfreier Tag 17.11.; geändert am 18.11.2026, vorher war die Frist einen Tag zu spät). Der Frist-Satz richtet sich nach dem Versandtag, damit er nie falsch ist:

| Block | Wann | Text |
|---|---|---|
| `storno_morgen` | letzter kostenfreier Tag ist morgen (Versand 4 Tage vor Anreise) | „Der Zeitraum für die kostenfreie Stornierung läuft morgen ab.“ |
| `storno_heute` | letzter kostenfreier Tag ist heute (Normalfall bei Versand 3 Tage vor Anreise) | „… läuft heute ab.“ |
| `storno_datum` | letzter kostenfreier Tag liegt später | „… läuft am *Di, 17. Nov* ab.“ |
| (keiner) | Frist schon abgelaufen (Anreise heute, morgen oder in 2 Tagen) **oder Rate `NRF`** (nicht erstattbar: nie kostenfrei stornierbar) | der Satz entfällt komplett |

**Zahlungsbereich** (Überschrift „Zahlung“, Einleitungssatz, Button „Jetzt bezahlen“) erscheint, wenn die CSV einen `Paylink` enthält, oder (ohne Paylink) wenn die Buchung komplett bezahlt ist. Der Einleitungssatz hängt vom Zahlstand ab (Reihenfolge der Prüfung):

| Block | Wann | Text (DE) |
|---|---|---|
| `zahlung_rest` | `Schon_eingegangen` > 0 (auch ohne `Vorausdatum_1`, ohne Vergleich mit `Vorauszahlung_1`) | „Du hast bereits eine Anzahlung geleistet, Deine Buchung ist garantiert. Mit folgendem Bezahllink kannst Du den Restbetrag bezahlen.“ |
| `zahlung_anzahlung` | sonst, `Vorausdatum_1` gesetzt **oder** (Sonderfall, 12.11.2026) Kanal `EGB` und Rate `FLEX` (Buchung über die Website, Anzahlung fällig, auch ohne Vorausdatum) | „Für Deine Buchung ist eine Anzahlung fällig und wurde noch nicht vorgenommen. Bitte benutze dazu folgenden Bezahllink, ansonsten können wir die Buchung nicht garantieren.“ (mit AGB-Satz unter dem Button, Block `zahlung_agb`) |
| `zahlung_bezahlt` | **`VCC` = yes (hat Vorrang, auch wenn ein Paylink und ein offener Restbetrag in der CSV stehen; Button und Paylink entfallen)**, oder: kein Paylink, aber komplett bezahlt: `Zahlbetrag` > 0, `Schon_eingegangen` > 0 und `Restbetrag` leer/0; egal welcher Kanal | „Deine Buchung ist bereits komplett bezahlt.“ (ohne Button, ohne Hinweis zu Zahlungsarten, ohne AGB-Satz) |
| `zahlung_gesamt` | sonst (kein Vorausdatum) | „Deine Buchung ist garantiert. Mit folgendem Bezahllink kannst Du die Buchung im Voraus bezahlen.“ |

Kanal und Rate zählen nur für den Sonderfall Website-Flex (`EGB` + `FLEX` → Anzahlungstext, solange noch nichts eingegangen ist); sonst keine Rolle (auch `@guest.booking.com` bekommt den Zahlungsbereich, sobald ein Paylink da ist). Betrag und Frist stehen nicht in der Mail. Ist `Restbetrag` offen, aber kein Paylink da, entfällt der Zahlungsbereich und das Log enthält eine Warnung; bei Paylink mit Restbetrag 0 wird der Bereich trotzdem angezeigt (ebenfalls Warnung). `RATE_STANDARD` und `ZAHLUNGSFRIST_TAGE` gibt es nicht mehr.

**Nichtanreise-Satz** (letzter Satz im Bereich „Stimmt alles?“): Direktbuchung (Kanal `EGB` oder leer) bekommt „Falls Du nicht kommen kannst, sag uns bitte kurz Bescheid – dann wissen wir Bescheid und halten Dir das Bett nicht unnötig frei.“ OTA-Buchung (jeder andere Kanal, z. B. `BHC`, `EHC`) bekommt „Falls Du stornieren musst, mach das über die Seite, auf der Du gebucht hast – wir bekommen automatisch Bescheid und Du musst uns nicht extra schreiben.“ (EN: „If you need to cancel, do it through the website you booked on – we’ll be notified automatically, so there’s no need to contact us.“) (Blöcke `nichtanreise_direkt` und `nichtanreise_ota`; EN ist meine Übersetzung, noch nicht geprüft.)

**Leistungen** (Spalte `room`): Im Kasten „Deine Buchung“ erscheint unter Anreise/Dauer eine Tabelle „Leistungen“ (EN „Services“) mit einer Zeile je Nacht und Artikel: Datum, Anzahl × Bezeichnung, Betrag. Bei Rabatt oder Anzahl größer 1 steht darunter klein der Einzelpreis und der Rabatt (z. B. „3 × 21,89 EUR, Rabatt 10 %“). Die Bezeichnungen kommen aus ASSD in der Gastsprache und werden so angezeigt: `10-Bettzimmer mixed` → „Bett im 10-Bettzimmer gender-mixed“ (ebenso 6 und 4), `4-Bettzimmer female` → „Bett im 4-Bettzimmer für Frauen“, `Appartement` → „Apartment für 4“; `Einzelzimmer`, `Doppelzimmer`, `Doppelzimmer Studio`, `4-Bettzimmer privat`, `6-Bettzimmer privat` (und Frühstück, Schlafcouch) bleiben unverändert. Englisch: „Bed in 10-bed gender-mixed room“, „Bed in 4-bed female only room“ (auch aus „4 bed room female only“), „Apartment for 4“, sonst wie von ASSD geliefert (EN-Namen sind mein Vorschlag, nicht geprüft). Hinter Mehrbettzimmern steht je nach Rate der Zusatz „, nicht erstattbar“ (Rate `NRF`) bzw. „, erstattbar“ (Rate `FLEX`), englisch „non-refundable“/„refundable“; bei anderen Raten und bei Nicht-Zimmer-Artikeln kein Zusatz (Beispiele: „1 × Doppelzimmer, erstattbar“, „2 × Bett im 10-Bettzimmer gender-mixed, nicht erstattbar“). Unbekannte Namen: im Deutschen `mixed` → „gemischt“, `female` → „weiblich“, sonst unverändert. `Internet Deposit` heißt „Bereits über das Portal bezahlt“ / „Already paid via the platform“ (Betrag mit Minus). Zuletzt steht „Gesamtbetrag“ / „Total“ (`Zahlbetrag`, sonst die Summe der Zeilen). Ist `room` leer oder auch nur ein Eintrag nicht lesbar, entfällt der ganze Bereich (kein unvollständiger Betrag), das Log enthält einen Hinweis, die Mail geht trotzdem raus. Code: `src/leistungen.py`, DB-Feld `leistungen` (Schema v5).

## Mehrere Aufenthalte in einer Buchung

In ASSD können zu einer Buchung mehrere Pfade angelegt werden (gedacht für Zimmerwechsel im Aufenthalt, gleiche Buchungsnummer). Wird das fälschlich für eine spätere weitere Buchung des Gastes benutzt (Beispiel Buchung 13104: Nächte 20.11. und 21.11., dazu 12.12.), wäre die Mail falsch. Erkennung in `src/leistungen.py` (`aufenthalte`): Aus der Spalte `room` zählen nur Zimmer-Nächte (kein Frühstück, kein Abzug). Fehlt zwischen zwei Zimmer-Nächten mindestens eine Nacht, sind es mehrere Aufenthalte. Ein Zimmerwechsel ohne Lücke bleibt ein Aufenthalt und löst nichts aus.

Folge: Der Gast bekommt keine Pre-Arrival-Mail. Stattdessen geht eine Warn-Mail (Buchungsnummer, RegNummer, Zeiträume, keine Gästeadresse) an `SEND_ABSENDER`, im Testmodus an die Testadresse (Betreff mit „[TEST]“), im Trockenlauf wird eine Datei `WARNUNG_<RegNummer>.txt` in den Vorschauordner geschrieben. Das Mail-Log erhält `uebersprungen` mit „Mehrere Aufenthalte in einer Buchung, Warn-Mail gesendet“, die Warnung kommt also nur einmal. Schlägt das Senden der Warn-Mail fehl, bleibt der Gast offen (`fehler`, wird erneut versucht, Cronjob meldet Fehler). Wird die Buchung in ASSD korrigiert (Lücke weg), hebt der nächste Live-Lauf die Sperre automatisch auf und der Gast bekommt seine Mail. Ist alles richtig so, bitte dem Gast manuell schreiben.

## Stornierte und verschobene Buchungen

Der Import löscht keine Gäste, die in einer neuen CSV fehlen; sie bleiben mit dem Stand ihres letzten Imports in der Datenbank (bis 30 Tage nach Abreise). Die CSV enthält aber täglich alle Anreisen von heute bis heute + 3 Tage. Deshalb versendet der Versand-Job nur an Gäste, die im **letzten CSV-Import** stehen. Wer dort fehlt, bekommt keine Mail; das Log nennt die Buchung (RegNummer, Anreise, „nicht mehr in der aktuellen CSV, storniert oder verschoben?“). Damit nicht eine alte Gästeliste als aktuell gilt, prüft der Job vorher, ob der letzte Import jung genug ist: `MAX_IMPORT_ALTER_TAGE` (Standard 0 = nur von heute). Ist er älter (z. B. ASSD-Mail ausgeblieben), sendet `--live` nichts und meldet Fehler (Exit 1, Healthcheck-Fehlerping); Trockenlauf und Test warnen nur. Wird dieselbe CSV nochmals eingelesen, wird der Zeitstempel des Imports aufgefrischt, und kehrt ein älterer Stand zurück, wird er neu eingelesen. Hat ein Gast die Mail schon bekommen und storniert danach, lässt sich das nicht zurückholen. *(Lücke am 17.11.2026 beim Prüfen der Vorschau gefunden und geschlossen.)*

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

Die Mail bekommen alle Gäste mit gültiger E-Mail-Adresse, auch Buchungen vor Ort oder am Telefon (entschieden am 06.11.2026). Fällig sind Anreisen von heute bis heute + `SEND_TAGE_VORHER` (Standard 3 Tage). Die Mail geht normalerweise drei Tage vor Anreise raus (CSV „Anreise in 3 Tagen“). Der Frist-Satz in „Stimmt alles?“ richtet sich nach dem tatsächlichen Versandtag. Jeder Gast bekommt sie nur einmal (`mail_log`). Solange `SEND_MODUS=vorschau` gilt, wird nichts versendet.

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
- Zahlungsdaten: seit 12.11.2026 `Vorauszahlung_1`, `Vorausdatum_1`, `Schon_eingegangen`, `Restbetrag`, `Zahlbetrag`, `Paylink` (neues 22-Spalten-Format, Zahlungslogik nur noch nach Paylink/Schon_eingegangen/Vorausdatum, nur erste E-Mail-Adresse; DB-Schema v4, Migration automatisch)
- Zahlungs- und Storno-Texte (09.11.2026): Zahlungsbereich bei Paylink, Varianten Anzahlung/Rest/Gesamt nach Kanal EGB + Rate FLEX + Zahlstand; „Stimmt alles?“ mit Frist-Satz je nach Versandtag; Spalten `Rate`, `Anzahlung`, `Anzahlung_bis`, `amou3` in der CSV (Import geprüft); Buchungsnummer in der CSV korrigiert; Zahlungsfrist entfällt; Versand 3 Tage vor Anreise
- Umstellung auf „Anreise in 3 Tagen“ (11/2026): ASSD-CSV, Mailordner, Abfrage und Dateinamen auf dem NAS angepasst; im Projekt war dafür keine Codeänderung nötig (Betreff-Filter „Anreise“ trifft weiterhin, Standard `SEND_TAGE_VORHER=3`)
- Hinweise an ASSD zur CSV notiert und erledigt: letzte leere Spalte `Paylink` kann fehlen (25 statt 26 Felder, harmlos), `BHC` zusammen mit `FLEX` sollte nicht vorkommen
- CSV-Abfrage bei ASSD: alle Anreisen von heute bis heute + 3 Tage (deckt Kurzfristbuchungen ab, 10.11.2026); Beispiel-CSV geprüft, 21 Paylinks, keine Warnungen
- Import abgesichert: Hat eine Zeile mehr gefüllte Felder als die Kopfzeile Spalten (z. B. fehlt `Paylink` in der Kopfzeile), bricht der Import ab, die Mail bleibt ungelesen und es wird nichts importiert. Zeilen ganz ohne E-Mail-Adresse (z. B. Buchung vor Ort) zählen nur als Hinweis, nicht als Fehler (der frühere „Importfehler Zeile #5“)
- Neues CSV-Format und Zahlungstexte inkl. Sonderfall Website-Flex (`EGB` + `FLEX` → Anzahlungstext) in den Vorschauen geprüft und in Ordnung (12.11.2026)
- Komplett bezahlte Buchungen (kein Paylink, `Zahlbetrag` > 0, `Schon_eingegangen` > 0, `Restbetrag` 0) bekommen den Zahlungsbereich mit „Deine Buchung ist bereits komplett bezahlt.“, ohne Button, für alle Kanäle; Nichtanreise-Satz unterscheidet Direkt- (Kanal `EGB`/leer) und OTA-Buchung (13.11.2026)
- Leistungsliste aus der CSV-Spalte `room` (Zimmer, Frühstück, Rabatt, Portal-Abzug) als Tabelle im Kasten „Deine Buchung“ inkl. Gesamtbetrag (16.11.2026). Damit erscheinen erstmals Beträge in der Mail (bewusst so entschieden)
- VCC (virtuelle Kreditkarte): neue CSV-Spalte `VCC` (yes = bezahlt) -> Zahlungsbereich „bereits komplett bezahlt“, ohne Button (19.11.2026)
- Mehrere Aufenthalte in einer Buchung: keine Gästemail, Warn-Mail an SEND_ABSENDER (19.11.2026)
- Stornierte/verschobene Buchungen: Versand nur an Gäste aus dem letzten CSV-Import, Aktualitätsprüfung `MAX_IMPORT_ALTER_TAGE` (17.11.2026)
- Texte überarbeitet (19.11.2026): Einleitung ohne „ein paar Tipps“, „Stimmt alles? Bitte prüfe Datum und Personenzahl.“, neuer Nichtanreise-Satz für OTA-Buchungen (Stornierung über die Seite, auf der gebucht wurde); Zimmernamen der Leistungsliste und Rate-Zusatz (erstattbar/nicht erstattbar) bei allen Zimmern; in den Vorlagen kommen keine QR-Codes vor
- Cronjob auf dem NAS läuft, Vorschauen werden erstellt
- Fehlende Anreisetage in der CSV: Ursache war die SQL-Abfrage bei ASSD, behoben
- Kanal leer: bedeutet Buchung vor Ort oder am Telefon (zusammen mit leerer Buchungsnummer), Mail geht trotzdem raus, Buchungsnummer-Fallback auf die `RegNummer` bleibt (06.11.2026)
- Events: Sheet angebunden (Spaltenköpfe des echten Sheets geprüft), `EVENTS_SHEET_ID` und `EVENTSEITE_URL` gesetzt, Theater gleichberechtigt, höchstens 4 Einträge, Abschlusslink „Mehr Tipps findest Du im Eventbereich unserer Website.“ (Vorschau auf dem NAS geprüft, 05.11.2026)

## Offen

1. **SPF, DKIM, DMARC** für `babelfish-hostel.de` prüfen, bevor Mails über den eigenen Server laufen.
2. **Livegang:** Beim ersten Lauf mit `SEND_MODUS=live` wären alle Gäste fällig, die im letzten CSV-Import stehen und in den nächsten 3 Tagen (einschließlich heute) anreisen. Vorher festlegen, wie der Start aussieht.
3. **Azure:** eigene App nur mit `Mail.Send`, per RBAC auf `info@babelfish-hostel.de` beschränkt (bewusst hinten angestellt).
