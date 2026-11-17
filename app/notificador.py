"""Agente notificador multicanal (Push vía webhook/FCM relay, WhatsApp Business Cloud API, correo).
Se ejecuta fuera del ciclo de la petición (BackgroundTasks) y deja constancia en la bitácora."""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
import unicodedata

import httpx

from .config import get_settings

log = logging.getLogger("agente.notificador")


@dataclass
class Notificacion:
    destinatario_email: str | None
    destinatario_whatsapp: str | None
    asunto: str
    cuerpo: str
    parametros_plantilla: list[str]
    datos: dict


def _limpiar_asunto_dkim(asunto: str) -> str:
    """Normaliza el asunto a ASCII limpio en una sola línea (< 75 chars) para evitar
    plegado RFC 2047 multiparte que rompe la firma DKIM en retransmisiones de Gmail."""
    texto = asunto.replace("—", "-").replace("–", "-")
    nfkd = unicodedata.normalize("NFKD", texto)
    solo_ascii = "".join(c for c in nfkd if ord(c) < 128 and c.isprintable())
    limpio = " ".join(solo_ascii.split()).strip(" -:")
    return limpio[:74] if limpio else "Notificacion Control Horario - Disjoya Autopartes"


def _email(n: Notificacion) -> str:
    s = get_settings()
    if not (s.smtp_host and n.destinatario_email):
        return "omitido"
    cuenta_smtp = (s.smtp_usuario or "pedro.noriega@gmail.com").strip()
    msg = MIMEText(n.cuerpo, "plain", "utf-8")
    # RFC 5322 estricto: sin puntos en el display-name y coincidiendo 100% con la cuenta autenticada para pasar DKIM
    msg["From"] = formataddr(("Disjoya Autopartes SAS", cuenta_smtp))
    msg["Reply-To"] = cuenta_smtp
    msg["To"] = n.destinatario_email.strip()
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="gmail.com")
    msg["Subject"] = _limpiar_asunto_dkim(n.asunto)
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    with smtplib.SMTP(s.smtp_host, s.smtp_puerto, timeout=12) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ctx)
        smtp.ehlo()
        if s.smtp_usuario and s.smtp_password:
            smtp.login(s.smtp_usuario, s.smtp_password)
        smtp.sendmail(cuenta_smtp, [n.destinatario_email.strip()], msg.as_string())
    return "enviado"


def _whatsapp(n: Notificacion) -> str:
    s = get_settings()
    if not (s.whatsapp_token and s.whatsapp_phone_number_id and n.destinatario_whatsapp):
        return "omitido"
    url = f"https://graph.facebook.com/{s.whatsapp_api_version}/{s.whatsapp_phone_number_id}/messages"
    cuerpo = {
        "messaging_product": "whatsapp",
        "to": n.destinatario_whatsapp,
        "type": "template",
        "template": {
            "name": s.whatsapp_plantilla,
            "language": {"code": "es"},
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": p[:1000]} for p in n.parametros_plantilla]}
            ],
        },
    }
    r = httpx.post(url, json=cuerpo, headers={"Authorization": f"Bearer {s.whatsapp_token}"}, timeout=10)
    r.raise_for_status()
    return "enviado"


def _push(n: Notificacion) -> str:
    s = get_settings()
    if not s.push_webhook_url:
        return "omitido"
    r = httpx.post(s.push_webhook_url, json={"titulo": n.asunto, "cuerpo": n.cuerpo, "datos": n.datos}, timeout=10)
    r.raise_for_status()
    return "enviado"


def despachar(n: Notificacion) -> dict[str, str]:
    resultado: dict[str, str] = {}
    for canal, fn in (("push", _push), ("whatsapp", _whatsapp), ("email", _email)):
        try:
            resultado[canal] = fn(n)
        except Exception as exc:  # el fallo de un canal no bloquea los demás
            log.warning("Fallo canal %s: %s", canal, exc)
            resultado[canal] = f"error: {type(exc).__name__}"
    if all(v == "omitido" for v in resultado.values()):
        log.info("[NOTIFICACION-LOCAL] %s | %s", n.asunto, n.cuerpo)
    return resultado

