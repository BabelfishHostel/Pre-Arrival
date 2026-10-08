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
| `test_csv_import.py` | Tests für den Import |
| `.env.example` | Vorlage der Konfiguration |

## Einrichtung

Es wird nur die Python-Standardbibliothek benötigt (Python 3.9 oder neuer).

    cp .env.example .env      # ausfüllen
    python csv_import.py /pfad/zur/pre_arrival.sqlite beispiel.csv

## Tests

    python test_csv_import.py beispiel.csv

## Betrieb auf dem NAS

Der Code wird auf dem Laptop entwickelt und zu GitHub gepusht. Das NAS holt ihn per `git pull`.
Die SQLite-Datei liegt auf dem NAS und darf nur von Prozessen genutzt werden, die auf dem NAS selbst laufen, nicht über eine Netzfreigabe von mehreren Rechnern.

## Datenschutz

Im Repository liegen weder Datenbanken noch CSV-Dateien noch Zugangsdaten. Gästedaten werden nach 30 Tagen nach Abreise gelöscht (`loesche_alte_gaeste()` in `db.py`).
