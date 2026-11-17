"""Servicio Autónomo de Monitoreo de Asistencia y Alerta Automática por Marcación Omitida
(Ventana de Gracia de 30 Minutos). Despacha correo simultáneo al Trabajador y al Jefe Inmediato
oficial (`tminero.sst@gmail.com`), exonerando automáticamente a empleados en Vacaciones o Incapacidad (EPS/ARL)."""
from __future__ import annotations

from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import registrar
from .models import (
    AlertaInasistencia,
    Empleado,
    JEFE_OFICIAL_EMAIL,
    MarcacionQR,
    TurnoProgramado,
)
from .legal import a_bogota
from .nomina_autopartes import tiene_novedad_activa
from .notificador import Notificacion, despachar

VENTANA_GRACIA_MINUTOS = 30


def evaluar_omisiones_30_minutos(db: Session, ahora_utc: datetime) -> list[dict]:
    """Evalúa todos los turnos programados del día local (America/Bogota UTC-5).
    Si han transcurrido >= 30 minutos desde la hora programada de ENTRADA o SALIDA
    sin marcación QR y el trabajador NO tiene novedad activa (Vacaciones / Incapacidad EPS / ARL),
    dispara alerta inmediata al trabajador y a `tminero.sst@gmail.com`."""
    fecha_hoy = a_bogota(ahora_utc).date()
    umbral_30m = timedelta(minutes=VENTANA_GRACIA_MINUTOS)

    turnos_hoy = db.execute(
        select(TurnoProgramado).where(TurnoProgramado.fecha == fecha_hoy)
    ).scalars().all()

    alertas_disparadas: list[dict] = []

    for turno in turnos_hoy:
        emp = db.get(Empleado, turno.empleado_id)
        if emp is None or not emp.activo:
            continue

        # 1. Bloqueo Inteligente por Novedad Activa (Vacaciones Art. 186 CST / Incapacidad EPS / ARL)
        nov = tiene_novedad_activa(db, emp.id, fecha_hoy)
        if nov is not None:
            continue

        # 2. Consultar marcaciones QR realizadas por el trabajador en la fecha
        marcas_hoy = db.execute(
            select(MarcacionQR.tipo).where(
                MarcacionQR.empleado_id == emp.id,
                MarcacionQR.fecha_local == fecha_hoy,
            )
        ).scalars().all()
        tipos_marcados = set(marcas_hoy)

        verificaciones = [
            ("ENTRADA", "ENTRADA_OMITIDA_30M", turno.inicio),
            ("SALIDA", "SALIDA_OMITIDA_30M", turno.fin),
        ]

        for tipo_marca, codigo_omision, hora_prog in verificaciones:
            if tipo_marca in tipos_marcados:
                continue
            if ahora_utc < (hora_prog + umbral_30m):
                continue

            # Verificar idempotencia (no enviar doble alerta por el mismo turno y tipo)
            ya_alertado = db.execute(
                select(AlertaInasistencia.id).where(
                    AlertaInasistencia.empleado_id == emp.id,
                    AlertaInasistencia.fecha == fecha_hoy,
                    AlertaInasistencia.tipo_omision == codigo_omision,
                )
            ).first()
            if ya_alertado:
                continue

            retraso_min = int((ahora_utc - hora_prog).total_seconds() // 60)
            hora_prog_local = a_bogota(hora_prog).strftime("%I:%M %p")
            hora_det_local = a_bogota(ahora_utc).strftime("%I:%M %p")

            # Correo 1: Alerta directa al Trabajador
            asunto_emp = f"⚠️ ALERTA DE ASISTENCIA DISJOYA: Omisión de marcación QR de {tipo_marca}"
            cuerpo_emp = (
                f"Estimado(a) {emp.nombre_completo} (C.C. {emp.numero_documento}):\n\n"
                f"El Sistema Agéntico de Control Horario de DISJOYA AUTOPARTES S.A.S. ha detectado que han "
                f"transcurrido {retraso_min} minutos desde su hora programada de {tipo_marca} ({hora_prog_local} del {fecha_hoy}) "
                f"sin que se registre su escaneo de código QR en sede.\n\n"
                f"Por favor realice su marcación de inmediato o reporte la novedad correspondiente a su Jefe Inmediato "
                f"({JEFE_OFICIAL_EMAIL}).\n\n"
                f"Hora legal de detección (UTC-5): {hora_det_local}."
            )
            res_emp = despachar(
                Notificacion(
                    destinatario_email=emp.email,
                    destinatario_whatsapp=emp.telefono_whatsapp,
                    asunto=asunto_emp,
                    cuerpo=cuerpo_emp,
                    parametros_plantilla=[emp.nombre_completo, tipo_marca, hora_prog_local],
                    datos={"empleado_id": str(emp.id), "tipo_omision": codigo_omision, "minutos_retraso": retraso_min},
                )
            )

            # Correo 2: Notificación Simultánea al Jefe Inmediato Oficial (tminero.sst@gmail.com)
            asunto_jefe = f"🚨 REPORTE JEFATURA DISJOYA: {emp.nombre_completo} sin marcación de {tipo_marca} (+{retraso_min} min)"
            cuerpo_jefe = (
                f"Reporte Automático de Control Disciplinario y Operativo — DISJOYA AUTOPARTES S.A.S.\n"
                f"Destinatario Oficial: Jefatura Inmediata ({JEFE_OFICIAL_EMAIL})\n\n"
                f"• Trabajador: {emp.nombre_completo}\n"
                f"• Documento: {emp.tipo_documento} {emp.numero_documento}\n"
                f"• Cargo: {emp.cargo}\n"
                f"• Evento: {codigo_omision} (Ventana de gracia de 30 minutos superada)\n"
                f"• Hora Programada: {hora_prog_local} ({fecha_hoy})\n"
                f"• Hora de Detección: {hora_det_local} ({retraso_min} minutos de retraso)\n"
                f"• Estado de Vacaciones/Incapacidad: SIN NOVEDAD ACTIVA REGISTRADA\n"
            )
            res_jefe = despachar(
                Notificacion(
                    destinatario_email=JEFE_OFICIAL_EMAIL,
                    destinatario_whatsapp=None,
                    asunto=asunto_jefe,
                    cuerpo=cuerpo_jefe,
                    parametros_plantilla=[emp.nombre_completo, codigo_omision, str(retraso_min)],
                    datos={"empleado_id": str(emp.id), "jefe_notificado": JEFE_OFICIAL_EMAIL, "tipo_omision": codigo_omision},
                )
            )

            alerta = AlertaInasistencia(
                empleado_id=emp.id,
                turno_id=turno.id,
                fecha=fecha_hoy,
                tipo_omision=codigo_omision,
                hora_programada=hora_prog,
                hora_deteccion=ahora_utc,
                minutos_retraso=retraso_min,
                correo_trabajador_enviado=(res_emp.get("email") == "enviado"),
                correo_jefe_enviado=(res_jefe.get("email") == "enviado"),
                jefe_email_notificado=JEFE_OFICIAL_EMAIL,
                resultado_despacho={"trabajador": res_emp, "jefe": res_jefe},
            )
            db.add(alerta)
            db.flush()

            registrar(
                db,
                ocurrido_en=ahora_utc,
                accion="ALERTA_INASISTENCIA_30M",
                entidad="alertas_inasistencia",
                entidad_id=alerta.id,
                actor_id=emp.id,
                actor_rol="SISTEMA",
                datos={
                    "empleado": emp.nombre_completo,
                    "documento": emp.numero_documento,
                    "tipo_omision": codigo_omision,
                    "minutos_retraso": retraso_min,
                    "jefe_notificado": JEFE_OFICIAL_EMAIL,
                    "canales_trabajador": res_emp,
                    "canales_jefe": res_jefe,
                },
            )

            alertas_disparadas.append(
                {
                    "alerta_id": str(alerta.id),
                    "empleado": emp.nombre_completo,
                    "documento": emp.numero_documento,
                    "email_empleado": emp.email,
                    "jefe_notificado": JEFE_OFICIAL_EMAIL,
                    "tipo_omision": codigo_omision,
                    "minutos_retraso": retraso_min,
                    "fecha": str(fecha_hoy),
                }
            )

    if alertas_disparadas:
        db.commit()

    return alertas_disparadas
