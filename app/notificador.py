"""Agente notificador multicanal (Push vía webhook/FCM relay, WhatsApp Business Cloud API, correo).
Se ejecuta fuera del ciclo de la petición (BackgroundTasks) y deja constancia en la bitácora."""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

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


def _email(n: Notificacion) -> str:
    s = get_settings()
    if not (s.smtp_host and n.destinatario_email):
        return "omitido"
    msg = EmailMessage()
    msg["From"] = s.smtp_remitente
    msg["To"] = n.destinatario_email
    msg["Subject"] = n.asunto
    msg.set_content(n.cuerpo)
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    with smtplib.SMTP(s.smtp_host, s.smtp_puerto, timeout=10) as smtp:
        smtp.starttls(context=ctx)
        if s.smtp_usuario and s.smtp_password:
            smtp.login(s.smtp_usuario, s.smtp_password)
        smtp.send_message(msg)
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

