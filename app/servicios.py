"""Servicios agénticos del lazo MAPE-K (lógica de dominio reutilizable por la API y los jobs).

* MONITOR  : `intervalos_laborados`, `evaluar_jornada` (retrasos, salidas tempranas, tiempo no autorizado)
* ANALYZE  : `horas_he_comprometidas` + `legal.validar_topes`
* PLAN     : `conciliar` (doble validación HE aprobada vs. marcación real) y clasificación de recargos
* EXECUTE  : `preliquidar` (motor financiero-laboral)
* KNOWLEDGE: bitácora encadenada (`audit.registrar`) y sello SHA-256 del reporte de pre-nómina
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import legal
from .audit import jsonable, registrar
from .config import get_settings
from .legal import Intervalo, a_bogota, horas, inicio_dia_local, intersectar, normalizar, restar, total_segundos
from .models import (
    ESTADOS_HE_ACTIVOS,
    Empleado,
    MarcacionQR,
    NovedadJornada,
    SolicitudHorasExtras,
    TurnoProgramado,
)
from .security import sha256_hex

DOS_DEC = Decimal("0.01")
NOVEDADES_GESTIONADAS = ("LLEGADA_TARDE", "SALIDA_ANTICIPADA", "TIEMPO_SUPLEMENTARIO_NO_AUTORIZADO")


# ----------------------------------------------------------------------------- MONITOR
def intervalos_laborados(
    sesion: Session, empleado_id: uuid.UUID, desde: datetime, hasta: datetime
) -> tuple[list[Intervalo], list[MarcacionQR]]:
    """Empareja ENTRADA->SALIDA y recorta al rango. Devuelve (intervalos, entradas_sin_salida)."""
    marcas = sesion.execute(
        select(MarcacionQR)
        .where(
            MarcacionQR.empleado_id == empleado_id,
            MarcacionQR.marca_tiempo >= desde - timedelta(hours=16),
            MarcacionQR.marca_tiempo < hasta + timedelta(hours=16),
        )
        .order_by(MarcacionQR.marca_tiempo)
    ).scalars().all()
    pares: list[Intervalo] = []
    abiertas: list[MarcacionQR] = []
    entrada: MarcacionQR | None = None
    for m in marcas:
        if m.tipo == "ENTRADA":
            if entrada is not None:
                abiertas.append(entrada)
            entrada = m
        elif entrada is not None:
            pares.append(Intervalo(entrada.marca_tiempo, m.marca_tiempo))
            entrada = None
    if entrada is not None:
        abiertas.append(entrada)
    rango = [Intervalo(desde, hasta)]
    return intersectar(pares, rango), [a for a in abiertas if desde <= a.marca_tiempo < hasta]


def turnos_en(sesion: Session, empleado_id: uuid.UUID, desde: date, hasta: date) -> list[TurnoProgramado]:
    return list(
        sesion.execute(
            select(TurnoProgramado)
            .where(TurnoProgramado.empleado_id == empleado_id, TurnoProgramado.fecha >= desde, TurnoProgramado.fecha <= hasta)
            .order_by(TurnoProgramado.inicio)
        ).scalars()
    )


def segundos_por_fecha(intervalos: list[Intervalo]) -> dict[date, int]:
    res: dict[date, int] = defaultdict(int)
    for it in normalizar(intervalos):
        cur = it.inicio
        while cur < it.fin:
            d = a_bogota(cur).date()
            corte = min(it.fin, inicio_dia_local(d + timedelta(days=1)))
            res[d] += int((corte - cur).total_seconds())
            cur = corte
    return dict(res)


def evaluar_jornada(sesion: Session, empleado: Empleado, fecha: date, ahora: datetime) -> list[NovedadJornada]:
    """Recalcula (idempotente) las novedades de la jornada a partir de las marcaciones reales."""
    turnos = turnos_en(sesion, empleado.id, fecha, fecha)
    calculo: dict[str, tuple[int, str]] = {}
    if turnos:
        tol = get_settings().tolerancia_minutos_jornada * 60
        t_int = [Intervalo(t.inicio, t.fin) for t in turnos]
        desde = inicio_dia_local(fecha)
        hasta = inicio_dia_local(fecha + timedelta(days=1)) + timedelta(hours=6)
        laborado, abiertas = intervalos_laborados(sesion, empleado.id, desde, hasta)
        entradas = [w.inicio for w in laborado] + [a.marca_tiempo for a in abiertas]
        if entradas:
            retraso = int((min(entradas) - t_int[0].inicio).total_seconds())
            if retraso > tol:
                calculo["LLEGADA_TARDE"] = (retraso // 60, f"Ingreso {retraso // 60} min después del inicio del turno.")
        if laborado and not abiertas:
            anticipo = int((max(t.fin for t in t_int) - max(w.fin for w in laborado)).total_seconds())
            if anticipo > tol:
                calculo["SALIDA_ANTICIPADA"] = (anticipo // 60, f"Salida {anticipo // 60} min antes del fin del turno.")
        ventanas_he = [
            Intervalo(s.inicio_programado, s.fin_programado)
            for s in sesion.execute(
                select(SolicitudHorasExtras).where(
                    SolicitudHorasExtras.empleado_id == empleado.id,
                    SolicitudHorasExtras.fecha == fecha,
                    SolicitudHorasExtras.estado.in_(("APROBADA", "CONCILIADA")),
                )
            ).scalars()
        ]
        no_aut = total_segundos(restar(restar(laborado, t_int), ventanas_he))
        if no_aut > tol:
            calculo["TIEMPO_SUPLEMENTARIO_NO_AUTORIZADO"] = (
                no_aut // 60,
                f"{no_aut // 60} min laborados por fuera del turno sin autorización previa de horas extras. "
                "Requiere revisión del jefe inmediato: si fue ordenado o tolerado por el empleador debe remunerarse.",
            )
    existentes = {
        n.tipo: n
        for n in sesion.execute(
            select(NovedadJornada).where(
                NovedadJornada.empleado_id == empleado.id,
                NovedadJornada.fecha == fecha,
                NovedadJornada.resuelta.is_(False),
                NovedadJornada.tipo.in_(NOVEDADES_GESTIONADAS),
            )
        ).scalars()
    }
    cambiadas: list[NovedadJornada] = []
    for tipo in NOVEDADES_GESTIONADAS:
        actual = existentes.get(tipo)
        if tipo in calculo:
            minutos, detalle = calculo[tipo]
            if actual is None:
                actual = NovedadJornada(empleado_id=empleado.id, fecha=fecha, tipo=tipo, minutos=minutos, detalle=detalle, creado_en=ahora)
                sesion.add(actual)
                cambiadas.append(actual)
            elif actual.minutos != minutos:
                actual.minutos, actual.detalle = minutos, detalle
                cambiadas.append(actual)
        elif actual is not None:
            sesion.delete(actual)
    sesion.flush()
    return cambiadas


# ----------------------------------------------------------------------------- ANALYZE
def horas_he_comprometidas(
    sesion: Session, empleado_id: uuid.UUID, desde: date, hasta: date, excluir_id: uuid.UUID | None = None
) -> Decimal:
    """Horas extras que ya consumen cupo legal: pendientes y aprobadas (por lo solicitado) y
    conciliadas (por lo efectivamente reconocido)."""
    q = select(SolicitudHorasExtras).where(
        SolicitudHorasExtras.empleado_id == empleado_id,
        SolicitudHorasExtras.fecha >= desde,
        SolicitudHorasExtras.fecha <= hasta,
        SolicitudHorasExtras.estado.in_(ESTADOS_HE_ACTIVOS),
    )
    total = legal.CERO
    for s in sesion.execute(q).scalars():
        if excluir_id is not None and s.id == excluir_id:
            continue
        total += s.horas_reconocidas if s.estado == "CONCILIADA" and s.horas_reconocidas is not None else s.horas_solicitadas
    return Decimal(total).quantize(DOS_DEC)


def analizar_topes(sesion: Session, empleado: Empleado, fecha: date, horas_solicitadas: Decimal, excluir_id=None):
    sem_ini = legal.inicio_semana(fecha)
    h_dia = horas_he_comprometidas(sesion, empleado.id, fecha, fecha, excluir_id)
    h_sem = horas_he_comprometidas(sesion, empleado.id, sem_ini, sem_ini + timedelta(days=6), excluir_id)
    turnos = turnos_en(sesion, empleado.id, fecha, fecha)
    h_ord = horas(total_segundos([Intervalo(t.inicio, t.fin) for t in turnos]))
    return legal.validar_topes(
        horas_solicitadas=horas_solicitadas,
        horas_he_dia=h_dia,
        horas_he_semana=h_sem,
        horas_ordinarias_programadas_dia=h_ord,
        es_direccion_confianza=empleado.es_direccion_confianza,
        edad=legal.edad_en(empleado.fecha_nacimiento, fecha),
    ), h_dia, h_sem


# ----------------------------------------------------------------------------- PLAN
def conciliar(
    sesion: Session,
    empleado: Empleado,
    ahora: datetime,
    salida: MarcacionQR | None = None,
    actor_rol: str = "SISTEMA",
) -> list[SolicitudHorasExtras]:
    """Doble validación: confronta cada HE aprobada con el tiempo realmente laborado según QR.
    Es idempotente y re-computable: cada nueva marcación recalcula las conciliaciones recientes."""
    q = select(SolicitudHorasExtras).where(
        SolicitudHorasExtras.empleado_id == empleado.id,
        SolicitudHorasExtras.estado.in_(("APROBADA", "CONCILIADA")),
        SolicitudHorasExtras.inicio_programado < ahora,
    )
    if salida is not None:
        q = q.where(SolicitudHorasExtras.fecha >= salida.fecha_local - timedelta(days=1))
    else:
        q = q.where(SolicitudHorasExtras.fin_programado <= ahora)
    sols = list(sesion.execute(q.order_by(SolicitudHorasExtras.inicio_programado)).scalars())
    if not sols:
        return []
    desde = min(s.inicio_programado for s in sols)
    hasta = max(s.fin_programado for s in sols)
    laborado, _ = intervalos_laborados(sesion, empleado.id, desde, hasta)
    actualizadas: list[SolicitudHorasExtras] = []
    for s in sols:
        reales = total_segundos(intersectar([Intervalo(s.inicio_programado, s.fin_programado)], laborado))
        h_reales = horas(reales).quantize(DOS_DEC, ROUND_HALF_UP)
        h_recon = min(h_reales, Decimal(s.horas_solicitadas))
        if s.estado == "CONCILIADA" and s.horas_reales == h_reales:
            continue
        s.horas_reales, s.horas_reconocidas = h_reales, h_recon
        s.estado, s.conciliado_en = "CONCILIADA", ahora
        if salida is not None:
            s.marcacion_salida_id = salida.id
        registrar(
            sesion,
            ocurrido_en=ahora,
            accion="HE_CONCILIADA",
            entidad="solicitudes_horas_extras",
            entidad_id=s.id,
            actor_rol=actor_rol,
            datos={"horas_aprobadas": s.horas_solicitadas, "horas_reales": h_reales, "horas_reconocidas": h_recon,
                   "marcacion_salida_id": salida.id if salida else None},
        )
        if reales == 0 and s.fin_programado <= ahora:
            sesion.add(NovedadJornada(
                empleado_id=empleado.id, fecha=s.fecha, tipo="HE_APROBADA_NO_LABORADA", minutos=0,
                detalle=f"Horas extras aprobadas ({s.horas_solicitadas} h) sin tiempo laborado registrado por QR.", creado_en=ahora,
            ))
        actualizadas.append(s)
    sesion.flush()
    return actualizadas


# ----------------------------------------------------------------------------- EXECUTE
def preliquidar(sesion: Session, empleado: Empleado, desde: date, hasta: date, ahora: datetime) -> dict:
    ini, fin = inicio_dia_local(desde), inicio_dia_local(hasta + timedelta(days=1))
    laborado, abiertas = intervalos_laborados(sesion, empleado.id, ini, fin)
    turnos = turnos_en(sesion, empleado.id, desde, hasta)
    cobertura = [Intervalo(t.inicio, t.fin) for t in turnos]
    dias_con_turno = {t.fecha for t in turnos}
    dias_sin_turno: list[date] = []
    d = desde
    while d <= hasta:
        if d not in dias_con_turno:
            cobertura.append(Intervalo(inicio_dia_local(d), inicio_dia_local(d + timedelta(days=1))))
            dias_sin_turno.append(d)
        d += timedelta(days=1)

    conciliadas = list(sesion.execute(
        select(SolicitudHorasExtras).where(
            SolicitudHorasExtras.empleado_id == empleado.id,
            SolicitudHorasExtras.fecha >= desde,
            SolicitudHorasExtras.fecha <= hasta,
            SolicitudHorasExtras.estado.in_(("APROBADA", "CONCILIADA", "PENDIENTE")),
        )
    ).scalars())
    ventanas = [Intervalo(s.inicio_programado, s.fin_programado) for s in conciliadas if s.estado == "CONCILIADA"]
    extras = intersectar(ventanas, laborado)
    ordinarios = restar(intersectar(laborado, cobertura), extras)
    no_autorizado = restar(restar(laborado, cobertura), extras)

    res = legal.liquidar(Decimal(empleado.salario_base), ordinarios, extras, empleado.salario_integral)

    alertas: list[dict] = []
    ord_dia = segundos_por_fecha(ordinarios)
    ext_dia = segundos_por_fecha(extras)
    lab_dia = segundos_por_fecha(laborado)
    for f, seg in sorted(ord_dia.items()):
        if horas(seg) > legal.MAX_ORDINARIA_DIARIA_FLEXIBLE:
            alertas.append({"nivel": "ADVERTENCIA", "fecha": f, "codigo": "ORDINARIA_DIARIA_SUPERIOR_A_9H",
                            "detalle": f"{horas(seg)} h ordinarias (máx. 9 h diarias en jornada flexible, Art. 161 CST)."})
    for f, seg in sorted(ext_dia.items()):
        if horas(seg) > legal.MAX_HE_DIARIAS:
            alertas.append({"nivel": "CRITICA", "fecha": f, "codigo": "TOPE_DIARIO_HE_EXCEDIDO", "detalle": f"{horas(seg)} h extras."})
    semanas_ord: dict[date, int] = defaultdict(int)
    semanas_ext: dict[date, int] = defaultdict(int)
    for f, seg in ord_dia.items():
        semanas_ord[legal.inicio_semana(f)] += seg
    for f, seg in ext_dia.items():
        semanas_ext[legal.inicio_semana(f)] += seg
    for sem, seg in sorted(semanas_ord.items()):
        jornada = legal.parametros_vigentes(sem + timedelta(days=6)).jornada_semanal
        if horas(seg) > jornada:
            alertas.append({"nivel": "ADVERTENCIA", "fecha": sem, "codigo": "EXCESO_JORNADA_SEMANAL",
                            "detalle": f"{horas(seg)} h ordinarias en la semana; jornada máxima legal {jornada} h (Ley 2101 de 2021)."})
    for sem, seg in sorted(semanas_ext.items()):
        if horas(seg) > legal.MAX_HE_SEMANALES:
            alertas.append({"nivel": "CRITICA", "fecha": sem, "codigo": "TOPE_SEMANAL_HE_EXCEDIDO", "detalle": f"{horas(seg)} h extras."})
    for f in sorted(lab_dia):
        if legal.es_dominical_o_festivo(f):
            alertas.append({"nivel": "INFORMATIVA", "fecha": f, "codigo": "TRABAJO_DOMINICAL_FESTIVO",
                            "detalle": "Verificar descanso compensatorio (Arts. 180 y 181 CST) y habitualidad del trabajo dominical."})
    for f in dias_sin_turno:
        if f in lab_dia:
            alertas.append({"nivel": "ADVERTENCIA", "fecha": f, "codigo": "LABOR_SIN_TURNO_PROGRAMADO",
                            "detalle": "Tiempo laborado sin turno programado: clasificado como ordinario, requiere validación de RRHH."})
    for a in abiertas:
        alertas.append({"nivel": "ADVERTENCIA", "fecha": a.fecha_local, "codigo": "JORNADA_SIN_SALIDA",
                        "detalle": f"Entrada {a_bogota(a.marca_tiempo).isoformat()} sin marcación de salida: no liquidada."})
    for s in conciliadas:
        if s.estado in ("APROBADA", "PENDIENTE"):
            alertas.append({"nivel": "ADVERTENCIA", "fecha": s.fecha, "codigo": f"HE_{s.estado}_SIN_CONCILIAR",
                            "detalle": f"Solicitud {s.id} ({s.horas_solicitadas} h) aún no confrontada con marcación real."})
    if total_segundos(no_autorizado):
        alertas.append({"nivel": "ADVERTENCIA", "fecha": desde, "codigo": "TIEMPO_NO_AUTORIZADO",
                        "detalle": f"{horas(total_segundos(no_autorizado))} h fuera de turno sin autorización: no liquidadas, escaladas al jefe inmediato."})

    params = sorted({legal.parametros_vigentes(f) for f in lab_dia} or {legal.parametros_vigentes(hasta)}, key=lambda p: p.fecha)
    parametros_aplicados = []
    vistos = set()
    for p in params:
        clave = (p.jornada_semanal, p.inicio_nocturno, p.recargo_dominical)
        if clave in vistos:
            continue
        vistos.add(clave)
        parametros_aplicados.append({
            "desde_fecha_causacion": p.fecha, "jornada_semanal_h": p.jornada_semanal, "divisor_horas_mes": p.horas_mes,
            "franja_nocturna": f"{p.inicio_nocturno.strftime('%H:%M')}-06:00", "recargo_dominical_festivo_pct": p.recargo_dominical * 100,
        })

    lineas = [{
        "concepto": ln.concepto, "codigo_dian": ln.codigo_dian, "descripcion": ln.nombre, "base_legal": ln.base_legal,
        "horas": ln.horas, "porcentaje_recargo": ln.porcentaje_recargo, "factor_pago": ln.factor_pago,
        "valor_hora_ordinaria": ln.valor_hora.quantize(DOS_DEC, ROUND_HALF_UP),
        "valor": ln.valor if not empleado.salario_integral else Decimal("0.00"),
        "nota": "Incluida en el salario básico mensual" if ln.concepto == "HOD" else None,
    } for ln in res.lineas]

    dian: dict[str, list] = defaultdict(list)
    for ln in res.lineas:
        if ln.codigo_dian == "Basico":
            continue
        dian[f"{ln.codigo_dian}s"].append({"Cantidad": ln.horas.quantize(DOS_DEC), "Porcentaje": ln.porcentaje_recargo.quantize(DOS_DEC),
                                           "Pago": ln.valor if not empleado.salario_integral else Decimal("0.00")})

    reporte = {
        "empleado": {"id": empleado.id, "documento": f"{empleado.tipo_documento} {empleado.numero_documento}",
                     "nombre": empleado.nombre_completo, "cargo": empleado.cargo, "salario_base": empleado.salario_base,
                     "salario_integral": empleado.salario_integral},
        "periodo": {"desde": desde, "hasta": hasta, "zona_horaria": "America/Bogota"},
        "parametros_legales_aplicados": parametros_aplicados,
        "resumen_horas": {
            "laboradas": horas(total_segundos(laborado)),
            "ordinarias": horas(total_segundos(ordinarios)),
            "extras_reconocidas": horas(total_segundos(extras)),
            "no_autorizadas_no_liquidadas": horas(total_segundos(no_autorizado)),
        },
        "lineas": lineas,
        "total_recargos_y_extras": res.total_recargos_y_extras,
        "nomina_electronica_dian": dict(dian),
        "alertas": alertas,
        "generado_en": ahora,
    }
    reporte = jsonable(reporte)
    reporte["sello_sha256"] = sha256_hex(json.dumps(reporte, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return reporte

