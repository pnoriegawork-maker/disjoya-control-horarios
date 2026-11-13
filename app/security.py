"""Primitivas criptográficas: AES-256-GCM (datos en reposo a nivel de campo), HMAC de
dispositivos, hash de contraseñas (scrypt), JWT y TOTP RFC 6238 (HMAC-SHA256) para el QR."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

# ------------------------------- AES-256-GCM ---------------------------------------------
_VERSION_CIFRADO = b"\x01"


def _aes() -> AESGCM:
    return AESGCM(base64.b64decode(get_settings().aes_key_b64))


def cifrar(datos: bytes, contexto: str) -> bytes:
    """Devuelve version(1) || nonce(12) || ciphertext+tag. El contexto (AAD) ata el
    criptograma a su registro: no puede trasplantarse a otra fila sin fallar la autenticación."""
    nonce = os.urandom(12)
    return _VERSION_CIFRADO + nonce + _aes().encrypt(nonce, datos, contexto.encode())


def descifrar(blob: bytes, contexto: str) -> bytes:
    if blob[:1] != _VERSION_CIFRADO:
        raise ValueError("Versión de cifrado no soportada")
    return _aes().decrypt(blob[1:13], blob[13:], contexto.encode())


def cifrar_json(obj: dict, contexto: str) -> bytes:
    return cifrar(json.dumps(obj, separators=(",", ":"), sort_keys=True).encode(), contexto)


def descifrar_json(blob: bytes, contexto: str) -> dict:
    return json.loads(descifrar(blob, contexto))


# ------------------------------- Seudonimización -----------------------------------------
def hash_dispositivo(device_uuid: str) -> str:
    normalizado = str(uuid.UUID(device_uuid))
    return hmac.new(get_settings().pepper_dispositivo.encode(), normalizado.encode(), hashlib.sha256).hexdigest()


def sha256_hex(texto: str | bytes) -> str:
    if isinstance(texto, str):
        texto = texto.encode()
    return hashlib.sha256(texto).hexdigest()


# ------------------------------- Contraseñas (scrypt) -------------------------------------
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    sal = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=sal, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(sal).decode()}${base64.b64encode(dk).decode()}"


def verificar_password(password: str, almacenado: str) -> bool:
    try:
        alg, n, r, p, sal_b64, dk_b64 = almacenado.split("$")
        if alg != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(sal_b64), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


# ------------------------------- JWT -----------------------------------------------------
def crear_jwt(empleado_id: uuid.UUID, rol: str) -> str:
    s = get_settings()
    ahora = datetime.now(timezone.utc)
    payload = {
        "sub": str(empleado_id),
        "rol": rol,
        "iat": int(ahora.timestamp()),
        "exp": int((ahora + timedelta(minutes=s.jwt_minutos)).timestamp()),
        "jti": secrets.token_hex(8),
        "iss": "disjoya-control-horario",
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algoritmo)


def decodificar_jwt(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algoritmo], issuer="disjoya-control-horario")


# ------------------------------- TOTP (RFC 6238 / HMAC-SHA256) ---------------------------
DIGITOS_TOTP = 8
PREFIJO_QR = "DJY1"


def nuevo_secreto_totp() -> bytes:
    return os.urandom(32)


def ventana_totp(instante: datetime, ttl: int) -> int:
    return int(instante.timestamp()) // ttl


def codigo_totp(secreto: bytes, centro_id: uuid.UUID, ventana: int) -> str:
    # El centro se incluye en el MAC: un QR de una sede no es válido en otra.
    mensaje = struct.pack(">Q", ventana) + centro_id.bytes
    digest = hmac.new(secreto, mensaje, hashlib.sha256).digest()
    offset = digest[-1] & 0x0F
    binario = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binario % 10**DIGITOS_TOTP).zfill(DIGITOS_TOTP)


def construir_payload_qr(secreto: bytes, centro_id: uuid.UUID, ventana: int) -> str:
    return f"{PREFIJO_QR}.{centro_id}.{ventana}.{codigo_totp(secreto, centro_id, ventana)}"


def parsear_payload_qr(payload: str) -> tuple[uuid.UUID, int, str]:
    texto = payload.strip()
    if "?" in texto:
        import urllib.parse
        parsed = urllib.parse.urlparse(texto)
        qs = urllib.parse.parse_qs(parsed.query)
        if "qr" in qs:
            texto = qs["qr"][0]
        elif "code" in qs:
            texto = qs["code"][0]
    partes = texto.split(".")
    if len(partes) != 4 or partes[0] != PREFIJO_QR:
        raise ValueError("Formato de QR no reconocido")
    centro_id = uuid.UUID(partes[1])
    ventana = int(partes[2])
    codigo = partes[3]
    if len(codigo) != DIGITOS_TOTP or not codigo.isdigit():
        raise ValueError("Código TOTP malformado")
    return centro_id, ventana, codigo


def verificar_totp(secreto: bytes, centro_id: uuid.UUID, ventana_qr: int, codigo: str, ventana_actual: int, tolerancia: int) -> bool:
    if not (ventana_actual - tolerancia <= ventana_qr <= ventana_actual):
        return False
    return hmac.compare_digest(codigo_totp(secreto, centro_id, ventana_qr), codigo)

