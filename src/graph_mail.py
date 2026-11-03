#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
graph_mail.py - Mailversand über Microsoft Graph (sendMail), mit Text-Alternative.

Voraussetzung in Azure: App-Berechtigung "Mail.Send" (Anwendung) für die App,
die auch für IMAP genutzt wird, idealerweise per Application Access Policy
auf das Postfach info@babelfish-hostel.de beschränkt.

Die Mail wird als MIME (base64) gesendet, damit HTML UND Text enthalten sind.
Graph liefert bei sendMail keine Message-ID zurück; wir erzeugen daher selbst
eine Message-ID und tragen sie in den Kopf ein (steht danach im mail_log).
"""

import base64
import json
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Callable, Optional

GRAPH_BASIS = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


class VersandFehler(Exception):
    """Graph hat die Mail abgelehnt oder war nicht erreichbar."""

    def __init__(self, text: str, dauerhaft: bool):
        super().__init__(text)
        self.dauerhaft = dauerhaft  # True: erneuter Versuch sinnlos (z. B. 400/403)


def baue_mime(absender: str, an: str, betreff: str, html: str, text: str,
              antwort_an: Optional[str] = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = absender, an, betreff
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=absender.split("@")[-1])
    if antwort_an:
        msg["Reply-To"] = antwort_an
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def hole_graph_token(tenant: str, client_id: str, secret: str) -> Callable[[], str]:
    """Token-Provider (msal, Client-Credentials) für Graph."""
    import msal

    app = msal.ConfidentialClientApplication(
        client_id, authority=f"https://login.microsoftonline.com/{tenant}", client_credential=secret)

    def provider() -> str:
        erg = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
        if "access_token" not in erg:
            raise VersandFehler(f"Graph-Token fehlgeschlagen: {erg.get('error_description', erg)}", True)
        return erg["access_token"]

    return provider


def sende_mime(msg: EmailMessage, postfach: str, token: str, basis: str = GRAPH_BASIS,
               timeout: int = 30) -> str:
    """Sendet die MIME-Nachricht über /users/{postfach}/sendMail. Gibt die Message-ID zurück."""
    daten = base64.b64encode(msg.as_bytes()).decode("ascii")
    req = urllib.request.Request(
        f"{basis}/users/{postfach}/sendMail", data=daten.encode("ascii"), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            if r.status != 202:
                raise VersandFehler(f"Unerwarteter Status {r.status}", False)
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        # 4xx (außer 408/429) = Anfrage/Berechtigung falsch -> nicht blind wiederholen
        dauerhaft = 400 <= e.code < 500 and e.code not in (408, 429)
        raise VersandFehler(f"HTTP {e.code}: {detail}", dauerhaft) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise VersandFehler(f"Netzwerkfehler: {e}", False) from e
    return msg["Message-ID"]
