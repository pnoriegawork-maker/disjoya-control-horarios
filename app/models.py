"""Modelo ORM (espejo exacto del DDL `sql/001_schema.sql` + tablas de Nómina Autopartes, Ledger HE, Novedades y Alertas 30m)."""
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
TIPOS_NOVEDAD_AUSENTISMO = (
    "VACACIONES",
    "INCAPACIDAD_EPS",
    "INCAPACIDAD_ARL",
    "LICENCIA_REMUNERADA",
    "LICENCIA_NO_REMUNERADA",
)
JEFE_OFICIAL_EMAIL = "tminero.sst@gmail.com"


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
    radio_metros: Mapped[int] = mapped_column(Integer, default=80)
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
        return f"{self.nombres} {self.apellidos}".strip()

    @property
    def es_vendedor(self) -> bool:
        cargo_up = (self.cargo or "").upper()
        return any(k in cargo_up for k in ("VENDEDOR", "VENTAS", "COMERCIAL", "ASESOR"))


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
        CheckConstraint("horas_solicitadas > 0 AND horas_solicitadas <= 24", name="ck_he_max_diario"),
        CheckConstraint(
            "estado IN ('PENDIENTE','APROBADA','RECHAZADA_JEFE','RECHAZADA_AUTOMATICA','CONCILIADA','ANULADA')",
            name="ck_he_estado",
        ),
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


class NovedadAusentismo(Base):
    """Gestión de Vacaciones (Art. 186 CST), Incapacidades EPS (origen común) e Incapacidades ARL (riesgos laborales).
    Bloquea automáticamente la exigencia de marcación QR y las alertas de 30 minutos durante su vigencia."""

    __tablename__ = "novedades_ausentismo"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empleado_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("empleados.id"))
    tipo_novedad: Mapped[str] = mapped_column(String(30))
    fecha_inicio: Mapped[date] = mapped_column(Date)
    fecha_fin: Mapped[date] = mapped_column(Date)
    dias_calendario: Mapped[int] = mapped_column(Integer)
    dias_habiles: Mapped[int] = mapped_column(Integer, default=0)
    porcentaje_pago_empresa: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("100.00"))
    porcentaje_reconocimiento_entidad: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.00"))
    entidad_responsable: Mapped[str] = mapped_column(String(120), default="EMPRESA_DISJOYA")
    codigo_cie10: Mapped[str | None] = mapped_column(String(15), nullable=True)
    soporte_ref: Mapped[str | None] = mapped_column(String(250), nullable=True)
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)
    exime_marcacion_qr: Mapped[bool] = mapped_column(Boolean, default=True)
    estado: Mapped[str] = mapped_column(String(15), default="ACTIVA")
    registrado_por_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("empleados.id"), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora)

    empleado: Mapped[Empleado] = relationship(foreign_keys=[empleado_id])
    __table_args__ = (
        CheckConstraint("fecha_fin >= fecha_inicio", name="ck_ausentismo_rango"),
        CheckConstraint("dias_calendario > 0", name="ck_ausentismo_dias"),
        CheckConstraint(
            "tipo_novedad IN ('VACACIONES','INCAPACIDAD_EPS','INCAPACIDAD_ARL','LICENCIA_REMUNERADA','LICENCIA_NO_REMUNERADA')",
            name="ck_ausentismo_tipo",
        ),
        CheckConstraint("estado IN ('ACTIVA','FINALIZADA','ANULADA')", name="ck_ausentismo_estado"),
    )


class AlertaInasistencia(Base):
    """Registro de alertas críticas disparadas tras 30 minutos de gracia sin marcación QR de Entrada o Salida."""

    __tablename__ = "alertas_inasistencia"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empleado_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("empleados.id"))
    turno_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("turnos_programados.id"), nullable=True)
    fecha: Mapped[date] = mapped_column(Date)
    tipo_omision: Mapped[str] = mapped_column(String(25))
    hora_programada: Mapped[datetime] = mapped_column(UTCDateTime)
    hora_deteccion: Mapped[datetime] = mapped_column(UTCDateTime)
    minutos_retraso: Mapped[int] = mapped_column(Integer)
    correo_trabajador_enviado: Mapped[bool] = mapped_column(Boolean, default=False)
    correo_jefe_enviado: Mapped[bool] = mapped_column(Boolean, default=False)
    jefe_email_notificado: Mapped[str] = mapped_column(String(200), default=JEFE_OFICIAL_EMAIL)
    resultado_despacho: Mapped[dict] = mapped_column(JSONDoc, default=dict)
    creado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora)

    empleado: Mapped[Empleado] = relationship(foreign_keys=[empleado_id])
    __table_args__ = (
        CheckConstraint(
            "tipo_omision IN ('ENTRADA_OMITIDA_30M','SALIDA_OMITIDA_30M')", name="ck_alerta_tipo_omision"
        ),
        CheckConstraint("minutos_retraso >= 30", name="ck_alerta_min_30m"),
        UniqueConstraint("empleado_id", "fecha", "tipo_omision", name="uq_alerta_empleado_fecha_tipo"),
    )


class HorasExtrasLedger(Base):
    """Sistema de Doble Contabilización y Libro Mayor de Arrastre Legal (Carry-Over Ledger):
    - Cuenta 1 (Nómina Mes Corriente): Liquida y paga hasta el tope legal mensual (Art. 22 Ley 50/1990).
    - Cuenta 2 (Banco de Arrastre Real): Acumula 1h diaria preautorizada L-V + horas aprobadas por jefe + saldo anterior,
      trasladando automáticamente cualquier excedente al mes inmediatamente siguiente sin pérdida para el trabajador."""

    __tablename__ = "horas_extras_ledger"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empleado_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("empleados.id"))
    periodo_anio: Mapped[int] = mapped_column(Integer)
    periodo_mes: Mapped[int] = mapped_column(Integer)
    dias_habiles_lv: Mapped[int] = mapped_column(Integer, default=19)
    horas_preautorizadas_1h_diaria: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    horas_adicionales_aprobadas_jefe: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    saldo_arrastre_mes_anterior: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    total_horas_banco_real: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    tope_legal_mensual_pagable: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("48.00"))
    horas_pagadas_nomina_mes: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    saldo_arrastre_siguiente_mes: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    detalle_horas_por_tipo: Mapped[dict] = mapped_column(JSONDoc, default=dict)
    valor_pagado_mes_cop: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    valor_arrastre_estimado_cop: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    aprobador_email: Mapped[str] = mapped_column(String(200), default=JEFE_OFICIAL_EMAIL)
    sello_auditoria_sha256: Mapped[str] = mapped_column(String(64))
    cerrado: Mapped[bool] = mapped_column(Boolean, default=False)
    creado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora)
    actualizado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora, onupdate=_ahora)

    empleado: Mapped[Empleado] = relationship(foreign_keys=[empleado_id])
    __table_args__ = (
        CheckConstraint("periodo_mes BETWEEN 1 AND 12", name="ck_ledger_mes"),
        CheckConstraint("total_horas_banco_real >= 0", name="ck_ledger_banco_pos"),
        CheckConstraint("horas_pagadas_nomina_mes >= 0", name="ck_ledger_pagadas_pos"),
        CheckConstraint("saldo_arrastre_siguiente_mes >= 0", name="ck_ledger_arrastre_pos"),
        UniqueConstraint("empleado_id", "periodo_anio", "periodo_mes", name="uq_ledger_empleado_periodo"),
    )


class ComisionVendedor(Base):
    """Módulo Exclusivo de Comisiones para Personal Comercial (Art. 127 CST).
    Constituye factor salarial pleno para IBC de Seguridad Social, Parafiscales, Prestaciones y Valor Hora."""

    __tablename__ = "comisiones_vendedores"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empleado_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("empleados.id"))
    periodo_anio: Mapped[int] = mapped_column(Integer)
    periodo_mes: Mapped[int] = mapped_column(Integer)
    comision_quincena_1: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    comision_quincena_2: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    total_comisiones_cop: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    constituye_salario_art127: Mapped[bool] = mapped_column(Boolean, default=True)
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)
    registrado_por_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("empleados.id"), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora)
    actualizado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora, onupdate=_ahora)

    empleado: Mapped[Empleado] = relationship(foreign_keys=[empleado_id])
    __table_args__ = (
        CheckConstraint("periodo_mes BETWEEN 1 AND 12", name="ck_comision_mes"),
        CheckConstraint("total_comisiones_cop >= 0", name="ck_comision_positiva"),
        UniqueConstraint("empleado_id", "periodo_anio", "periodo_mes", name="uq_comision_empleado_periodo"),
    )


class LiquidacionNomina(Base):
    """Liquidación Mensual Automatizada conforme al archivo maestro 'NOMINA DE AUTOPARTES para sr pedro'."""

    __tablename__ = "liquidaciones_nomina"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    empleado_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("empleados.id"))
    periodo_anio: Mapped[int] = mapped_column(Integer)
    periodo_mes: Mapped[int] = mapped_column(Integer)
    seccion_plantilla: Mapped[str] = mapped_column(String(40), default="PERSONAL ADMINISTRATIVO")
    salario_basico_pactado: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    comisiones_ventas_art127: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    sueldo_base_calculo: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    divisor_hora_mes: Mapped[int] = mapped_column(Integer, default=210)
    valor_hora_ordinaria: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    dias_laborados: Mapped[int] = mapped_column(Integer, default=30)
    dias_vacaciones: Mapped[int] = mapped_column(Integer, default=0)
    dias_incapacidad_eps: Mapped[int] = mapped_column(Integer, default=0)
    dias_incapacidad_arl: Mapped[int] = mapped_column(Integer, default=0)
    salario_basico_proporcional: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    auxilio_transporte_devengado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    pago_vacaciones: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    pago_incapacidad_eps: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    pago_incapacidad_arl: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    horas_recargo_nocturno: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    horas_extra_diurna: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    horas_extra_nocturna: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    horas_festiva_diurna: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    horas_festiva_nocturna: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    horas_extra_festiva_diurna: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    horas_extra_festiva_nocturna: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    total_horas_extras_cop: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    horas_arrastradas_siguiente_mes: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"))
    total_devengado: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    ibc_seguridad_social: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    deduccion_salud_4pct: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    deduccion_pension_4pct: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    fondo_solidaridad_pensional: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    otras_deducciones_libranzas: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    total_deducido: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    neto_a_pagar: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    aporte_salud_empleador: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    aporte_pension_empleador: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    aporte_arl_empleador: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    aporte_caja_compensacion: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    aporte_sena: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    aporte_icbf: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    provision_cesantias: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    provision_intereses_cesantias: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    provision_prima_servicios: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    provision_vacaciones: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    pdf_ruta_archivo: Mapped[str | None] = mapped_column(String(300), nullable=True)
    pdf_hash_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email_enviado: Mapped[bool] = mapped_column(Boolean, default=False)
    email_enviado_en: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    creado_en: Mapped[datetime] = mapped_column(UTCDateTime, default=_ahora)

    empleado: Mapped[Empleado] = relationship(foreign_keys=[empleado_id])
    __table_args__ = (
        CheckConstraint("periodo_mes BETWEEN 1 AND 12", name="ck_liq_mes"),
        CheckConstraint("dias_laborados BETWEEN 0 AND 30", name="ck_liq_dias"),
        UniqueConstraint("empleado_id", "periodo_anio", "periodo_mes", name="uq_liquidacion_empleado_periodo"),
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
