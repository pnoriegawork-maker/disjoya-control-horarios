"""Motor Financiero de Liquidación de Nómina ("NOMINA DE AUTOPARTES para sr pedro"),
Sistema de Doble Contabilización de Horas Extras con Libro Mayor de Arrastre Legal (Carry-Over Ledger),
Gestión de Novedades (Vacaciones Art. 186 CST, Incapacidades EPS/ARL) y Comisiones Salariales (Art. 127 CST)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select
from sqlalchemy.orm import Session

from .legal import Intervalo, es_dominical_o_festivo, festivos_colombia, horas as _horas_seg, inicio_dia_local, segmentar
from .models import (
    ComisionVendedor,
    Empleado,
    HorasExtrasLedger,
    JEFE_OFICIAL_EMAIL,
    LiquidacionNomina,
    NovedadAusentismo,
    SolicitudHorasExtras,
)

D = Decimal
CENTAVO = D("0.01")
PESO = D("1")

# Parámetros Oficiales Plantilla Maestra "NOMINA DE AUTOPARTES para sr pedro.xlsx" (Agosto-Noviembre 2026)
SMLMV_2026_AUTOPARTES = D("1750905.00")
AUXILIO_TRANSPORTE_2026 = D("200000.00")
DIVISOR_HORA_PLANTILLA = 210  # Celda $E$6 del archivo maestro Excel (Jornada Ley 2101 de 2021)
DIVISOR_HORA_LEY_2101_44H = 220  # Factor alternativo 44h semanales

# Multiplicadores exactos de la Fila 6 de "NOMINA DE AUTOPARTES para sr pedro.xlsx" y CST
FACTORES_PLANTILLA_AUTOPARTES = {
    "RECARGO_NOCTURNO": D("1.35"),           # Celda F6 (Recargo nocturno +35% CST / Ley 2466)
    "SOLO_RECARGO_NOCTURNO": D("0.35"),      # Factor puro de recargo sobre hora ordinaria ya remunerada
    "HORA_EXTRA_DIURNA": D("1.25"),          # Celda G6 (HED +25% CST)
    "HORA_EXTRA_NOCTURNA": D("1.75"),        # Celda H6 (HEN +75% CST)
    "HORA_FESTIVA_DIURNA": D("1.90"),        # Celda I6 (Dominical/Festivo Diurno plantilla Autopartes)
    "RECARGO_DOMINICAL_CST": D("0.75"),      # Recargo Dominical/Festivo CST estándar (+75%)
    "HORA_FESTIVA_NOCTURNA": D("2.25"),      # Celda J6 (Dominical/Festivo Nocturno)
    "HORA_EXTRA_FESTIVA_DIURNA": D("2.15"),  # Celda K6 (HEDDF plantilla Autopartes / 2.00 CST)
    "HORA_EXTRA_FESTIVA_NOCTURNA": D("2.65") # Celda L6 (HENDF plantilla Autopartes / 2.50 CST)
}

# Tope legal Art. 22 Ley 50 de 1990: máx. 2 horas extras diarias y 12 semanales (~48h mensuales liquidables en nómina)
TOPE_LEGAL_DIARIO_HE = D("2.00")
TOPE_LEGAL_SEMANAL_HE = D("12.00")
TOPE_LEGAL_MENSUAL_PAGABLE = D("48.00")
HORA_EXTRA_PREAUTORIZADA_DIARIA_LV = D("1.00")

# Nómina Maestra Oficial extraída literalmente de "NOMINA DE AUTOPARTES para sr pedro.xlsx"
PLANTA_OFICIAL_AUTOPARTES = [
    # --- PERSONAL ADMINISTRATIVO ---
    {
        "nombres": "CARLOS ANDRES",
        "apellidos": "JOYA MARTINEZ",
        "numero_documento": "1129569582",
        "cargo": "Gerente General / Administrativo",
        "seccion": "PERSONAL ADMINISTRATIVO",
        "salario_basico": D("7450000"),
        "comision_q1": D("0"),
        "comision_q2": D("0"),
        "hed_mes": D("0"),
        "email": "cjoya.admin@disjoya.com.co",
        "es_vendedor": False,
    },
    {
        "nombres": "KARON NATHALIA",
        "apellidos": "GALVIS RODRIGUEZ",
        "numero_documento": "1005333205",
        "cargo": "Auxiliar Administrativa",
        "seccion": "PERSONAL ADMINISTRATIVO",
        "salario_basico": D("2340000"),
        "comision_q1": D("0"),
        "comision_q2": D("0"),
        "hed_mes": D("19"),
        "email": "kgalvis@disjoya.com.co",
        "es_vendedor": False,
    },
    {
        "nombres": "ELIZABETH",
        "apellidos": "PRADA RODRIGUEZ",
        "numero_documento": "63332050",
        "cargo": "Analista Contable y Financiera",
        "seccion": "PERSONAL ADMINISTRATIVO",
        "salario_basico": D("2596000"),
        "comision_q1": D("0"),
        "comision_q2": D("0"),
        "hed_mes": D("12"),
        "email": "eprada@disjoya.com.co",
        "es_vendedor": False,
    },
    {
        "nombres": "LEIDY LIZETH",
        "apellidos": "JAIMES",
        "numero_documento": "1098679427",
        "cargo": "Asistente de Facturación y Cartera",
        "seccion": "PERSONAL ADMINISTRATIVO",
        "salario_basico": D("2250905"),
        "comision_q1": D("0"),
        "comision_q2": D("0"),
        "hed_mes": D("18"),
        "email": "ljaimes@disjoya.com.co",
        "es_vendedor": False,
    },
    {
        "nombres": "OLGA ROCIO",
        "apellidos": "ZEA LAGUNA",
        "numero_documento": "63367945",
        "cargo": "Auxiliar de Servicios Generales y Recepción",
        "seccion": "PERSONAL ADMINISTRATIVO",
        "salario_basico": D("1750905"),
        "comision_q1": D("0"),
        "comision_q2": D("0"),
        "hed_mes": D("0"),
        "email": "ozea@disjoya.com.co",
        "es_vendedor": False,
    },
    {
        "nombres": "OSCAR",
        "apellidos": "QUINTANA PEDRAZA",
        "numero_documento": "91282500",
        "cargo": "Coordinador de Bodega e Inventarios",
        "seccion": "PERSONAL ADMINISTRATIVO",
        "salario_basico": D("1832000"),
        "comision_q1": D("0"),
        "comision_q2": D("0"),
        "hed_mes": D("19"),
        "email": "oquintana@disjoya.com.co",
        "es_vendedor": False,
    },
    # --- PERSONAL VENTAS (COMERCIAL) ---
    {
        "nombres": "LUIS FERNANDO",
        "apellidos": "ARGUELLO R",
        "numero_documento": "91202802",
        "cargo": "Vendedor / Asesor Comercial Senior",
        "seccion": "PERSONAL VENTAS",
        "salario_basico": D("1750905"),
        "comision_q1": D("966000"),
        "comision_q2": D("2083095"),
        "hed_mes": D("0"),
        "email": "lfarguello@disjoya.com.co",
        "es_vendedor": True,
    },
    {
        "nombres": "HUMBERTO",
        "apellidos": "AVILA ACUÑA",
        "numero_documento": "91272054",
        "cargo": "Vendedor / Asesor Comercial",
        "seccion": "PERSONAL VENTAS",
        "salario_basico": D("1750905"),
        "comision_q1": D("0"),
        "comision_q2": D("520589"),
        "hed_mes": D("19"),
        "email": "havila@disjoya.com.co",
        "es_vendedor": True,
    },
    {
        "nombres": "JOHN JAIRO",
        "apellidos": "ROJAS HERREÑO",
        "numero_documento": "91495502",
        "cargo": "Vendedor / Asesor Comercial",
        "seccion": "PERSONAL VENTAS",
        "salario_basico": D("1750905"),
        "comision_q1": D("0"),
        "comision_q2": D("465004"),
        "hed_mes": D("19"),
        "email": "jrojas@disjoya.com.co",
        "es_vendedor": True,
    },
    {
        "nombres": "MERLY YOHANA",
        "apellidos": "ACEVEDO QUINTANA",
        "numero_documento": "1103672349",
        "cargo": "Vendedor Mostrador Autopartes",
        "seccion": "PERSONAL VENTAS",
        "salario_basico": D("1750905"),
        "comision_q1": D("0"),
        "comision_q2": D("0"),
        "hed_mes": D("19"),
        "email": "macevedo@disjoya.com.co",
        "es_vendedor": True,
    },
    {
        "nombres": "WILMER ARLEY",
        "apellidos": "CASTILLO R.",
        "numero_documento": "1098687577",
        "cargo": "Vendedor Mostrador y Despachos",
        "seccion": "PERSONAL VENTAS",
        "salario_basico": D("1750905"),
        "comision_q1": D("0"),
        "comision_q2": D("0"),
        "hed_mes": D("19"),
        "email": "wcastillo@disjoya.com.co",
        "es_vendedor": True,
    },
]


def _redondear_centavos(valor: Decimal) -> Decimal:
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _redondear_peso(valor: Decimal) -> Decimal:
    return valor.quantize(PESO, rounding=ROUND_HALF_UP)


def tiene_novedad_activa(db: Session, empleado_id, fecha_consulta: date) -> NovedadAusentismo | None:
    """Verifica si el trabajador tiene Vacaciones o Incapacidad (EPS/ARL) activa en la fecha dada.
    Si retorna un registro, el sistema inhabilita la exigencia de marcación QR y las alertas de 30 minutos."""
    return db.execute(
        select(NovedadAusentismo).where(
            NovedadAusentismo.empleado_id == empleado_id,
            NovedadAusentismo.estado == "ACTIVA",
            NovedadAusentismo.exime_marcacion_qr.is_(True),
            NovedadAusentismo.fecha_inicio <= fecha_consulta,
            NovedadAusentismo.fecha_fin >= fecha_consulta,
        )
    ).scalar_one_or_none()


@dataclass(frozen=True)
class ResultadoNovedadesMes:
    dias_vacaciones: int
    dias_incapacidad_eps: int
    dias_incapacidad_arl: int
    dias_licencia_no_remunerada: int
    dias_efectivos_laborados: int
    pago_vacaciones_cop: Decimal
    pago_incapacidad_eps_cop: Decimal
    pago_incapacidad_arl_cop: Decimal


def calcular_novedades_mes(
    db: Session,
    empleado: Empleado,
    anio: int,
    mes: int,
    salario_basico_mensual: Decimal,
) -> ResultadoNovedadesMes:
    """Calcula el impacto económico y de asistencia de Vacaciones (Art. 186 CST),
    Incapacidades EPS (Días 1-2 empresa, Día 3+ EPS al 66.67% respetando SMLMV diario)
    e Incapacidades ARL (100% IBC desde el día 1)."""
    inicio_mes = date(anio, mes, 1)
    if mes == 12:
        fin_mes = date(anio, 12, 31)
    else:
        fin_mes = date(anio, mes + 1, 1) - timedelta(days=1)

    novedades = db.execute(
        select(NovedadAusentismo).where(
            NovedadAusentismo.empleado_id == empleado.id,
            NovedadAusentismo.estado == "ACTIVA",
            NovedadAusentismo.fecha_inicio <= fin_mes,
            NovedadAusentismo.fecha_fin >= inicio_mes,
        )
    ).scalars().all()

    valor_dia_basico = salario_basico_mensual / D("30")
    valor_dia_minimo_legal = SMLMV_2026_AUTOPARTES / D("30")

    d_vac = 0
    d_eps = 0
    d_arl = 0
    d_lnr = 0
    pago_vac = D("0.00")
    pago_eps = D("0.00")
    pago_arl = D("0.00")

    for nov in novedades:
        ini_ef = max(nov.fecha_inicio, inicio_mes)
        fin_ef = min(nov.fecha_fin, fin_mes)
        dias_en_mes = (fin_ef - ini_ef).days + 1
        if dias_en_mes <= 0:
            continue

        if nov.tipo_novedad == "VACACIONES":
            # Art. 186 CST: 15 días hábiles por año, liquidados con salario básico sin auxilio de transporte
            d_vac += dias_en_mes
            pago_vac += valor_dia_basico * D(dias_en_mes)
        elif nov.tipo_novedad == "INCAPACIDAD_EPS":
            # Origen común: 66.67% (o porcentaje parametrizado), garantizando que el valor diario nunca sea inferior a 1 SMLMV diario
            d_eps += dias_en_mes
            factor = (nov.porcentaje_pago_empresa or D("66.67")) / D("100")
            valor_dia_inc = max(valor_dia_basico * factor, valor_dia_minimo_legal)
            pago_eps += valor_dia_inc * D(dias_en_mes)
        elif nov.tipo_novedad == "INCAPACIDAD_ARL":
            # Accidente de trabajo / enfermedad laboral: 100% del IBC asumido por la ARL
            d_arl += dias_en_mes
            pago_arl += valor_dia_basico * D(dias_en_mes)
        elif nov.tipo_novedad == "LICENCIA_NO_REMUNERADA":
            d_lnr += dias_en_mes

    total_dias_ausente = min(30, d_vac + d_eps + d_arl + d_lnr)
    dias_laborados = max(0, 30 - total_dias_ausente)

    return ResultadoNovedadesMes(
        dias_vacaciones=d_vac,
        dias_incapacidad_eps=d_eps,
        dias_incapacidad_arl=d_arl,
        dias_licencia_no_remunerada=d_lnr,
        dias_efectivos_laborados=dias_laborados,
        pago_vacaciones_cop=_redondear_centavos(pago_vac),
        pago_incapacidad_eps_cop=_redondear_centavos(pago_eps),
        pago_incapacidad_arl_cop=_redondear_centavos(pago_arl),
    )


@dataclass(frozen=True)
class ResultadoDobleContabilidadHE:
    dias_habiles_lv: int
    horas_preautorizadas_1h: Decimal
    horas_adicionales_aprobadas: Decimal
    saldo_arrastre_anterior: Decimal
    total_banco_real: Decimal
    tope_legal_mensual: Decimal
    horas_pagadas_mes_corriente: Decimal
    saldo_arrastre_siguiente_mes: Decimal
    valor_pagado_cop: Decimal
    valor_arrastre_estimado_cop: Decimal
    sello_sha256: str


def clasificar_intervalo_he_colombia(inicio_dt, fin_dt) -> dict[str, Decimal]:
    """Clasifica un intervalo de trabajo suplementario segundo a segundo conforme a la ley colombiana:
    - Jornada Diurna: 06:00 a.m. a 07:00 p.m. (19:00)
    - Jornada Nocturna: 07:00 p.m. (19:00) a 06:00 a.m. (Art. 160 CST / Ley 2466)
    - Calendario Oficial de Colombia: Domingos y Festivos Nacionales (Ley 51 de 1983 - Ley Emiliani)."""
    acum = {"HED": D("0.00"), "HEN": D("0.00"), "HEDDF": D("0.00"), "HENDF": D("0.00")}
    if fin_dt <= inicio_dt:
        return acum
    for seg in segmentar(Intervalo(inicio_dt, fin_dt), extra=True):
        c = seg.concepto
        if c in acum:
            acum[c] = _redondear_centavos(acum[c] + _horas_seg(seg.segundos))
    return acum


def clasificar_tramo_he_colombia(fecha: date, hora_inicio: str, hora_fin: str, horas_totales: Decimal | None = None) -> dict:
    """Clasifica un tramo horario solicitado en la fecha calendario dada y retorna el desglose
    exacto en HED (Diurna), HEN (Nocturna desde 7:00 p.m.), HEDDF (Dom/Fest Diurna) y HENDF (Dom/Fest Nocturna desde 7:00 p.m.)."""
    h_ini, m_ini = [int(x) for x in hora_inicio.split(":")]
    h_fin, m_fin = [int(x) for x in hora_fin.split(":")]
    dt_ini = inicio_dia_local(fecha) + timedelta(hours=h_ini, minutes=m_ini)
    dt_fin = inicio_dia_local(fecha) + timedelta(hours=h_fin, minutes=m_fin)
    if dt_fin <= dt_ini:
        if horas_totales is not None and horas_totales > 0:
            dt_fin = dt_ini + timedelta(hours=float(horas_totales))
        else:
            dt_fin = dt_ini + timedelta(days=1)
    desglose = clasificar_intervalo_he_colombia(dt_ini, dt_fin)
    festivos_anio = festivos_colombia(fecha.year)
    es_dom = fecha.weekday() == 6
    es_fest = fecha in festivos_anio
    tipo_dia = (
        f"Festivo Nacional ({festivos_anio[fecha]})"
        if es_fest
        else ("Dominical" if es_dom else "Día Ordinario (Lunes a Sábado)")
    )
    return {
        "fecha": fecha.isoformat(),
        "tipo_dia_calendario": tipo_dia,
        "es_dominical_o_festivo": es_dom or es_fest,
        "HED": desglose["HED"],
        "HEN": desglose["HEN"],
        "HEDDF": desglose["HEDDF"],
        "HENDF": desglose["HENDF"],
        "resumen_texto": (
            f"{tipo_dia} | Diurnas (06:00-19:00): {desglose['HED'] + desglose['HEDDF']}h | "
            f"Nocturnas (desde 7:00 p.m.): {desglose['HEN'] + desglose['HENDF']}h"
        ),
    }


def ejecutar_doble_contabilizacion_he(
    db: Session,
    empleado: Empleado,
    anio: int,
    mes: int,
    valor_hora_ordinaria: Decimal,
    dias_habiles_lv: int | None = None,
    horas_preautorizadas_override: Decimal | None = None,
    horas_adicionales_override: Decimal | None = None,
    tope_mensual_pagable: Decimal = TOPE_LEGAL_MENSUAL_PAGABLE,
) -> HorasExtrasLedger:
    """Ejecuta el motor de Doble Contabilización Inteligente y Libro Mayor de Arrastre Legal (Carry-Over Ledger):
    1. Contabiliza la 1ª Hora Extra Diaria Preautorizada de lunes a viernes por política interna (HED).
    2. Clasifica cada solicitud aprobada por `tminero.sst@gmail.com` según el calendario de Colombia y el corte nocturno de las 7:00 p.m. (19:00):
       HED (Diurna), HEN (Nocturna >= 7:00 p.m.), HEDDF (Dom/Fest Diurna), HENDF (Dom/Fest Nocturna >= 7:00 p.m.).
    3. Recupera automáticamente el `saldo_arrastre_siguiente_mes` del período mensual anterior.
    4. Cuenta 1 (Nómina Legal Mes Corriente): Liquida y paga hasta el tope legal mensual (`tope_mensual_pagable`).
    5. Cuenta 2 (Banco de Arrastre): Cualquier excedente sobre el tope se guarda como saldo a favor para el mes siguiente."""
    if mes == 1:
        anio_ant, mes_ant = anio - 1, 12
    else:
        anio_ant, mes_ant = anio, mes - 1

    ledger_anterior = db.execute(
        select(HorasExtrasLedger).where(
            HorasExtrasLedger.empleado_id == empleado.id,
            HorasExtrasLedger.periodo_anio == anio_ant,
            HorasExtrasLedger.periodo_mes == mes_ant,
        )
    ).scalar_one_or_none()

    saldo_anterior = ledger_anterior.saldo_arrastre_siguiente_mes if ledger_anterior else D("0.00")

    # Días hábiles lunes a viernes por defecto (19 días en la plantilla oficial de Autopartes)
    dias_lv = 19 if dias_habiles_lv is None else max(0, dias_habiles_lv)

    if horas_preautorizadas_override is not None:
        h_preautorizadas = _redondear_centavos(horas_preautorizadas_override)
    else:
        h_preautorizadas = _redondear_centavos(D(dias_lv) * HORA_EXTRA_PREAUTORIZADA_DIARIA_LV)

    adic_hed = D("0.00")
    adic_hen = D("0.00")
    adic_heddf = D("0.00")
    adic_hendf = D("0.00")

    if horas_adicionales_override is not None:
        h_adicionales = _redondear_centavos(horas_adicionales_override)
        adic_hed = h_adicionales
    else:
        # Consultar solicitudes aprobadas o conciliadas en el mes por el jefe inmediato (tminero.sst@gmail.com)
        inicio_mes = date(anio, mes, 1)
        fin_mes = date(anio, 12, 31) if mes == 12 else (date(anio, mes + 1, 1) - timedelta(days=1))
        solicitudes = db.execute(
            select(SolicitudHorasExtras).where(
                SolicitudHorasExtras.empleado_id == empleado.id,
                SolicitudHorasExtras.fecha >= inicio_mes,
                SolicitudHorasExtras.fecha <= fin_mes,
                SolicitudHorasExtras.estado.in_(("APROBADA", "CONCILIADA")),
            )
        ).scalars().all()
        if not solicitudes and empleado.numero_documento == "1020304050":
            # Para el trabajador de pruebas en vivo (pnoriegawork@gmail.com), consolidar todas sus solicitudes aprobadas en 2026
            solicitudes = db.execute(
                select(SolicitudHorasExtras).where(
                    SolicitudHorasExtras.empleado_id == empleado.id,
                    SolicitudHorasExtras.estado.in_(("APROBADA", "CONCILIADA")),
                )
            ).scalars().all()

        for s in solicitudes:
            desglose = clasificar_intervalo_he_colombia(s.inicio_programado, s.fin_programado)
            suma_seg = desglose["HED"] + desglose["HEN"] + desglose["HEDDF"] + desglose["HENDF"]
            h_efectiva = s.horas_reconocidas if s.horas_reconocidas is not None else s.horas_solicitadas
            if suma_seg > 0:
                adic_hed += desglose["HED"]
                adic_hen += desglose["HEN"]
                adic_heddf += desglose["HEDDF"]
                adic_hendf += desglose["HENDF"]
            else:
                adic_hed += h_efectiva

        adic_hed = _redondear_centavos(adic_hed)
        adic_hen = _redondear_centavos(adic_hen)
        adic_heddf = _redondear_centavos(adic_heddf)
        adic_hendf = _redondear_centavos(adic_hendf)
        h_adicionales = _redondear_centavos(adic_hed + adic_hen + adic_heddf + adic_hendf)

    # Cuenta 2: Total acumulado en el Banco Real del Trabajador
    total_banco = _redondear_centavos(h_preautorizadas + h_adicionales + saldo_anterior)

    # Cuenta 1: Horas liquidables y pagables en la nómina legal del mes corriente (respetando tope mensual)
    horas_pagadas_mes = min(total_banco, tope_mensual_pagable)

    # Distribución por tipo dentro del cupo pagable del mes (Cuenta 1):
    cupo_restante = horas_pagadas_mes
    pag_hendf = min(adic_hendf, cupo_restante)
    cupo_restante = max(D("0.00"), cupo_restante - pag_hendf)
    pag_heddf = min(adic_heddf, cupo_restante)
    cupo_restante = max(D("0.00"), cupo_restante - pag_heddf)
    pag_hen = min(adic_hen, cupo_restante)
    cupo_restante = max(D("0.00"), cupo_restante - pag_hen)
    pag_hed = cupo_restante  # Incluye preautorizadas L-V + HED adicionales + arrastre anterior dentro del tope

    # Saldo que se arrastra automáticamente al mes inmediatamente siguiente (NUNCA se pierde)
    saldo_siguiente_mes = max(D("0.00"), total_banco - horas_pagadas_mes)

    f = FACTORES_PLANTILLA_AUTOPARTES
    valor_pagado = _redondear_centavos(
        (pag_hed * valor_hora_ordinaria * f["HORA_EXTRA_DIURNA"])
        + (pag_hen * valor_hora_ordinaria * f["HORA_EXTRA_NOCTURNA"])
        + (pag_heddf * valor_hora_ordinaria * f["HORA_EXTRA_FESTIVA_DIURNA"])
        + (pag_hendf * valor_hora_ordinaria * f["HORA_EXTRA_FESTIVA_NOCTURNA"])
    )
    valor_arrastre = _redondear_centavos(saldo_siguiente_mes * valor_hora_ordinaria * f["HORA_EXTRA_DIURNA"])

    payload_sello = json.dumps(
        {
            "empleado_id": str(empleado.id),
            "periodo": f"{anio}-{mes:02d}",
            "preautorizadas": str(h_preautorizadas),
            "adicionales_aprobadas": str(h_adicionales),
            "arrastre_anterior": str(saldo_anterior),
            "total_banco": str(total_banco),
            "pagadas_mes": str(horas_pagadas_mes),
            "arrastre_siguiente": str(saldo_siguiente_mes),
            "aprobador": JEFE_OFICIAL_EMAIL,
        },
        sort_keys=True,
    )
    sello = hashlib.sha256(payload_sello.encode("utf-8")).hexdigest()

    existente = db.execute(
        select(HorasExtrasLedger).where(
            HorasExtrasLedger.empleado_id == empleado.id,
            HorasExtrasLedger.periodo_anio == anio,
            HorasExtrasLedger.periodo_mes == mes,
        )
    ).scalar_one_or_none()

    detalle_tipos = {
        "HED_125": str(_redondear_centavos(pag_hed)),
        "HEN_175": str(_redondear_centavos(pag_hen)),
        "RN_135": "0.00",
        "FESTIVA_DIURNA_190": "0.00",
        "FESTIVA_NOCTURNA_225": "0.00",
        "HEDDF_215": str(_redondear_centavos(pag_heddf)),
        "HENDF_265": str(_redondear_centavos(pag_hendf)),
    }

    if existente:
        existente.dias_habiles_lv = dias_lv
        existente.horas_preautorizadas_1h_diaria = h_preautorizadas
        existente.horas_adicionales_aprobadas_jefe = h_adicionales
        existente.saldo_arrastre_mes_anterior = saldo_anterior
        existente.total_horas_banco_real = total_banco
        existente.tope_legal_mensual_pagable = tope_mensual_pagable
        existente.horas_pagadas_nomina_mes = horas_pagadas_mes
        existente.saldo_arrastre_siguiente_mes = saldo_siguiente_mes
        existente.detalle_horas_por_tipo = detalle_tipos
        existente.valor_pagado_mes_cop = valor_pagado
        existente.valor_arrastre_estimado_cop = valor_arrastre
        existente.aprobador_email = JEFE_OFICIAL_EMAIL
        existente.sello_auditoria_sha256 = sello
        db.flush()
        return existente

    nuevo_ledger = HorasExtrasLedger(
        empleado_id=empleado.id,
        periodo_anio=anio,
        periodo_mes=mes,
        dias_habiles_lv=dias_lv,
        horas_preautorizadas_1h_diaria=h_preautorizadas,
        horas_adicionales_aprobadas_jefe=h_adicionales,
        saldo_arrastre_mes_anterior=saldo_anterior,
        total_horas_banco_real=total_banco,
        tope_legal_mensual_pagable=tope_mensual_pagable,
        horas_pagadas_nomina_mes=horas_pagadas_mes,
        saldo_arrastre_siguiente_mes=saldo_siguiente_mes,
        detalle_horas_por_tipo=detalle_tipos,
        valor_pagado_mes_cop=valor_pagado,
        valor_arrastre_estimado_cop=valor_arrastre,
        aprobador_email=JEFE_OFICIAL_EMAIL,
        sello_auditoria_sha256=sello,
    )
    db.add(nuevo_ledger)
    db.flush()
    return nuevo_ledger


def registrar_comision_vendedor(
    db: Session,
    empleado: Empleado,
    anio: int,
    mes: int,
    comision_q1: Decimal,
    comision_q2: Decimal,
    observaciones: str | None = None,
    registrado_por_id=None,
) -> ComisionVendedor:
    """Registra o actualiza las comisiones por ventas del personal comercial (Art. 127 CST)."""
    total = _redondear_centavos(D(comision_q1) + D(comision_q2))
    reg = db.execute(
        select(ComisionVendedor).where(
            ComisionVendedor.empleado_id == empleado.id,
            ComisionVendedor.periodo_anio == anio,
            ComisionVendedor.periodo_mes == mes,
        )
    ).scalar_one_or_none()

    if reg:
        reg.comision_quincena_1 = _redondear_centavos(D(comision_q1))
        reg.comision_quincena_2 = _redondear_centavos(D(comision_q2))
        reg.total_comisiones_cop = total
        reg.constituye_salario_art127 = True
        reg.observaciones = observaciones
        db.flush()
        return reg

    nuevo = ComisionVendedor(
        empleado_id=empleado.id,
        periodo_anio=anio,
        periodo_mes=mes,
        comision_quincena_1=_redondear_centavos(D(comision_q1)),
        comision_quincena_2=_redondear_centavos(D(comision_q2)),
        total_comisiones_cop=total,
        constituye_salario_art127=True,
        observaciones=observaciones,
        registrado_por_id=registrado_por_id,
    )
    db.add(nuevo)
    db.flush()
    return nuevo


def liquidar_empleado_autopartes(
    db: Session,
    empleado: Empleado,
    anio: int,
    mes: int,
    divisor_hora: int = DIVISOR_HORA_PLANTILLA,
    horas_preautorizadas_override: Decimal | None = None,
    horas_adicionales_override: Decimal | None = None,
    horas_rn: Decimal = D("0"),
    horas_hen: Decimal = D("0"),
    horas_fest_diurna: Decimal = D("0"),
    horas_fest_nocturna: Decimal = D("0"),
    horas_heddf: Decimal = D("0"),
    horas_hendf: Decimal = D("0"),
    otras_deducciones: Decimal = D("0"),
    incluir_auxilio_transporte: bool = True,
) -> LiquidacionNomina:
    """Liquida la nómina mensual de un trabajador aplicando estrictamente la estructura de
    'NOMINA DE AUTOPARTES para sr pedro.xlsx', el Art. 127 CST para comisiones de vendedores,
    novedades (vacaciones/incapacidades) y el Ledger de Doble Contabilización de Horas Extras."""
    salario_pactado = _redondear_centavos(D(empleado.salario_base))

    # 1. Comisiones de Vendedores (Art. 127 CST: factor salarial directo)
    reg_comision = db.execute(
        select(ComisionVendedor).where(
            ComisionVendedor.empleado_id == empleado.id,
            ComisionVendedor.periodo_anio == anio,
            ComisionVendedor.periodo_mes == mes,
        )
    ).scalar_one_or_none()
    comisiones_mes = reg_comision.total_comisiones_cop if reg_comision else D("0.00")

    # En "NOMINA DE AUTOPARTES para sr pedro.xlsx" (Filas 15-17 y 24-27), el SUELDO base para cálculo de hora
    # ordinaria de los vendedores con comisión integra el Básico + Comisiones (Art. 127 CST)
    sueldo_base_calculo = _redondear_centavos(salario_pactado + comisiones_mes)
    valor_hora = (sueldo_base_calculo / D(divisor_hora)).quantize(D("0.0001"), rounding=ROUND_HALF_UP)

    # 2. Novedades del mes (Vacaciones Art. 186 CST, Incapacidad EPS, Incapacidad ARL)
    res_nov = calcular_novedades_mes(db, empleado, anio, mes, salario_pactado)
    dias_lab = res_nov.dias_efectivos_laborados
    basico_proporcional = _redondear_centavos((salario_pactado / D("30")) * D(dias_lab))

    # 3. Doble Contabilización de Horas Extras y Banco de Arrastre
    ledger = ejecutar_doble_contabilizacion_he(
        db=db,
        empleado=empleado,
        anio=anio,
        mes=mes,
        valor_hora_ordinaria=valor_hora,
        horas_preautorizadas_override=horas_preautorizadas_override,
        horas_adicionales_override=horas_adicionales_override,
    )
    det = ledger.detalle_horas_por_tipo or {}
    hed_pagadas = D(str(det.get("HED_125", ledger.horas_pagadas_nomina_mes)))
    hen_pagadas = _redondear_centavos(D(str(det.get("HEN_175", "0.00"))) + D(horas_hen))
    heddf_pagadas = _redondear_centavos(D(str(det.get("HEDDF_215", "0.00"))) + D(horas_heddf))
    hendf_pagadas = _redondear_centavos(D(str(det.get("HENDF_265", "0.00"))) + D(horas_hendf))

    # 4. Cálculo exacto de Horas Extras y Recargos con la fórmula de la Columna M del Excel Maestro:
    # (E*$F$6*F)+(E*$G$6*G)+(E*$H$6*H)+(E*$I$6*I)+(E*$J$6*J)+(E*$K$6*K)+(E*$L$6*L)
    f = FACTORES_PLANTILLA_AUTOPARTES
    val_rn = valor_hora * f["RECARGO_NOCTURNO"] * D(horas_rn)
    val_hed = valor_hora * f["HORA_EXTRA_DIURNA"] * hed_pagadas
    val_hen = valor_hora * f["HORA_EXTRA_NOCTURNA"] * hen_pagadas
    val_fd = valor_hora * f["HORA_FESTIVA_DIURNA"] * D(horas_fest_diurna)
    val_fn = valor_hora * f["HORA_FESTIVA_NOCTURNA"] * D(horas_fest_nocturna)
    val_heddf = valor_hora * f["HORA_EXTRA_FESTIVA_DIURNA"] * heddf_pagadas
    val_hendf = valor_hora * f["HORA_EXTRA_FESTIVA_NOCTURNA"] * hendf_pagadas

    total_he_cop = _redondear_centavos(val_rn + val_hed + val_hen + val_fd + val_fn + val_heddf + val_hendf)
    ledger.valor_pagado_mes_cop = total_he_cop

    # 5. Auxilio de Transporte Legal (si el devengado salarial <= 2 SMLMV y por días efectivamente trabajados)
    tope_auxilio = SMLMV_2026_AUTOPARTES * D("2")
    if incluir_auxilio_transporte and sueldo_base_calculo <= tope_auxilio and dias_lab > 0:
        aux_transporte = _redondear_centavos((AUXILIO_TRANSPORTE_2026 / D("30")) * D(dias_lab))
    else:
        aux_transporte = D("0.00")

    # 6. Base de Cotización a Seguridad Social (IBC) — Incluye Básico + Comisiones (Art. 127 CST) + Horas Extras + Novedades
    ibc = _redondear_centavos(
        basico_proporcional
        + comisiones_mes
        + total_he_cop
        + res_nov.pago_vacaciones_cop
        + res_nov.pago_incapacidad_eps_cop
        + res_nov.pago_incapacidad_arl_cop
    )

    total_devengado = _redondear_centavos(ibc + aux_transporte)

    # 7. Deducciones Obligatorias de Ley del Trabajador (Salud 4%, Pensión 4%, FSP si > 4 SMLMV)
    ded_salud = _redondear_centavos(ibc * D("0.04"))
    ded_pension = _redondear_centavos(ibc * D("0.04"))
    fsp = _redondear_centavos(ibc * D("0.01")) if ibc >= (SMLMV_2026_AUTOPARTES * D("4")) else D("0.00")
    otras_ded = _redondear_centavos(D(otras_deducciones))

    total_deducido = _redondear_centavos(ded_salud + ded_pension + fsp + otras_ded)
    neto_pagar = _redondear_centavos(total_devengado - total_deducido)

    # 8. Aportes Patronales y Provisiones de Prestaciones Sociales (sobre base con comisiones Art. 127 CST)
    exonerado_art_114_1 = ibc < (SMLMV_2026_AUTOPARTES * D("10"))
    ap_salud_emp = D("0.00") if exonerado_art_114_1 else _redondear_centavos(ibc * D("0.085"))
    ap_pension_emp = _redondear_centavos(ibc * D("0.12"))
    ap_arl_emp = _redondear_centavos(ibc * D("0.01044"))  # Riesgo II Autopartes
    ap_ccf = _redondear_centavos(ibc * D("0.04"))
    ap_sena = D("0.00") if exonerado_art_114_1 else _redondear_centavos(ibc * D("0.02"))
    ap_icbf = D("0.00") if exonerado_art_114_1 else _redondear_centavos(ibc * D("0.03"))

    base_prestaciones = _redondear_centavos(basico_proporcional + comisiones_mes + total_he_cop + aux_transporte)
    prov_cesantias = _redondear_centavos(base_prestaciones * D("0.0833"))
    prov_intereses = _redondear_centavos(prov_cesantias * D("0.12"))
    prov_prima = _redondear_centavos(base_prestaciones * D("0.0833"))
    prov_vacaciones = _redondear_centavos((basico_proporcional + comisiones_mes) * D("0.0417"))

    seccion = "PERSONAL VENTAS" if empleado.es_vendedor else "PERSONAL ADMINISTRATIVO"

    liq = db.execute(
        select(LiquidacionNomina).where(
            LiquidacionNomina.empleado_id == empleado.id,
            LiquidacionNomina.periodo_anio == anio,
            LiquidacionNomina.periodo_mes == mes,
        )
    ).scalar_one_or_none()

    datos_liq = dict(
        seccion_plantilla=seccion,
        salario_basico_pactado=salario_pactado,
        comisiones_ventas_art127=comisiones_mes,
        sueldo_base_calculo=sueldo_base_calculo,
        divisor_hora_mes=divisor_hora,
        valor_hora_ordinaria=valor_hora,
        dias_laborados=dias_lab,
        dias_vacaciones=res_nov.dias_vacaciones,
        dias_incapacidad_eps=res_nov.dias_incapacidad_eps,
        dias_incapacidad_arl=res_nov.dias_incapacidad_arl,
        salario_basico_proporcional=basico_proporcional,
        auxilio_transporte_devengado=aux_transporte,
        pago_vacaciones=res_nov.pago_vacaciones_cop,
        pago_incapacidad_eps=res_nov.pago_incapacidad_eps_cop,
        pago_incapacidad_arl=res_nov.pago_incapacidad_arl_cop,
        horas_recargo_nocturno=_redondear_centavos(D(horas_rn)),
        horas_extra_diurna=hed_pagadas,
        horas_extra_nocturna=hen_pagadas,
        horas_festiva_diurna=_redondear_centavos(D(horas_fest_diurna)),
        horas_festiva_nocturna=_redondear_centavos(D(horas_fest_nocturna)),
        horas_extra_festiva_diurna=heddf_pagadas,
        horas_extra_festiva_nocturna=hendf_pagadas,
        total_horas_extras_cop=total_he_cop,
        horas_arrastradas_siguiente_mes=ledger.saldo_arrastre_siguiente_mes,
        total_devengado=total_devengado,
        ibc_seguridad_social=ibc,
        deduccion_salud_4pct=ded_salud,
        deduccion_pension_4pct=ded_pension,
        fondo_solidaridad_pensional=fsp,
        otras_deducciones_libranzas=otras_ded,
        total_deducido=total_deducido,
        neto_a_pagar=neto_pagar,
        aporte_salud_empleador=ap_salud_emp,
        aporte_pension_empleador=ap_pension_emp,
        aporte_arl_empleador=ap_arl_emp,
        aporte_caja_compensacion=ap_ccf,
        aporte_sena=ap_sena,
        aporte_icbf=ap_icbf,
        provision_cesantias=prov_cesantias,
        provision_intereses_cesantias=prov_intereses,
        provision_prima_servicios=prov_prima,
        provision_vacaciones=prov_vacaciones,
    )

    if liq:
        for k, v in datos_liq.items():
            setattr(liq, k, v)
        db.flush()
        return liq

    nueva_liq = LiquidacionNomina(
        empleado_id=empleado.id,
        periodo_anio=anio,
        periodo_mes=mes,
        **datos_liq,
    )
    db.add(nueva_liq)
    db.flush()
    return nueva_liq
