#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
imap_anhang_abruf.py - Wiederverwendbarer IMAP-Abruf von E-Mail-Anhängen.
 
Holt den Anhang (Standard: PDF) der neuesten ungelesenen E-Mail ab, die zu
einem Absender-/Betreff-Filter passt, und verschiebt die Mail erst NACH
erfolgreicher Weiterverarbeitung in einen "Verarbeitet"-Ordner. Schlägt die
Weiterverarbeitung fehl, bleibt die Mail ungelesen im Postfach und wird beim
nächsten Lauf automatisch erneut versucht.
 
Enthält keinerlei Zugangsdaten. Zwei Anmeldearten werden unterstützt:
    1. OAuth2 (XOAUTH2, z. B. Exchange Online): `token_provider` übergeben,
       ein Callable ohne Argumente, das ein gültiges Access-Token liefert.
       Eine fertige MSAL-Variante (Client-Credentials-Flow) steckt in
       `hole_access_token_msal()` und liest ihre Werte aus Umgebungsvariablen.
    2. Passwort (klassisches IMAP-LOGIN): `passwort` übergeben, z. B. aus
       der Umgebungsvariable IMAP_PASSWORD.
 
Voraussetzung: nur Standardbibliothek. Für OAuth2 via MSAL zusätzlich:
    pip install msal
 
Beispiel:
    konfig = ImapKonfig(
        server="imap.example.com",
        user="postfach@example.com",
        absender_filter="noreply@example.com",
        betreff_filter="Tagesreport",
        zielordner=Path("./downloads"),
    )
    ergebnis = hole_anhang(konfig, passwort=os.environ["IMAP_PASSWORD"])
    if ergebnis:
        try:
            verarbeite(ergebnis.dateipfad)          # eigene Logik
            markiere_verarbeitet(ergebnis.imap, ergebnis.msg_num, konfig)
        finally:
            ergebnis.imap.logout()
"""
 
import email
import imaplib
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from email.header import decode_header, make_header
from pathlib import Path
from typing import Callable, List, Optional, TypeVar
 
T = TypeVar("T")
 
 
@dataclass
class ImapKonfig:
    """Verbindungs- und Filtereinstellungen für den Abruf (ohne Zugangsdaten).
 
    Attributes:
        server: IMAP-Hostname, z. B. 'outlook.office365.com'.
        user: Postfachadresse, deren Mails abgerufen werden.
        absender_filter: Teilstring des Absenders (IMAP-FROM-Suche).
        betreff_filter: Teilstring des Betreffs (IMAP-SUBJECT-Suche).
            Absender- und Betrefffilter sollten reines ASCII sein, da
            imaplib Suchkriterien standardmäßig ASCII-kodiert.
        zielordner: Lokaler Ordner, in den der Anhang gespeichert wird.
        port: IMAP-SSL-Port.
        postfach: Zu durchsuchender IMAP-Ordner.
        verarbeitet_ordner: IMAP-Zielordner für erfolgreich verarbeitete Mails.
        dateiendung: Endung des gesuchten Anhangs (klein, mit Punkt).
    """
 
    server: str
    user: str
    absender_filter: str
    betreff_filter: str
    zielordner: Path
    port: int = 993
    postfach: str = "INBOX"
    verarbeitet_ordner: str = "Verarbeitet"
    dateiendung: str = ".pdf"
 
 
@dataclass
class AnhangErgebnis:
    """Ergebnis eines erfolgreichen Abrufs.
 
    Attributes:
        dateipfad: Lokaler Pfad des heruntergeladenen Anhangs.
        imap: Aktive, eingeloggte Verbindung (Postfach bereits ausgewählt).
            Der Aufrufer ist für `logout()` zuständig.
        msg_num: Nachrichtennummer der Quell-Mail, nötig für
            `markiere_verarbeitet()`.
    """
 
    dateipfad: Path
    imap: imaplib.IMAP4_SSL
    msg_num: bytes
 
 
def ts() -> str:
    """Erzeugt einen Zeitstempel für Log-Ausgaben.
 
    Returns:
        Aktuelle Zeit im Format 'YYYY-MM-DD HH:MM:SS'.
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
 
 
def mit_netzwerk_retry(
    funktion: Callable[[], T],
    versuche: int = 3,
    wartezeit_sek: float = 5.0,
    was: str = "Netzwerkoperation",
) -> T:
    """Führt eine Netzwerkoperation mit Wiederholungsversuchen bei OSError aus.
 
    Hintergrund: DNS-Auflösung schlägt auf manchen NAS-/Embedded-Systemen
    gelegentlich transient fehl (z. B. 'Device or resource busy', wenn
    /etc/resolv.conf gerade neu geschrieben wird). Ein kurzer erneuter
    Versuch behebt das zuverlässig.
 
    Args:
        funktion: Argumentloses Callable, das die Operation ausführt.
        versuche: Maximale Anzahl Versuche.
        wartezeit_sek: Pause zwischen den Versuchen in Sekunden.
        was: Kurzbeschreibung für Log-Ausgaben.
 
    Returns:
        Rückgabewert von `funktion()`.
 
    Raises:
        OSError: Wenn der Fehler nach allen Versuchen bestehen bleibt.
    """
    letzter_fehler: Optional[OSError] = None
    for versuch in range(1, versuche + 1):
        try:
            return funktion()
        except OSError as exc:
            letzter_fehler = exc
            print(f"[{ts()}] ⚠ {was} fehlgeschlagen (Versuch {versuch}/{versuche}): {exc}")
            if versuch < versuche:
                time.sleep(wartezeit_sek)
    assert letzter_fehler is not None  # Schleife läuft mindestens einmal
    raise letzter_fehler
 
 
def hole_access_token_msal(scope: Optional[List[str]] = None) -> str:
    """Holt per MSAL ein OAuth2-Token (Client-Credentials-Flow, App-only).
 
    Zugangsdaten kommen ausschließlich aus Umgebungsvariablen
    AZURE_TENANT_ID, AZURE_CLIENT_ID und AZURE_CLIENT_SECRET - nie im Code
    ablegen. Für Exchange-Online-IMAP braucht die App die
    Anwendungsberechtigung 'IMAP.AccessAsApp' mit Administrator-Zustimmung.
 
    Args:
        scope: Angeforderte Scopes; Standard ist der IMAP-Scope von
            Exchange Online.
 
    Returns:
        Gültiges Bearer-Token.
 
    Raises:
        RuntimeError: Bei fehlenden Umgebungsvariablen, fehlendem msal-Paket,
            Netzwerkproblemen oder wenn kein Token ausgestellt wurde.
    """
    try:
        import msal  # bewusst lokal: nur nötig, wenn OAuth2 tatsächlich genutzt wird
    except ImportError as exc:
        raise RuntimeError("Paket 'msal' fehlt - 'pip install msal'.") from exc
 
    try:
        tenant = os.environ["AZURE_TENANT_ID"]
        client_id = os.environ["AZURE_CLIENT_ID"]
        secret = os.environ["AZURE_CLIENT_SECRET"]
    except KeyError as exc:
        raise RuntimeError(f"Umgebungsvariable {exc} nicht gesetzt.") from exc
 
    scope = scope or ["https://outlook.office365.com/.default"]
    try:
        app = msal.ConfidentialClientApplication(
            client_id=client_id,
            client_credential=secret,
            authority=f"https://login.microsoftonline.com/{tenant}",
        )
        ergebnis = app.acquire_token_for_client(scopes=scope)
    except ValueError as exc:  # MSAL: ungültige Authority/Tenant-ID
        raise RuntimeError(f"OAuth2-Konfiguration ungültig: {exc}") from exc
 
    if "access_token" not in ergebnis:
        raise RuntimeError(
            f"Token nicht ausgestellt ({ergebnis.get('error', 'unbekannt')}): "
            f"{ergebnis.get('error_description', 'keine Details')}"
        )
    return ergebnis["access_token"]
 
 
def xoauth2_string(user: str, access_token: str) -> bytes:
    """Baut den SASL-XOAUTH2-Authentifizierungsstring für IMAP.
 
    Args:
        user: Postfachadresse.
        access_token: Gültiges OAuth2-Zugriffstoken.
 
    Returns:
        Auth-String im vom Server erwarteten Byte-Format.
    """
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01".encode("utf-8")
 
 
def verbinde(
    konfig: ImapKonfig,
    token_provider: Optional[Callable[[], str]] = None,
    passwort: Optional[str] = None,
) -> imaplib.IMAP4_SSL:
    """Baut eine IMAP-Verbindung auf, meldet sich an und wählt das Postfach.
 
    Args:
        konfig: Verbindungseinstellungen.
        token_provider: Callable für OAuth2-Token (hat Vorrang vor Passwort).
        passwort: Passwort für klassisches IMAP-LOGIN.
 
    Returns:
        Eingeloggte Verbindung mit ausgewähltem Postfach.
 
    Raises:
        ValueError: Wenn weder Token-Provider noch Passwort übergeben wurde.
        OSError: Bei anhaltenden Netzwerkproblemen (nach Retries).
        imaplib.IMAP4.error: Bei Anmelde- oder Select-Fehlern.
    """
    if token_provider is None and passwort is None:
        raise ValueError("Entweder token_provider oder passwort angeben.")
 
    imap = mit_netzwerk_retry(
        lambda: imaplib.IMAP4_SSL(konfig.server, konfig.port),
        was="IMAP-Verbindungsaufbau",
    )
    try:
        if token_provider is not None:
            token = mit_netzwerk_retry(token_provider, was="OAuth2-Tokenanfrage")
            imap.authenticate("XOAUTH2", lambda _: xoauth2_string(konfig.user, token))
        else:
            imap.login(konfig.user, passwort)  # type: ignore[arg-type]
        imap.select(konfig.postfach)
    except imaplib.IMAP4.error:
        imap.logout()
        raise
    return imap
 
 
def _sicherer_dateiname(roh: str) -> str:
    """Entschärft einen Anhang-Dateinamen für das lokale Dateisystem.
 
    Anhangsnamen stammen aus fremden Mails; ohne Bereinigung wären
    Pfadanteile wie '../' möglich (Path-Traversal).
 
    Args:
        roh: Dekodierter Dateiname aus dem Mail-Header.
 
    Returns:
        Dateiname ohne Pfadanteile und ohne Sonderzeichen außer '._- '.
    """
    name = Path(roh.replace("\\", "/")).name
    return re.sub(r"[^\w.\- ]", "_", name) or "anhang"
 
 
def hole_anhang(
    konfig: ImapKonfig,
    token_provider: Optional[Callable[[], str]] = None,
    passwort: Optional[str] = None,
) -> Optional[AnhangErgebnis]:
    """Lädt den Anhang der neuesten passenden, ungelesenen Mail herunter.
 
    Nutzt `BODY.PEEK[]` statt `RFC822`, damit die Mail beim Abruf nicht als
    gelesen markiert wird - erst `markiere_verarbeitet()` schließt sie ab.
    Bei mehreren Treffern wird nur die neueste verarbeitet; ältere bleiben
    ungelesen und kommen beim nächsten Lauf an die Reihe.
 
    Args:
        konfig: Verbindungs- und Filtereinstellungen.
        token_provider: Callable für OAuth2-Token (siehe `verbinde`).
        passwort: Passwort für IMAP-LOGIN (siehe `verbinde`).
 
    Returns:
        `AnhangErgebnis` mit offener Verbindung, oder None, wenn keine
        passende Mail bzw. kein Anhang gefunden wurde (Verbindung ist
        dann bereits geschlossen).
 
    Raises:
        ValueError, OSError, imaplib.IMAP4.error: siehe `verbinde`; zusätzlich
            imaplib.IMAP4.error, wenn der Fetch fehlschlägt.
    """
    konfig.zielordner.mkdir(parents=True, exist_ok=True)
    imap = verbinde(konfig, token_provider, passwort)
 
    try:
        kriterium = (
            f'(UNSEEN FROM "{konfig.absender_filter}" '
            f'SUBJECT "{konfig.betreff_filter}")'
        )
        status, daten = imap.search(None, kriterium)
        if status != "OK" or not daten[0]:
            print(f"[{ts()}] ℹ Keine passende Mail gefunden ({kriterium}).")
            imap.logout()
            return None
 
        msg_num = daten[0].split()[-1]  # höchste Nummer = neueste Mail
        status, msg_daten = imap.fetch(msg_num, "(BODY.PEEK[])")
        if status != "OK" or not msg_daten or msg_daten[0] is None:
            raise imaplib.IMAP4.error(f"Fetch fehlgeschlagen für Nachricht {msg_num!r}")
 
        nachricht = email.message_from_bytes(msg_daten[0][1])
        for teil in nachricht.walk():
            dateiname = teil.get_filename()
            if not dateiname:
                continue
            dateiname = str(make_header(decode_header(dateiname)))
            if not dateiname.lower().endswith(konfig.dateiendung):
                continue
            inhalt = teil.get_payload(decode=True)
            if not inhalt:
                continue
 
            zeitstempel = datetime.now().strftime("%Y%m%d_%H%M%S")
            pfad = konfig.zielordner / f"{zeitstempel}_{_sicherer_dateiname(dateiname)}"
            pfad.write_bytes(inhalt)
            print(f"[{ts()}] 📎 Anhang heruntergeladen: {pfad}")
            return AnhangErgebnis(dateipfad=pfad, imap=imap, msg_num=msg_num)
 
        print(f"[{ts()}] ⚠ Mail gefunden, aber kein '{konfig.dateiendung}'-Anhang.")
        imap.logout()
        return None
    except (imaplib.IMAP4.error, OSError):
        # Verbindung nicht offen lassen, wenn der Aufrufer sie nie erhält.
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
        raise
 
 
def markiere_verarbeitet(
    imap: imaplib.IMAP4_SSL, msg_num: bytes, konfig: ImapKonfig
) -> None:
    """Verschiebt eine erfolgreich verarbeitete Mail in den Ordner 'Verarbeitet'.
 
    Nutzt COPY + \\Deleted + EXPUNGE statt der MOVE-Erweiterung (RFC 6851),
    weil nicht jeder Server MOVE unterstützt. Der Zielordner wird bei Bedarf
    angelegt; ein Fehler, weil er schon existiert, wird bewusst ignoriert.
 
    Args:
        imap: Aktive Verbindung mit ausgewähltem Postfach.
        msg_num: Nachrichtennummer aus `AnhangErgebnis`.
        konfig: Einstellungen (Name des Zielordners).
 
    Raises:
        imaplib.IMAP4.error: Wenn COPY fehlschlägt (Mail bleibt dann
            unverändert im Postfach).
    """
    try:
        imap.create(konfig.verarbeitet_ordner)
    except imaplib.IMAP4.error:
        pass  # Ordner existiert bereits - kein echter Fehler
 
    status, _ = imap.copy(msg_num, konfig.verarbeitet_ordner)
    if status != "OK":
        raise imaplib.IMAP4.error(
            f"Verschieben nach '{konfig.verarbeitet_ordner}' fehlgeschlagen"
        )
    imap.store(msg_num, "+FLAGS", "\\Deleted")
    imap.expunge()