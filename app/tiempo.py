"""Reloj legal del sistema. La marca de tiempo SIEMPRE la pone el servidor (nunca el móvil):
reloj del host sincronizado (chrony/NTP) + verificación SNTP periódica del desfase frente a una
fuente de referencia (configurable; p.ej. la hora legal del INM - Decreto 4175 de 2011)."""
from __future__ import annotations

import socket
import struct
import threading
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import get_settings

_NTP_EPOCH = 2_208_988_800  # segundos entre 1900-01-01 y 1970-01-01


@dataclass(frozen=True)
class Instante:
    utc: datetime
    ntp_sincronizado: bool
    desfase_ms: int


def consultar_desfase_sntp(servidor: str, timeout: float = 2.0) -> float:
    """Cliente SNTPv4 (RFC 4330). Devuelve el desfase (s) del reloj local frente al servidor."""
    paquete = b"\x23" + 47 * b"\0"  # LI=0, VN=4, Mode=3 (cliente)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        t1 = _time.time()
        s.sendto(paquete, (servidor, 123))
        datos, _ = s.recvfrom(512)
        t4 = _time.time()
    if len(datos) < 48:
        raise OSError("Respuesta NTP incompleta")

    def _ts(off: int) -> float:
        seg, frac = struct.unpack("!II", datos[off : off + 8])
        return seg - _NTP_EPOCH + frac / 2**32

    t2, t3 = _ts(32), _ts(40)
    return ((t2 - t1) + (t3 - t4)) / 2


class RelojLegal:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._desfase_s = 0.0
        self._ultima = 0.0
        self._sincronizado = False

    def _refrescar(self) -> None:
        s = get_settings()
        if not s.ntp_habilitado:
            return
        if _time.monotonic() - self._ultima < s.ntp_intervalo_segundos and self._ultima:
            return
        with self._lock:
            try:
                self._desfase_s = consultar_desfase_sntp(s.ntp_servidor)
                self._sincronizado = abs(self._desfase_s) * 1000 <= s.ntp_max_desfase_ms
            except OSError:
                self._sincronizado = False
            self._ultima = _time.monotonic()

    def ahora(self) -> Instante:
        self._refrescar()
        s = get_settings()
        base = datetime.now(timezone.utc)
        if s.ntp_habilitado and self._ultima:
            base += timedelta(seconds=self._desfase_s)
        return Instante(utc=base, ntp_sincronizado=self._sincronizado, desfase_ms=int(round(self._desfase_s * 1000)))


reloj_global = RelojLegal()


def get_reloj() -> RelojLegal:
    return reloj_global

