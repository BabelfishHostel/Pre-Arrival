# Pre-Arrival

Verschickt automatisch Informationsmails an Gäste, die in den nächsten Tagen im Hostel anreisen.

## Ablauf

1. Das PMS (ASSD) schickt täglich per Cronjob eine CSV mit den Anreisen in 2 Tagen an ein Postfach.
2. Der **Ingest-Job** holt die Mail ab (`imap_anhang_abruf.py`) und liest die CSV in die SQLite-Datenbank (`csv_import.py`).
3. Der **Versand-Job** (noch offen) verschickt die Mails an fällige Gäste und protokolliert sie in `mail_log`.

## Dateien

| Datei | Zweck |
|---|---|
| `db.py` | Schema (`import_log`, `arrivals`, `mail_log`), Verbindung, Löschfrist |
| `csv_import.py` | CSV-Import, auch per Kommandozeile nutzbar |
| `imap_anhang_abruf.py` | IMAP-Abruf des Anhangs |
| `run_ingest.py` | Ingest-Job: Postfach abrufen, importieren, aufräumen |
| `test_csv_import.py` | Tests für den Import |
| `test_run_ingest.py` | Test des Ingest-Jobs gegen einen simulierten IMAP-Server |
| `.env.example` | Vorlage der Konfiguration |
| `requirements.txt` | Abhängigkeit `msal` für OAuth2 |

## Einrichtung

Python 3.9 oder neuer. Für das klassische IMAP-Login reicht die Standardbibliothek. Für Microsoft 365 (OAuth2) zusätzlich `pip install -r requirements.txt`.

    cp .env.example .env      # ausfüllen
    python run_ingest.py

Der Ingest-Job holt alle ungelesenen CSV-Mails ab, importiert sie, verschiebt sie nach "Verarbeitet", löscht die heruntergeladene CSV und entfernt Gäste nach Ablauf der Aufbewahrungsfrist. Bei einem Fehler endet er mit Exit-Code 1 und lässt die Mail ungelesen. Eine CSV nur mit Kopfzeile gilt als Tag ohne Anreisen.

Einen einzelnen Import ohne Postfach testen:

    python csv_import.py /pfad/zur/pre_arrival.sqlite beispiel.csv

Als Cronjob auf dem NAS, z. B. täglich um 06:00 Uhr:

    0 6 * * * cd /pfad/zu/Pre-Arrival && python3 run_ingest.py >> ingest.log 2>&1

## Tests

    python test_csv_import.py beispiel.csv
    python test_run_ingest.py

## Betrieb auf dem NAS

Der Code wird auf dem Laptop entwickelt und zu GitHub gepusht. Das NAS holt ihn per `git pull`.
Die SQLite-Datei liegt auf dem NAS und darf nur von Prozessen genutzt werden, die auf dem NAS selbst laufen, nicht über eine Netzfreigabe von mehreren Rechnern.

## Datenschutz

Im Repository liegen weder Datenbanken noch CSV-Dateien noch Zugangsdaten. Gästedaten werden nach 30 Tagen nach Abreise gelöscht (`loesche_alte_gaeste()` in `db.py`).
