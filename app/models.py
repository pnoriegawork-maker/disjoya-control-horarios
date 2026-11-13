"""Modelo ORM (espejo exacto del DDL `sql/001_schema.sql`)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

ROLES = ("TRABAJADOR", "JEFE_INMEDIATO", "NOMINA_RRHH", "AUDITOR_SUPERADMIN")
ESTADOS_HE = ("PENDIENTE", "APROBADA", "RECHAZADA_JEFE", "RECHAZADA_AUTOMATICA", "CONCILIADA", "ANULADA")
ESTADOS_HE_ACTIVOS = ("PENDIENTE", "APROBADA", "CONCILIADA")


class UTCDateTime(TypeDecorator):
    """timestamptz siempre en UTC, independientemente del dialecto."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Se requieren datetimes con zona horaria")
        value = value.astimezone(timezone.utc)
        return value if dialect.name == "postgresql" else value.replace(tzinfo=None)

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


IP = String(45).with_variant(INET(), "postgresql")
JSONDoc = JSON().with_variant(JSONB(), "postgresql")
BigId = BigInteger().with_variant(Integer(), "sqlite")


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class CentroTrabajo(Base):
    __tablename__ = "centros_trabajo"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    nombre: Mapped[str] = mapped_column(String(150))
    direccion: Mapped[str] = mapped_column(String(250))
    municipio_dane: Mapped[str] = mapped_column(String(5))
    latitud: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    longitud: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    radio_metros: Mapped[int] = mapped_column(Integer, default=50)
    poligono: Mapped[list | None] = mapped_column(JSONDoc, nullable=True)
    secreto_totp_cifrado: Mapped[bytes] = mapped_column(LargeBinary)
    kiosco_api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    creado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora)
    __table_args__ = (CheckConstraint("radio_metros BETWEEN 10 AND 1000", name="ck_centro_radio"),)


class Empleado(Base):
    __tablename__ = "empleados"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tipo_documento: Mapped[str] = mapped_column(String(3))
    numero_documento: Mapped[str] = mapped_column(String(20), unique=True)
    nombres: Mapped[str] = mapped_column(String(120))
    apellidos: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    telefono_whatsapp: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rol: Mapped[str] = mapped_column(String(20), default="TRABAJADOR")
    cargo: Mapped[str] = mapped_column(String(120))
    jefe_inmediato_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("empleados.id"), nullable=True)
    centro_trabajo_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("centros_trabajo.id"), nullable=True)
    fecha_nacimiento: Mapped[date | None] = mapped_column(Date, nullable=True)
    salario_base: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    salario_integral: Mapped[bool] = mapped_column(Boolean, default=False)
    es_direccion_confianza: Mapped[bool] = mapped_column(Boolean, default=False)
    password_hash: Mapped[str] = mapped_column(String(200))
    dispositivo_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dispositivo_registrado_en: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    creado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora)
    actualizado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora, onupdate=_ahora)

    jefe: Mapped["Empleado | None"] = relationship(remote_side="Empleado.id")
    centro: Mapped[CentroTrabajo | None] = relationship()
    __table_args__ = (
        CheckConstraint(
            "rol IN ('TRABAJADOR','JEFE_INMEDIATO','NOMINA_RRHH','AUDITOR_SUPERADMIN')", name="ck_empleado_rol"
        ),
        CheckConstraint("salario_base > 0", name="ck_empleado_salario"),
        CheckConstraint("jefe_inmediato_id IS NULL OR jefe_inmediato_id <> id", name="ck_empleado_no_autojefe"),
    )

    @property
    def nombre_completo(self) -> str:
        return f"{self.nombres} {self.apellidos}"


class ConsentimientoHabeasData(Base):
    __tablename__ = "consentimientos_habeas_data"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empleado_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("empleados.id"))
    version_politica: Mapped[str] = mapped_column(String(20))
    hash_texto_politica: Mapped[str] = mapped_column(String(64))
    acepta_tratamiento_laboral: Mapped[bool] = mapped_column(Boolean)
    acepta_geolocalizacion: Mapped[bool] = mapped_column(Boolean)
    otorgado_en: Mapped[datetime] = mapped_column(UTCDateTime)
    ip_origen: Mapped[str | None] = mapped_column(IP, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    revocado_en: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class TurnoProgramado(Base):
    __tablename__ = "turnos_programados"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empleado_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("empleados.id"))
    centro_trabajo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("centros_trabajo.id"))
    fecha: Mapped[date] = mapped_column(Date)
    inicio: Mapped[datetime] = mapped_column(UTCDateTime)
    fin: Mapped[datetime] = mapped_column(UTCDateTime)
    creado_por_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("empleados.id"))
    creado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora)
    __table_args__ = (
        CheckConstraint("fin > inicio", name="ck_turno_rango"),
        UniqueConstraint("empleado_id", "inicio", name="uq_turno_empleado_inicio"),
    )


class MarcacionQR(Base):
    __tablename__ = "marcaciones_qr"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empleado_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("empleados.id"))
    centro_trabajo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("centros_trabajo.id"))
    tipo: Mapped[str] = mapped_column(String(7))
    marca_tiempo: Mapped[datetime] = mapped_column(UTCDateTime)
    fecha_local: Mapped[date] = mapped_column(Date)
    ntp_sincronizado: Mapped[bool] = mapped_column(Boolean)
    ntp_desfase_ms: Mapped[int] = mapped_column(Integer)
    ventana_totp: Mapped[int] = mapped_column(BigInteger)
    coordenadas_cifradas: Mapped[bytes] = mapped_column(LargeBinary)
    distancia_metros: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    precision_gps_m: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    dispositivo_hash: Mapped[str] = mapped_column(String(64))
    ip_origen: Mapped[str | None] = mapped_column(IP, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sello_integridad: Mapped[str] = mapped_column(String(64))
    creado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora)
    __table_args__ = (
        CheckConstraint("tipo IN ('ENTRADA','SALIDA')", name="ck_marcacion_tipo"),
        UniqueConstraint("empleado_id", "centro_trabajo_id", "ventana_totp", name="uq_marcacion_antireplay"),
    )


class SolicitudHorasExtras(Base):
    __tablename__ = "solicitudes_horas_extras"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empleado_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("empleados.id"))
    solicitado_por_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("empleados.id"))
    jefe_aprobador_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("empleados.id"), nullable=True)
    fecha: Mapped[date] = mapped_column(Date)
    inicio_programado: Mapped[datetime] = mapped_column(UTCDateTime)
    fin_programado: Mapped[datetime] = mapped_column(UTCDateTime)
    horas_solicitadas: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    motivo: Mapped[str] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(String(22), default="PENDIENTE")
    motivo_rechazo: Mapped[str | None] = mapped_column(Text, nullable=True)
    comentario_jefe: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_en: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    aprobacion_extemporanea: Mapped[bool] = mapped_column(Boolean, default=False)
    horas_reales: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    horas_reconocidas: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    marcacion_salida_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("marcaciones_qr.id"), nullable=True)
    conciliado_en: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    creado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora)
    actualizado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora, onupdate=_ahora)

    empleado: Mapped[Empleado] = relationship(foreign_keys=[empleado_id])
    __table_args__ = (
        CheckConstraint("fin_programado > inicio_programado", name="ck_he_rango"),
        CheckConstraint("horas_solicitadas > 0 AND horas_solicitadas <= 2", name="ck_he_max_diario"),
        CheckConstraint(
            "estado IN ('PENDIENTE','APROBADA','RECHAZADA_JEFE','RECHAZADA_AUTOMATICA','CONCILIADA','ANULADA')",
            name="ck_he_estado",
        ),
        CheckConstraint("horas_reconocidas IS NULL OR horas_reconocidas <= horas_solicitadas", name="ck_he_reconocidas"),
    )


class NovedadJornada(Base):
    __tablename__ = "novedades_jornada"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empleado_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("empleados.id"))
    fecha: Mapped[date] = mapped_column(Date)
    tipo: Mapped[str] = mapped_column(String(40))
    minutos: Mapped[int] = mapped_column(Integer, default=0)
    detalle: Mapped[str] = mapped_column(Text)
    resuelta: Mapped[bool] = mapped_column(Boolean, default=False)
    creado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora)
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('LLEGADA_TARDE','SALIDA_ANTICIPADA','TIEMPO_SUPLEMENTARIO_NO_AUTORIZADO',"
            "'HE_APROBADA_NO_LABORADA','JORNADA_SIN_SALIDA','EXCESO_JORNADA_SEMANAL')",
            name="ck_novedad_tipo",
        ),
    )


class AuditoriaLog(Base):
    __tablename__ = "auditoria_logs"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ocurrido_en: Mapped[datetime] = mapped_column(UTCDateTime)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    actor_rol: Mapped[str] = mapped_column(String(20))
    accion: Mapped[str] = mapped_column(String(60))
    entidad: Mapped[str] = mapped_column(String(60))
    entidad_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_origen: Mapped[str | None] = mapped_column(IP, nullable=True)
    dispositivo_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    datos: Mapped[dict] = mapped_column(JSONDoc)
    hash_anterior: Mapped[str] = mapped_column(String(64))
    hash_actual: Mapped[str] = mapped_column(String(64), unique=True)

