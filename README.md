# Pre-Arrival

Verschickt automatisch Informationsmails an Gäste, die in den nächsten Tagen im Hostel anreisen.

## Ablauf

1. Das PMS (ASSD) schickt täglich per Cronjob eine CSV mit den Anreisen in 3 Tagen an ein Postfach.
2. Der **Ingest-Job** (`src/run_ingest.py`) holt die Mail ab und liest die CSV in die SQLite-Datenbank.
3. Der **Versand-Job** (`src/run_send.py`) verschickt die Mails an fällige Gäste und protokolliert sie in `mail_log`.
4. Der **Tagesjob** (`src/run_daily.py`) führt beides nacheinander aus und ist für den Cronjob gedacht.

## Ordnerstruktur

    src/      alle Programme (.py) und src/templates/ (PreArrival_DE.html, PreArrival_EN.html)
    tests/    Tests
    conf/     .env (echte Konfiguration, nicht im Git) und .env.example (Vorlage)
    data/     Datenbank, Downloads, Vorschau, Lock-File (Inhalt nicht im Git)
    docs/     Dokumentation

Relative Pfade in der `.env` (z. B. `DB_PATH=data/pre_arrival.sqlite`) gelten ab der Projektwurzel, also dem Ordner über `src/`. Es ist unabhängig davon, aus welchem Ordner ein Skript gestartet wird.

## Dateien in src/

| Datei | Zweck |
|---|---|
| `db.py` | Schema (`import_log`, `arrivals`, `mail_log`), Verbindung, Löschfrist |
| `csv_import.py` | CSV-Import, auch per Kommandozeile nutzbar |
| `imap_anhang_abruf.py` | IMAP-Abruf des Anhangs |
| `run_ingest.py` | Ingest-Job: Postfach abrufen, importieren, aufräumen |
| `run_daily.py` | Tagesjob für den Cronjob: Ingest, dann Vorschau oder Versand, mit Lock und Healthcheck |
| `run_send.py` | Versand-Job (Trockenlauf, Testversand, live) |
| `render.py` | Vorlage füllen: Blöcke, Anrede, Datums-/Betragsformate |
| `events.py` | Veranstaltungen aus dem Google Sheet, mit Fallback |
| `graph_mail.py` | Versand über Microsoft Graph `sendMail` (HTML + Text) |
| `db_status.py`, `imap_diagnose.py` | Diagnose (Datenbankstand ohne Personendaten, Postfach-Prüfung) |

`requirements.txt` (Abhängigkeit `msal` für OAuth2) liegt in der Projektwurzel.

## Einrichtung

Python 3.9 oder neuer. Für das klassische IMAP-Login reicht die Standardbibliothek. Für Microsoft 365 (OAuth2) zusätzlich `pip install -r requirements.txt`.

    cp conf/.env.example conf/.env      # ausfüllen
    python src/run_ingest.py

Der Ingest-Job holt alle ungelesenen CSV-Mails ab, importiert sie, verschiebt sie nach "Verarbeitet", löscht die heruntergeladene CSV und entfernt Gäste nach Ablauf der Aufbewahrungsfrist. Bei einem Fehler endet er mit Exit-Code 1 und lässt die Mail ungelesen. Eine CSV nur mit Kopfzeile gilt als Tag ohne Anreisen.

Einen einzelnen Import ohne Postfach testen:

    python src/csv_import.py data/pre_arrival.sqlite beispiel.csv

Als Cronjob auf dem NAS (ASSD schickt die CSV um 06:00 Uhr), stündlich von 06:15 bis 11:15, damit eine verspätete Mail noch am selben Tag verarbeitet wird:

    15 6-11 * * * cd /pfad/zu/Pre-Arrival && python3 src/run_daily.py >> data/daily.log 2>&1

`run_daily.py` startet erst den Ingest, dann je nach `SEND_MODUS` entweder die Vorschau (Standard, es wird nichts gesendet; HTML-Dateien landen in `VORSCHAU_DIR` und werden nach `VORSCHAU_TAGE` Tagen gelöscht) oder den echten Versand (`live`). Beide sind idempotent, ein Lock-File verhindert parallele Läufe. Mit `HEALTHCHECK_URL` in der `.env` wird nach jedem erfolgreichen Lauf mit Import von heute ein Ping gesendet; bleibt er aus, schlägt Healthchecks.io Alarm.

## Versand

    python src/run_send.py --vorschau data/vorschau # Trockenlauf: HTML-Vorschauen, nichts wird gesendet
    python src/run_send.py --test-an ich@example.de # alle fälligen Mails an eine Testadresse, kein Protokoll
    python src/run_send.py --live                   # echter Versand, Protokoll in mail_log

Fällig sind Anreisen von heute bis heute + `SEND_TAGE_VORHER`. Pro Gast wird die Mail nur einmal gesendet (`mail_log`). Fehler werden bis zu 3-mal wiederholt; ein beim Senden abgebrochener Eintrag wird nie automatisch wiederholt (kein Doppelversand). Der Zahlungsbereich erscheint, wenn die CSV einen `Paylink` enthält (leer = bezahlt, das entscheidet ASSD). Der Einleitungssatz unterscheidet Anzahlung, Rest und Gesamtzahlung: Anzahlung/Rest nur bei Kanal `EGB` und Rate `FLEX` (`amou1` ≥ `total` = noch nichts bezahlt), alle anderen Gäste mit Paylink bekommen den Gesamttext. Der Frist-Satz zur kostenfreien Stornierung (`STORNO_TAGE`, Standard 2) richtet sich nach dem Versandtag. Sprache kommt aus der Spalte `Sprache` (D/E), nur ohne Spalte gilt die PLZ-Regel. Die Buchungsnummer kommt aus der Spalte `Buchungsnummer`.

## Tests

    python tests/test_send.py
    python tests/test_csv_import.py beispiel.csv
    python tests/test_run_ingest.py

## Betrieb auf dem NAS

Der Code wird auf dem Laptop entwickelt und zu GitHub gepusht. Das NAS holt ihn per `git pull`.
Die SQLite-Datei liegt auf dem NAS und darf nur von Prozessen genutzt werden, die auf dem NAS selbst laufen, nicht über eine Netzfreigabe von mehreren Rechnern.

## Datenschutz

Im Repository liegen weder Datenbanken noch CSV-Dateien noch Zugangsdaten. Gästedaten werden nach 30 Tagen nach Abreise gelöscht (`loesche_alte_gaeste()` in `db.py`).
