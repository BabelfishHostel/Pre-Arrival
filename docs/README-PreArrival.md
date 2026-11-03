# Pre-Arrival-Mails Babelfish Hostel – Stand 13.10.2026

## Dateien

| Datei | Zweck |
|---|---|
| `PreArrival_DE.html` | Vorlage Deutsch, mit allen Blöcken und Platzhaltern |
| `PreArrival_EN.html` | Vorlage Englisch, identischer Aufbau |
| `Vorschau_PreArrival_DE.html` / `.jpg` | Beispielmail mit Testdaten (Flex, T-3, mit Zahlung) |
| `Vorschau_PreArrival_EN.html` / `.jpg` | dasselbe auf Englisch |
| `build_beispiel.py` | Referenz-Implementierung der Block-Logik, erzeugt die Vorschau |

Das Logo fehlt in den Vorschaubildern nur deshalb, weil die Testumgebung die
Website nicht erreicht. Beim Öffnen im Browser oder im Mailclient ist es da.

## Betreffzeilen

- DE: „Deine Anreise im Babelfish Hostel – alles Wichtige auf einen Blick"
- EN: „Your arrival at Babelfish Hostel – everything at a glance"

Bei der späteren Zahlungserinnerung bewusst anders formulieren, sonst wird sie
für dieselbe Mail gehalten.

## Platzhalter

`{{anrede}}` `{{buchungsnummer}}` `{{anreise}}` `{{abreise}}` `{{naechte}}`
`{{gaeste}}` `{{events}}` `{{stornofrist}}` `{{betrag}}` `{{zahlungsfrist}}` `{{paymentlink}}`

`{{stornofrist}}` nur im Block `storno_flex_vor_frist`, die letzten drei nur im Zahlungsblock. Datum und Betrag werden vom Skript
sprachrichtig formatiert (DE: `9. Okt 2026`, `528,00 EUR` – EN: `9 Oct 2026`,
`EUR 528.00`).

## Blöcke

Jeder austauschbare Teil steht zwischen `<!-- BLOCK:name:START -->` und
`<!-- BLOCK:name:END -->`. Das Skript behält pro Gast genau einen Storno-Block
und entfernt die übrigen samt Markern.

| Block | Wann |
|---|---|
| `storno_flex_vor_frist` | Flex, Versand vor Ablauf der kostenfreien Stornofrist |
| `storno_flex_nach_frist` | Flex, Frist bereits abgelaufen |
| `storno_fix` | Fix |
| `zahlung` | nur Flex **und** Kanal EGB **und** noch nicht bezahlt |

Portalbuchungen (`BHC`) bekommen nie den Zahlungsblock. Die
`@guest.booking.com`-Adressen sind Weiterleitungen von Booking.com; eine
direkte Zahlungsaufforderung darüber verstößt gegen deren Regeln.

## Anrede-Fallback

In der Beispiel-CSV sind zwei von 18 Zeilen betroffen.

- Vorname leer, gleich der Stadt oder mit Ziffern → `Hallo zusammen,` / `Hi everyone,`
- sonst erstes Wort des Vornamens nehmen (`Ruth - Edith` → `Ruth`)
- durchgehend großgeschriebene Namen in Normalschreibweise wandeln

## Events

Quelle: öffentliches Google Sheet, Tab „Veranstaltungen", als CSV-Ausgabe:
`https://docs.google.com/spreadsheets/d/<ID>/gviz/tq?tqx=out:csv&sheet=Veranstaltungen`
Der Tab wird über den Namen angesprochen, deshalb wird keine `gid` gebraucht
und ein Verschieben der Tabs schadet nicht. Keine Zugangsdaten nötig, das
Sheet steht auf „Jeder mit dem Link: Leser".

- nur Status **freigegeben**
- Treffer, wenn Startdatum–Enddatum den Anreisetag oder den Folgetag berührt
  (Enddatum leer = eintägig)
- Dubletten über Titel + Startdatum entfernen
- maximal 3 Einträge, chronologisch; bei mehr Treffern haben Konzert,
  Ausstellung und Sport Vorrang vor Messe und „Uni & Kongress"
- fehlende Uhrzeit oder fehlender Ort: Angabe weglassen, kein leeres Trennzeichen
- Kategorie Sport: Enddatum ignorieren (es ist das offene Spieltermin-Fenster
  des Verbands, kein Zeitraum)
- Ortsfilter nur als Ausschlussliste (Schweinfurt, Bamberg, …). NICHT positiv
  auf „Würzburg" filtern: 19 der 42 freigegebenen Termine haben das Wort gar
  nicht im Ortsfeld (Cairo, Immerhin, Laby, Posthalle), 8 haben keinen Ort
- höchstens ein Termin je Kategorie und Tag, höchstens zwei pro Tag
- Kurztext wird nicht verwendet
- englische Titel aus Spalte „Titel EN"; wenn leer, deutscher Titel
- keine Treffer oder Sheet nicht erreichbar → Fallback-Satz mit Link auf die
  Veranstaltungsseite. Die Mail darf an einem Sheet-Problem nie scheitern.

Getestet gegen den echten Sheet-Stand (131 Zeilen, 42 freigegeben): Von 68
geprüften Anreisetagen bekommen 24 gar keinen Termin, 26 genau einen und nur 6
die vollen drei. Der Engpass ist die Freigabe, nicht der Filter.

## Offen

1. **Ratenfeld (Flex/Fix)** fehlt in der CSV – ohne das keine Block-Auswahl
2. **Sprachfeld (DE/EN)** fehlt in der CSV
3. **Adresse der Veranstaltungsseite** für den Fallback-Satz (`EVENTSEITE_URL`)
4. **Adresse des öffentlichen Sheets** und genaue Schreibweise des Tab-Namens
5. **Buchungsnummer:** CSV zeigt `13400`, die Bestätigung `410200-20-12058` –
   mit ASSD klären, welche Nummer der Gast sieht
6. **Kanal:** 4 von 18 Zeilen haben kein Kanalkürzel – was bedeutet leer?
7. **Spätanreise-Frist:** steht als Vorschlag „bis zum Tag vor der Anreise" drin
8. **SPF, DKIM, DMARC** für `babelfish-hostel.de` prüfen, bevor Zahlungsmails
   über den eigenen Server laufen
9. **Kurzfristbuchungen:** Wer weniger als 3 Tage vor Anreise bucht, taucht in
   der T-3-Liste nicht mehr auf und bekommt gar keine Vorab-Mail. Regel nötig.

## Technische Festlegungen

- Stil 1:1 aus `BookingWidgetEmail_DE/EN.html`, keine neuen Farben außer der
  Aktionsfarbe `#f0bc12` für den Zahlungs-Button
- Button als Tabelle, nicht als Link – Outlook stellt gestylte Links sonst als
  blassen Text dar
- wichtige Stile zusätzlich inline, weil einzelne Clients den `<style>`-Block
  im Kopf verwerfen
- keine CSS-Variablen, kein Flexbox/Grid, kein SVG, keine externen Fonts
- kein Abmeldelink nötig: Mails zum bestehenden Vertrag, keine Werbung
