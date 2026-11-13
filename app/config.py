"""Configuración centralizada (12-factor). Todos los secretos llegan por variables de entorno
(o un gestor de secretos / KMS que las inyecte). Nunca se versionan en el repositorio."""
from __future__ import annotations

import base64
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DJY_", extra="ignore")

    # --- Persistencia ---------------------------------------------------------------
    # En producción: sslmode=verify-full (TLS 1.3 hacia PostgreSQL) y usuario sin BYPASSRLS.
    database_url: str = "postgresql+psycopg://app_api:cambiar@localhost:5432/control_horario?sslmode=prefer"

    # --- Identidad y criptografía ---------------------------------------------------
    jwt_secret: str
    jwt_algoritmo: str = "HS256"
    jwt_minutos: int = 480
    aes_key_b64: str  # 32 bytes (AES-256) en Base64, provisto por KMS / Vault
    pepper_dispositivo: str  # clave HMAC para seudonimizar Device_UUID

    # --- Tiempo legal ---------------------------------------------------------------
    zona_horaria: str = "America/Bogota"
    ntp_habilitado: bool = True
    ntp_servidor: str = "time.google.com"
    ntp_intervalo_segundos: int = 300
    ntp_max_desfase_ms: int = 1000
    ntp_modo_estricto: bool = False  # True => rechaza marcaciones si no hay sincronía NTP

    # --- QR dinámico y antifraude -----------------------------------------------------
    qr_ttl_segundos: int = 30
    qr_ventanas_tolerancia: int = 1  # ventanas previas aceptadas (latencia de red)
    gps_precision_max_m: float = 35.0
    velocidad_max_kmh: float = 150.0  # detección de "viaje imposible" entre marcaciones
    tolerancia_minutos_jornada: int = 10

    # --- Agente notificador -----------------------------------------------------------
    smtp_host: str | None = None
    smtp_puerto: int = 587
    smtp_usuario: str | None = None
    smtp_password: str | None = None
    smtp_remitente: str = "control-horario@empresa.com.co"
    whatsapp_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_plantilla: str = "aprobacion_horas_extras"
    whatsapp_api_version: str = "v21.0"
    push_webhook_url: str | None = None

    entorno: str = "produccion"

    @field_validator("qr_ttl_segundos")
    @classmethod
    def _ttl_valido(cls, v: int) -> int:
        if not 15 <= v <= 30:
            raise ValueError("qr_ttl_segundos debe estar entre 15 y 30 segundos")
        return v

    @field_validator("aes_key_b64")
    @classmethod
    def _aes_256(cls, v: str) -> str:
        if len(base64.b64decode(v)) != 32:
            raise ValueError("aes_key_b64 debe decodificar exactamente 32 bytes (AES-256)")
        return v

    @field_validator("jwt_secret", "pepper_dispositivo")
    @classmethod
    def _secreto_robusto(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("los secretos deben tener al menos 32 caracteres")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()

