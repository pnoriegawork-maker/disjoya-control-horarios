"""Bitácora forense encadenada (hash chain SHA-256). Cada evento sella el hash del anterior; la
alteración, inserción o eliminación de cualquier registro rompe la cadena y es detectable.
En PostgreSQL además hay triggers que prohíben UPDATE/DELETE/TRUNCATE y validan el encadenamiento."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import AuditoriaLog
from .security import sha256_hex

GENESIS = "0" * 64
_LOCK_AUDITORIA = 7_202_101  # clave de advisory lock (serializa la cadena)


def jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.astimezone(timezone.utc).isoformat()
    if isinstance(obj, (date, Decimal, uuid.UUID)):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.hex()
    return obj


def _canonico(reg: AuditoriaLog | dict) -> str:
    g = (lambda k: reg[k]) if isinstance(reg, dict) else (lambda k: getattr(reg, k))
    cuerpo = {
        "ocurrido_en": g("ocurrido_en").astimezone(timezone.utc).isoformat(),
        "actor_id": str(g("actor_id")) if g("actor_id") else None,
        "actor_rol": g("actor_rol"),
        "accion": g("accion"),
        "entidad": g("entidad"),
        "entidad_id": g("entidad_id"),
        "ip_origen": str(g("ip_origen")) if g("ip_origen") else None,
        "dispositivo_hash": g("dispositivo_hash"),
        "user_agent": g("user_agent"),
        "datos": g("datos"),
    }
    return json.dumps(cuerpo, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def calcular_hash(hash_anterior: str, reg: AuditoriaLog | dict) -> str:
    return sha256_hex(hash_anterior + "|" + _canonico(reg))


def registrar(
    sesion: Session,
    *,
    ocurrido_en: datetime,
    accion: str,
    entidad: str,
    entidad_id: Any = None,
    actor_id: uuid.UUID | None = None,
    actor_rol: str = "SISTEMA",
    ip_origen: str | None = None,
    dispositivo_hash: str | None = None,
    user_agent: str | None = None,
    datos: dict | None = None,
) -> AuditoriaLog:
    if sesion.get_bind().dialect.name == "postgresql":
        sesion.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_AUDITORIA})
    anterior = sesion.execute(select(AuditoriaLog.hash_actual).order_by(AuditoriaLog.id.desc()).limit(1)).scalar()
    hash_anterior = anterior or GENESIS
    campos = dict(
        ocurrido_en=ocurrido_en.astimezone(timezone.utc),
        actor_id=actor_id,
        actor_rol=actor_rol,
        accion=accion,
        entidad=entidad,
        entidad_id=str(entidad_id) if entidad_id is not None else None,
        ip_origen=ip_origen,
        dispositivo_hash=dispositivo_hash,
        user_agent=(user_agent or "")[:300] or None,
        datos=jsonable(datos or {}),
    )
    reg = AuditoriaLog(**campos, hash_anterior=hash_anterior, hash_actual=calcular_hash(hash_anterior, campos))
    sesion.add(reg)
    sesion.flush()
    return reg


def verificar_cadena(sesion: Session) -> dict:
    esperado = GENESIS
    total = 0
    for reg in sesion.execute(select(AuditoriaLog).order_by(AuditoriaLog.id)).scalars():
        total += 1
        if reg.hash_anterior != esperado:
            return {"integra": False, "registros_verificados": total, "falla_en_id": reg.id, "causa": "eslabón roto (hash_anterior)"}
        if calcular_hash(reg.hash_anterior, reg) != reg.hash_actual:
            return {"integra": False, "registros_verificados": total, "falla_en_id": reg.id, "causa": "contenido alterado"}
        esperado = reg.hash_actual
    return {"integra": True, "registros_verificados": total, "hash_cabeza": esperado}

