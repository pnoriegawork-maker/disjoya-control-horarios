"""Contratos Pydantic v2 de entrada/salida."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from .legal import a_utc

Rol = Literal["TRABAJADOR", "JEFE_INMEDIATO", "NOMINA_RRHH", "AUDITOR_SUPERADMIN"]


class TokenIn(BaseModel):
    numero_documento: str = Field(min_length=4, max_length=20)
    password: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    rol: Rol
    expira_en_minutos: int


class CentroIn(BaseModel):
    nombre: str = Field(min_length=3, max_length=150)
    direccion: str = Field(min_length=5, max_length=250)
    municipio_dane: str = Field(pattern=r"^\d{5}$")
    latitud: Decimal = Field(ge=Decimal("-4.3"), le=Decimal("16.0"), decimal_places=6)
    longitud: Decimal = Field(ge=Decimal("-82.0"), le=Decimal("-66.8"), decimal_places=6)
    radio_metros: int = Field(default=50, ge=10, le=1000)
    poligono: list[tuple[float, float]] | None = Field(default=None, min_length=3, max_length=200)


class CentroCreadoOut(BaseModel):
    id: uuid.UUID
    nombre: str
    radio_metros: int
    kiosco_api_key: str = Field(description="Se muestra UNA sola vez. Configúrela en la pantalla/kiosco del QR.")


class EmpleadoIn(BaseModel):
    tipo_documento: Literal["CC", "CE", "PPT", "PA", "TI"]
    numero_documento: str = Field(pattern=r"^[0-9A-Za-z]{4,20}$")
    nombres: str = Field(min_length=2, max_length=120)
    apellidos: str = Field(min_length=2, max_length=120)
    email: EmailStr
    telefono_whatsapp: str | None = Field(default=None, pattern=r"^57\d{10}$")
    rol: Rol = "TRABAJADOR"
    cargo: str = Field(min_length=2, max_length=120)
    jefe_inmediato_id: uuid.UUID | None = None
    centro_trabajo_id: uuid.UUID | None = None
    fecha_nacimiento: date | None = None
    salario_base: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    salario_integral: bool = False
    es_direccion_confianza: bool = False
    password: str = Field(min_length=12, max_length=128)


class EmpleadoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    numero_documento: str
    nombres: str
    apellidos: str
    rol: Rol
    cargo: str
    jefe_inmediato_id: uuid.UUID | None
    centro_trabajo_id: uuid.UUID | None


class _RangoHorario(BaseModel):
    inicio: datetime
    fin: datetime

    @model_validator(mode="after")
    def _normalizar(self):
        self.inicio, self.fin = a_utc(self.inicio), a_utc(self.fin)
        if self.fin <= self.inicio:
            raise ValueError("fin debe ser posterior a inicio")
        return self


class TurnoIn(_RangoHorario):
    empleado_id: uuid.UUID

    @model_validator(mode="after")
    def _duracion(self):
        if (self.fin - self.inicio).total_seconds() > 12 * 3600:
            raise ValueError("Un turno ordinario no puede superar 12 horas")
        return self


class ConsentimientoIn(BaseModel):
    version_politica: str = Field(max_length=20)
    acepta_tratamiento_laboral: bool
    acepta_geolocalizacion: bool


class DispositivoIn(BaseModel):
    device_uuid: uuid.UUID


class MarcacionIn(BaseModel):
    qr_payload: str = Field(min_length=20, max_length=120)
    device_uuid: uuid.UUID
    latitud: float = Field(ge=-90, le=90)
    longitud: float = Field(ge=-180, le=180)
    precision_m: float = Field(gt=0, le=5000)
    ubicacion_simulada: bool = Field(default=False, description="Bandera mock-location reportada por el SO")


class SolicitudHEIn(_RangoHorario):
    empleado_id: uuid.UUID | None = Field(default=None, description="Vacío = el propio solicitante")
    motivo: str = Field(min_length=15, max_length=1000)


class DecisionHEIn(BaseModel):
    aprobar: bool
    comentario: str | None = Field(default=None, max_length=1000)

    @field_validator("comentario")
    @classmethod
    def _limpio(cls, v: str | None) -> str | None:
        return v.strip() if v else v


class SolicitudHEOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    empleado_id: uuid.UUID
    solicitado_por_id: uuid.UUID
    jefe_aprobador_id: uuid.UUID | None
    fecha: date
    inicio_programado: datetime
    fin_programado: datetime
    horas_solicitadas: Decimal
    motivo: str
    estado: str
    motivo_rechazo: str | None
    comentario_jefe: str | None
    decision_en: datetime | None
    aprobacion_extemporanea: bool
    horas_reales: Decimal | None
    horas_reconocidas: Decimal | None

