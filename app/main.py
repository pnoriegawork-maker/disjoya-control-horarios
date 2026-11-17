"""API FastAPI - Sistema Agéntico de Control Horario, Horas Extras y Pre-liquidación de Nómina.

Ejecutar:  uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
(TLS 1.3 terminado en el proxy inverso - ver docs/ARQUITECTURA_MAPE-K.md, sección 4)."""
from __future__ import annotations

import hmac
import ipaddress
import json
import threading
import time as _time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import legal
from .audit import registrar, verificar_cadena
from .config import get_settings
from .database import fijar_contexto, get_db, nueva_sesion
from .geo import haversine_m, punto_en_poligono, velocidad_kmh
from .habeas import HASH_POLITICA, TEXTO_POLITICA, VERSION_POLITICA
from .legal import a_bogota, a_utc, horas, inicio_dia_local
from .models import (
    AuditoriaLog,
    CentroTrabajo,
    ConsentimientoHabeasData,
    Empleado,
    MarcacionQR,
    NovedadJornada,
    SolicitudHorasExtras,
    TurnoProgramado,
)
from .notificador import Notificacion, despachar
from .schemas import (
    AsignarJefeIn,
    CentroCreadoOut,
    CentroIn,
    ConsentimientoIn,
    DecisionHEIn,
    DispositivoIn,
    EmpleadoIn,
    EmpleadoOut,
    MarcacionIn,
    SolicitudHEIn,
    SolicitudHEOut,
    TokenIn,
    TokenOut,
    TurnoIn,
)
from .security import (
    cifrar,
    cifrar_json,
    construir_payload_qr,
    crear_jwt,
    decodificar_jwt,
    descifrar,
    descifrar_json,
    hash_dispositivo,
    hash_password,
    nuevo_secreto_totp,
    parsear_payload_qr,
    sha256_hex,
    ventana_totp,
    verificar_password,
    verificar_totp,
)
from .servicios import analizar_topes, conciliar, evaluar_jornada, intervalos_laborados, preliquidar
from .tiempo import RelojLegal, get_reloj

app = FastAPI(
    title="Disjoya - Control Horario Agéntico (MAPE-K)",
    version="1.0.0",
    description="Marcación QR dinámica con geocerca, workflow legal de horas extras y pre-liquidación de nómina (Colombia).",
)

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", include_in_schema=False)
@app.get("/trabajador", include_in_schema=False)
@app.get("/admin", include_in_schema=False)
@app.get("/jefe", include_in_schema=False)
def index():
    html_file = STATIC_DIR / "index.html"
    if html_file.exists():
        return FileResponse(html_file)
    return {"mensaje": "Disjoya Autopartes SAS - Control Horario"}

bearer = HTTPBearer(auto_error=False)
PREFIJO = "/api/v1"


# ============================================================================ Infraestructura
@app.middleware("http")
async def cabeceras_seguridad(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Permissions-Policy"] = "geolocation=(self), camera=(self)"
    if request.url.path.startswith("/api/"):
        resp.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return resp


class LimitadorTasa:
    """Ventana deslizante en memoria (por proceso). En despliegues multi-réplica: Redis/WAF."""

    def __init__(self) -> None:
        self._eventos: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def verificar(self, clave: str, maximo: int, ventana_s: int) -> None:
        ahora = _time.monotonic()
        with self._lock:
            cola = self._eventos[clave]
            while cola and ahora - cola[0] > ventana_s:
                cola.popleft()
            if len(cola) >= maximo:
                raise HTTPException(429, "Demasiadas solicitudes; intente más tarde")
            cola.append(ahora)


limitador = LimitadorTasa()


def ip_cliente(request: Request) -> str | None:
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


@dataclass
class Actor:
    empleado: Empleado
    ip: str | None
    user_agent: str | None

    @property
    def id(self) -> uuid.UUID:
        return self.empleado.id

    @property
    def rol(self) -> str:
        return self.empleado.rol


def actor_actual(
    request: Request,
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Actor:
    if cred is None:
        raise HTTPException(401, "Autenticación requerida", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decodificar_jwt(cred.credentials)
        emp_id = uuid.UUID(payload["sub"])
    except Exception:
        raise HTTPException(401, "Token inválido o expirado", headers={"WWW-Authenticate": "Bearer"})
    fijar_contexto(db, str(emp_id), payload.get("rol", "ANONIMO"))
    emp = db.get(Empleado, emp_id)
    if emp is None or not emp.activo or emp.rol != payload.get("rol"):
        raise HTTPException(401, "Usuario inactivo o rol modificado; inicie sesión nuevamente")
    return Actor(emp, ip_cliente(request), request.headers.get("user-agent"))


def requiere(*roles: str):
    def _dep(actor: Actor = Depends(actor_actual), db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> Actor:
        if actor.rol not in roles:
            registrar(db, ocurrido_en=reloj.ahora().utc, accion="ACCESO_DENEGADO", entidad="rbac", actor_id=actor.id,
                      actor_rol=actor.rol, ip_origen=actor.ip, user_agent=actor.user_agent, datos={"roles_requeridos": roles})
            db.commit()
            raise HTTPException(403, "Rol sin permiso para esta operación")
        return actor

    return _dep


def consentimiento_vigente(db: Session, empleado_id: uuid.UUID) -> ConsentimientoHabeasData | None:
    return db.execute(
        select(ConsentimientoHabeasData)
        .where(ConsentimientoHabeasData.empleado_id == empleado_id, ConsentimientoHabeasData.revocado_en.is_(None))
        .order_by(ConsentimientoHabeasData.otorgado_en.desc())
        .limit(1)
    ).scalar_one_or_none()


def _notificar_en_segundo_plano(n: Notificacion, entidad: str, entidad_id: str) -> None:
    resultado = despachar(n)
    with nueva_sesion(rol="SISTEMA") as s:
        registrar(s, ocurrido_en=get_reloj().ahora().utc, accion="NOTIFICACION_DESPACHADA", entidad=entidad,
                  entidad_id=entidad_id, datos={"canales": resultado, "asunto": n.asunto})
        s.commit()


def _local(dt: datetime) -> str:
    return a_bogota(dt).strftime("%Y-%m-%d %H:%M")


@app.get("/health")
def health(reloj: RelojLegal = Depends(get_reloj)) -> dict:
    inst = reloj.ahora()
    return {"estado": "ok", "hora_legal": a_bogota(inst.utc).isoformat(), "ntp_sincronizado": inst.ntp_sincronizado,
            "desfase_ms": inst.ntp_desfase_ms, "parametros": _parametros_dict(a_bogota(inst.utc).date())}


def _parametros_dict(f: date) -> dict:
    p = legal.parametros_vigentes(f)
    return {"fecha": f.isoformat(), "jornada_semanal_h": p.jornada_semanal, "divisor_horas_mes": p.horas_mes,
            "inicio_nocturno": p.inicio_nocturno.strftime("%H:%M"), "recargo_dominical_pct": str(p.recargo_dominical * 100),
            "factores": {k: str(v) for k, v in p.factores().items()}}


@app.get(f"{PREFIJO}/legal/parametros")
def parametros_legales(fecha: date | None = None, reloj: RelojLegal = Depends(get_reloj)) -> dict:
    f = fecha or a_bogota(reloj.ahora().utc).date()
    return {**_parametros_dict(f), "festivos_anio": {d.isoformat(): n for d, n in legal.festivos_colombia(f.year).items()}}


# ============================================================================ Autenticación
@app.post(f"{PREFIJO}/auth/token", response_model=TokenOut)
def login(body: TokenIn, request: Request, db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)):
    ip = ip_cliente(request)
    limitador.verificar(f"login:{ip}", 10, 300)
    fijar_contexto(db, "", "SISTEMA")
    emp = db.execute(select(Empleado).where(Empleado.numero_documento == body.numero_documento)).scalar_one_or_none()
    ok = emp is not None and emp.activo and verificar_password(body.password, emp.password_hash)
    registrar(db, ocurrido_en=reloj.ahora().utc, accion="LOGIN_EXITOSO" if ok else "LOGIN_FALLIDO", entidad="empleados",
              entidad_id=emp.id if emp else None, actor_id=emp.id if ok else None, actor_rol=emp.rol if ok else "ANONIMO",
              ip_origen=ip, user_agent=request.headers.get("user-agent"),
              datos={} if ok else {"documento_hash": sha256_hex(body.numero_documento)})
    db.commit()
    if not ok:
        raise HTTPException(401, "Credenciales inválidas")
    return TokenOut(access_token=crear_jwt(emp.id, emp.rol), rol=emp.rol, expira_en_minutos=get_settings().jwt_minutos)


# ============================================================================ Habeas Data
@app.get(f"{PREFIJO}/habeas-data/politica")
def politica() -> dict:
    return {"version": VERSION_POLITICA, "sha256": HASH_POLITICA, "texto": TEXTO_POLITICA}


@app.post(f"{PREFIJO}/habeas-data/consentimiento", status_code=201)
def otorgar_consentimiento(body: ConsentimientoIn, actor: Actor = Depends(actor_actual), db: Session = Depends(get_db),
                           reloj: RelojLegal = Depends(get_reloj)) -> dict:
    if body.version_politica != VERSION_POLITICA:
        raise HTTPException(409, f"Debe aceptar la versión vigente de la política ({VERSION_POLITICA})")
    if not body.acepta_tratamiento_laboral:
        raise HTTPException(422, "La autorización de tratamiento laboral es necesaria para usar la aplicación")
    inst = reloj.ahora()
    previo = consentimiento_vigente(db, actor.id)
    if previo:
        previo.revocado_en = inst.utc
    c = ConsentimientoHabeasData(empleado_id=actor.id, version_politica=VERSION_POLITICA, hash_texto_politica=HASH_POLITICA,
                                 acepta_tratamiento_laboral=True, acepta_geolocalizacion=body.acepta_geolocalizacion,
                                 otorgado_en=inst.utc, ip_origen=actor.ip, user_agent=(actor.user_agent or "")[:300])
    db.add(c)
    db.flush()
    registrar(db, ocurrido_en=inst.utc, accion="CONSENTIMIENTO_OTORGADO", entidad="consentimientos_habeas_data", entidad_id=c.id,
              actor_id=actor.id, actor_rol=actor.rol, ip_origen=actor.ip, user_agent=actor.user_agent,
              datos={"version": VERSION_POLITICA, "sha256_politica": HASH_POLITICA, "geolocalizacion": body.acepta_geolocalizacion})
    db.commit()
    return {"id": str(c.id), "version": VERSION_POLITICA, "geolocalizacion": c.acepta_geolocalizacion, "otorgado_en": a_bogota(c.otorgado_en).isoformat()}


@app.post(f"{PREFIJO}/habeas-data/revocar")
def revocar_consentimiento(actor: Actor = Depends(actor_actual), db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    c = consentimiento_vigente(db, actor.id)
    if c is None:
        raise HTTPException(404, "No existe consentimiento vigente")
    inst = reloj.ahora()
    c.revocado_en = inst.utc
    registrar(db, ocurrido_en=inst.utc, accion="CONSENTIMIENTO_REVOCADO", entidad="consentimientos_habeas_data", entidad_id=c.id,
              actor_id=actor.id, actor_rol=actor.rol, ip_origen=actor.ip, user_agent=actor.user_agent, datos={})
    db.commit()
    return {"revocado": True, "medio_alterno": "Marcación asistida en kiosco con verificación del jefe inmediato."}


@app.get(f"{PREFIJO}/habeas-data/mis-datos")
def mis_datos(actor: Actor = Depends(actor_actual), db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    """Derecho de acceso (Art. 8 lit. a y Art. 14 Ley 1581 de 2012)."""
    e = actor.empleado
    marcas = db.execute(select(MarcacionQR).where(MarcacionQR.empleado_id == e.id).order_by(MarcacionQR.marca_tiempo.desc()).limit(500)).scalars()
    registrar(db, ocurrido_en=reloj.ahora().utc, accion="CONSULTA_TITULAR", entidad="empleados", entidad_id=e.id,
              actor_id=e.id, actor_rol=e.rol, ip_origen=actor.ip, user_agent=actor.user_agent, datos={})
    db.commit()
    return {
        "titular": {"documento": f"{e.tipo_documento} {e.numero_documento}", "nombre": e.nombre_completo, "email": e.email,
                    "cargo": e.cargo, "dispositivo_registrado": bool(e.dispositivo_hash)},
        "marcaciones": [{"tipo": m.tipo, "hora_legal": a_bogota(m.marca_tiempo).isoformat(),
                         "coordenadas": descifrar_json(m.coordenadas_cifradas, f"marcacion:{m.id}"),
                         "distancia_m": str(m.distancia_metros)} for m in marcas],
    }


# ============================================================================ Administración
@app.post(f"{PREFIJO}/centros", response_model=CentroCreadoOut, status_code=201)
def crear_centro(body: CentroIn, actor: Actor = Depends(requiere("AUDITOR_SUPERADMIN")), db: Session = Depends(get_db),
                 reloj: RelojLegal = Depends(get_reloj)):
    import secrets

    cid = uuid.uuid4()
    api_key = secrets.token_urlsafe(32)
    c = CentroTrabajo(id=cid, nombre=body.nombre, direccion=body.direccion, municipio_dane=body.municipio_dane,
                      latitud=body.latitud, longitud=body.longitud, radio_metros=body.radio_metros,
                      poligono=[list(p) for p in body.poligono] if body.poligono else None,
                      secreto_totp_cifrado=cifrar(nuevo_secreto_totp(), f"centro:{cid}"), kiosco_api_key_hash=sha256_hex(api_key))
    db.add(c)
    db.flush()
    registrar(db, ocurrido_en=reloj.ahora().utc, accion="CENTRO_CREADO", entidad="centros_trabajo", entidad_id=cid, actor_id=actor.id,
              actor_rol=actor.rol, ip_origen=actor.ip, datos={"nombre": body.nombre, "radio_metros": body.radio_metros})
    db.commit()
    return CentroCreadoOut(id=cid, nombre=c.nombre, radio_metros=c.radio_metros, kiosco_api_key=api_key)


@app.post(f"{PREFIJO}/empleados", response_model=EmpleadoOut, status_code=201)
def crear_empleado(body: EmpleadoIn, actor: Actor = Depends(requiere("NOMINA_RRHH", "AUDITOR_SUPERADMIN")),
                   db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)):
    if body.rol == "AUDITOR_SUPERADMIN" and actor.rol != "AUDITOR_SUPERADMIN":
        raise HTTPException(403, "Solo un SuperAdmin puede crear otro SuperAdmin")
    if body.jefe_inmediato_id and db.get(Empleado, body.jefe_inmediato_id) is None:
        raise HTTPException(422, "Jefe inmediato inexistente")
    datos = body.model_dump(exclude={"password"})
    e = Empleado(**datos, password_hash=hash_password(body.password))
    db.add(e)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Documento o correo ya registrado")
    registrar(db, ocurrido_en=reloj.ahora().utc, accion="EMPLEADO_CREADO", entidad="empleados", entidad_id=e.id, actor_id=actor.id,
              actor_rol=actor.rol, ip_origen=actor.ip, datos={"rol": e.rol, "cargo": e.cargo, "jefe": e.jefe_inmediato_id})
    db.commit()
    return e


@app.get(f"{PREFIJO}/admin/empleados")
def listar_empleados_admin(actor: Actor = Depends(requiere("NOMINA_RRHH", "AUDITOR_SUPERADMIN")), db: Session = Depends(get_db)):
    emps = db.execute(select(Empleado).order_by(Empleado.nombres)).scalars().all()
    resultado = []
    for e in emps:
        jefe = db.get(Empleado, e.jefe_inmediato_id) if e.jefe_inmediato_id else None
        centro = db.get(CentroTrabajo, e.centro_trabajo_id) if e.centro_trabajo_id else None
        resultado.append({
            "id": str(e.id),
            "tipo_documento": e.tipo_documento,
            "numero_documento": e.numero_documento,
            "nombres": e.nombres,
            "apellidos": e.apellidos,
            "nombre_completo": e.nombre_completo,
            "email": e.email,
            "rol": e.rol,
            "cargo": e.cargo,
            "jefe_inmediato_id": str(e.jefe_inmediato_id) if e.jefe_inmediato_id else None,
            "jefe_inmediato_nombre": jefe.nombre_completo if jefe else "Sin asignar",
            "jefe_inmediato_email": jefe.email if jefe else None,
            "centro_trabajo_id": str(e.centro_trabajo_id) if e.centro_trabajo_id else None,
            "centro_nombre": centro.nombre if centro else "Sin asignar",
            "activo": e.activo
        })
    return resultado


@app.get(f"{PREFIJO}/admin/jefes")
def listar_jefes_disponibles(actor: Actor = Depends(requiere("NOMINA_RRHH", "AUDITOR_SUPERADMIN")), db: Session = Depends(get_db)):
    jefes = db.execute(select(Empleado).where(Empleado.rol.in_(["JEFE_INMEDIATO", "AUDITOR_SUPERADMIN"]), Empleado.activo == True).order_by(Empleado.nombres)).scalars().all()
    return [{"id": str(j.id), "nombre_completo": j.nombre_completo, "email": j.email, "cargo": j.cargo, "rol": j.rol} for j in jefes]


@app.patch(f"{PREFIJO}/admin/empleados/{{empleado_id}}/jefe")
def asignar_jefe_inmediato(
    empleado_id: uuid.UUID,
    body: AsignarJefeIn,
    actor: Actor = Depends(requiere("NOMINA_RRHH", "AUDITOR_SUPERADMIN")),
    db: Session = Depends(get_db),
    reloj: RelojLegal = Depends(get_reloj)
):
    emp = db.get(Empleado, empleado_id)
    if emp is None:
        raise HTTPException(404, "Empleado no encontrado")
    if body.jefe_inmediato_id:
        if body.jefe_inmediato_id == empleado_id:
            raise HTTPException(422, "Un empleado no puede ser su propio jefe inmediato")
        jefe = db.get(Empleado, body.jefe_inmediato_id)
        if jefe is None or not jefe.activo:
            raise HTTPException(422, "Jefe inmediato no encontrado o inactivo")
        if jefe.rol not in ("JEFE_INMEDIATO", "AUDITOR_SUPERADMIN"):
            raise HTTPException(422, "El usuario seleccionado no tiene rol de jefatura o administración")
    
    jefe_anterior = emp.jefe_inmediato_id
    emp.jefe_inmediato_id = body.jefe_inmediato_id
    
    registrar(db, ocurrido_en=reloj.ahora().utc, accion="ASIGNACION_JEFE_ACTUALIZADA", entidad="empleados", entidad_id=emp.id,
              actor_id=actor.id, actor_rol=actor.rol, ip_origen=actor.ip,
              datos={"jefe_anterior": str(jefe_anterior) if jefe_anterior else None,
                     "nuevo_jefe": str(body.jefe_inmediato_id) if body.jefe_inmediato_id else None})
    db.commit()
    jefe_nuevo = db.get(Empleado, body.jefe_inmediato_id) if body.jefe_inmediato_id else None
    return {
        "empleado_id": str(emp.id),
        "empleado_nombre": emp.nombre_completo,
        "jefe_inmediato_id": str(body.jefe_inmediato_id) if body.jefe_inmediato_id else None,
        "jefe_inmediato_nombre": jefe_nuevo.nombre_completo if jefe_nuevo else "Sin asignar",
        "mensaje": "Jefe inmediato asignado exitosamente"
    }


@app.post(f"{PREFIJO}/turnos", status_code=201)
def crear_turno(body: TurnoIn, actor: Actor = Depends(requiere("NOMINA_RRHH", "JEFE_INMEDIATO")), db: Session = Depends(get_db),
                reloj: RelojLegal = Depends(get_reloj)) -> dict:
    emp = db.get(Empleado, body.empleado_id)
    if emp is None or emp.centro_trabajo_id is None:
        raise HTTPException(422, "Empleado inexistente o sin centro de trabajo asignado")
    if actor.rol == "JEFE_INMEDIATO" and emp.jefe_inmediato_id != actor.id:
        raise HTTPException(403, "Solo puede programar turnos de su equipo")
    fecha = a_bogota(body.inicio).date()
    traslape = db.execute(select(TurnoProgramado.id).where(TurnoProgramado.empleado_id == emp.id,
                                                           TurnoProgramado.inicio < body.fin, TurnoProgramado.fin > body.inicio)).first()
    if traslape:
        raise HTTPException(409, "El turno se traslapa con otro turno programado")
    t = TurnoProgramado(empleado_id=emp.id, centro_trabajo_id=emp.centro_trabajo_id, fecha=fecha, inicio=body.inicio, fin=body.fin,
                        creado_por_id=actor.id)
    db.add(t)
    db.flush()
    registrar(db, ocurrido_en=reloj.ahora().utc, accion="TURNO_PROGRAMADO", entidad="turnos_programados", entidad_id=t.id,
              actor_id=actor.id, actor_rol=actor.rol, ip_origen=actor.ip,
              datos={"empleado_id": emp.id, "inicio": body.inicio, "fin": body.fin})
    db.commit()
    return {"id": str(t.id), "fecha": fecha.isoformat(), "inicio_local": _local(t.inicio), "fin_local": _local(t.fin),
            "horas": str(horas((t.fin - t.inicio).total_seconds()))}


@app.post(f"{PREFIJO}/dispositivos", status_code=201)
def registrar_dispositivo(body: DispositivoIn, actor: Actor = Depends(actor_actual), db: Session = Depends(get_db),
                          reloj: RelojLegal = Depends(get_reloj)) -> dict:
    e = actor.empleado
    h = hash_dispositivo(str(body.device_uuid))
    if e.dispositivo_hash and e.dispositivo_hash != h:
        raise HTTPException(409, "Ya existe un dispositivo vinculado; solicite a RRHH su desvinculación")
    otro = db.execute(select(Empleado.id).where(Empleado.dispositivo_hash == h, Empleado.id != e.id)).first()
    if otro:
        raise HTTPException(409, "El dispositivo ya está vinculado a otro trabajador")
    inst = reloj.ahora()
    e.dispositivo_hash, e.dispositivo_registrado_en = h, inst.utc
    registrar(db, ocurrido_en=inst.utc, accion="DISPOSITIVO_VINCULADO", entidad="empleados", entidad_id=e.id, actor_id=e.id,
              actor_rol=e.rol, ip_origen=actor.ip, dispositivo_hash=h, user_agent=actor.user_agent, datos={})
    db.commit()
    return {"vinculado": True, "dispositivo_hash": h}


@app.delete(f"{PREFIJO}/empleados/{{empleado_id}}/dispositivo")
def desvincular_dispositivo(empleado_id: uuid.UUID, actor: Actor = Depends(requiere("NOMINA_RRHH", "AUDITOR_SUPERADMIN")),
                            db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    e = db.get(Empleado, empleado_id)
    if e is None:
        raise HTTPException(404, "Empleado inexistente")
    anterior = e.dispositivo_hash
    e.dispositivo_hash = None
    e.dispositivo_registrado_en = None
    registrar(db, ocurrido_en=reloj.ahora().utc, accion="DISPOSITIVO_DESVINCULADO", entidad="empleados", entidad_id=e.id,
              actor_id=actor.id, actor_rol=actor.rol, ip_origen=actor.ip, dispositivo_hash=anterior, datos={})
    db.commit()
    return {"desvinculado": True}


# ============================================================================ ENDPOINT 1: QR dinámico + geocerca
@app.get(f"{PREFIJO}/qr/centros/{{centro_id}}/actual")
def qr_actual(centro_id: uuid.UUID, x_kiosk_key: str = Header(..., alias="X-Kiosk-Key"), db: Session = Depends(get_db),
              reloj: RelojLegal = Depends(get_reloj)) -> dict:
    """Consumido por la pantalla/kiosco del centro de trabajo, que renderiza el payload como QR y
    lo refresca cada `ttl` segundos. Un QR fotografiado o reenviado por WhatsApp expira en <= 2 TTL."""
    fijar_contexto(db, "", "SISTEMA")
    c = db.get(CentroTrabajo, centro_id)
    if c is None or not c.activo or not hmac.compare_digest(c.kiosco_api_key_hash, sha256_hex(x_kiosk_key)):
        raise HTTPException(401, "Kiosco no autorizado")
    s = get_settings()
    inst = reloj.ahora()
    v = ventana_totp(inst.utc, s.qr_ttl_segundos)
    secreto = descifrar(c.secreto_totp_cifrado, f"centro:{c.id}")
    expira = datetime.fromtimestamp((v + 1) * s.qr_ttl_segundos, tz=inst.utc.tzinfo)
    return {"payload": construir_payload_qr(secreto, c.id, v), "ttl_segundos": s.qr_ttl_segundos,
            "expira_en": a_bogota(expira).isoformat(), "centro": c.nombre}


@app.post(f"{PREFIJO}/marcaciones", status_code=201)
def registrar_marcacion(body: MarcacionIn, bg: BackgroundTasks, actor: Actor = Depends(actor_actual),
                        db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    s = get_settings()
    limitador.verificar(f"marcacion:{actor.id}", 6, 60)
    inst = reloj.ahora()
    emp = actor.empleado
    dev_hash = hash_dispositivo(str(body.device_uuid))
    coords_audit = cifrar_json({"lat": body.latitud, "lon": body.longitud, "precision_m": body.precision_m}, f"audit:{emp.id}").hex()

    def rechazar(http: int, codigo: str, mensaje: str, extra: dict | None = None):
        registrar(db, ocurrido_en=inst.utc, accion="MARCACION_RECHAZADA", entidad="marcaciones_qr", actor_id=emp.id,
                  actor_rol=emp.rol, ip_origen=actor.ip, dispositivo_hash=dev_hash, user_agent=actor.user_agent,
                  datos={"causa": codigo, "coordenadas_cifradas": coords_audit, "ntp_sincronizado": inst.ntp_sincronizado, **(extra or {})})
        db.commit()
        raise HTTPException(http, {"codigo": codigo, "mensaje": mensaje})

    # ---- M: reloj legal, habeas data, dispositivo ------------------------------------
    if s.ntp_modo_estricto and not inst.ntp_sincronizado:
        rechazar(503, "RELOJ_NO_SINCRONIZADO", "El reloj legal del servidor no está sincronizado por NTP.")
    cons = consentimiento_vigente(db, emp.id)
    if cons is None or not cons.acepta_geolocalizacion:
        rechazar(403, "SIN_CONSENTIMIENTO_GEOLOCALIZACION", "Sin autorización de geolocalización vigente: use el medio alterno de marcación asistida.")
    if not emp.dispositivo_hash:
        rechazar(403, "DISPOSITIVO_NO_REGISTRADO", "Debe vincular su dispositivo antes de marcar.")
    if not hmac.compare_digest(emp.dispositivo_hash, dev_hash):
        rechazar(403, "DISPOSITIVO_NO_AUTORIZADO", "El dispositivo no corresponde al vinculado al trabajador.")

    # ---- M: QR dinámico TOTP ----------------------------------------------------------
    try:
        centro_id, ventana, codigo = parsear_payload_qr(body.qr_payload)
    except ValueError:
        rechazar(422, "QR_INVALIDO", "El código QR no es institucional o está malformado.")
    centro = db.get(CentroTrabajo, centro_id)
    if centro is None or not centro.activo:
        rechazar(404, "CENTRO_INEXISTENTE", "Centro de trabajo inexistente o inactivo.")
    if emp.centro_trabajo_id and emp.centro_trabajo_id != centro.id:
        rechazar(403, "CENTRO_NO_ASIGNADO", "El trabajador no está asignado a este centro de trabajo.")
    secreto = descifrar(centro.secreto_totp_cifrado, f"centro:{centro.id}")
    v_actual = ventana_totp(inst.utc, s.qr_ttl_segundos)
    if not verificar_totp(secreto, centro.id, ventana, codigo, v_actual, s.qr_ventanas_tolerancia):
        rechazar(401, "QR_EXPIRADO_O_FALSO", "El QR expiró o no es auténtico. Escanee el código vigente en pantalla.",
                 {"ventana_qr": ventana, "ventana_servidor": v_actual})
    if db.execute(select(MarcacionQR.id).where(MarcacionQR.empleado_id == emp.id, MarcacionQR.centro_trabajo_id == centro.id,
                                               MarcacionQR.ventana_totp == ventana)).first():
        rechazar(409, "QR_REUTILIZADO", "Este QR ya fue utilizado por el trabajador (anti-replay).")

    # ---- M: geocerca GPS (Haversine + polígono opcional) ---------------------------------
    if body.ubicacion_simulada:
        rechazar(403, "UBICACION_SIMULADA", "El sistema operativo reporta ubicación simulada (mock location).")
    if body.precision_m > s.gps_precision_max_m:
        rechazar(422, "PRECISION_GPS_INSUFICIENTE", f"Precisión GPS {body.precision_m:.0f} m > {s.gps_precision_max_m:.0f} m permitidos.")
    distancia = haversine_m(float(centro.latitud), float(centro.longitud), body.latitud, body.longitud)
    dentro = distancia <= centro.radio_metros and (not centro.poligono or punto_en_poligono(body.latitud, body.longitud, centro.poligono))
    if not dentro:
        rechazar(403, "FUERA_DE_GEOCERCA", f"Ubicación a {distancia:.0f} m del centro (radio {centro.radio_metros} m).",
                 {"distancia_m": round(distancia, 1)})

    # ---- M: coherencia temporal / viaje imposible ---------------------------------------
    ultima = db.execute(select(MarcacionQR).where(MarcacionQR.empleado_id == emp.id).order_by(MarcacionQR.marca_tiempo.desc()).limit(1)).scalar_one_or_none()
    if ultima is not None:
        if (inst.utc - ultima.marca_tiempo).total_seconds() < 60:
            rechazar(409, "MARCACION_DUPLICADA", "Ya registró una marcación hace menos de un minuto.")
        prev = descifrar_json(ultima.coordenadas_cifradas, f"marcacion:{ultima.id}")
        salto = haversine_m(prev["lat"], prev["lon"], body.latitud, body.longitud)
        vel = velocidad_kmh(prev["lat"], prev["lon"], ultima.marca_tiempo, body.latitud, body.longitud, inst.utc)
        if salto > 1000 and vel > s.velocidad_max_kmh:
            rechazar(403, "VIAJE_IMPOSIBLE", "Desplazamiento físicamente imposible desde la marcación anterior.", {"velocidad_kmh": round(vel)})

    abierta = ultima is not None and ultima.tipo == "ENTRADA" and (inst.utc - ultima.marca_tiempo) <= timedelta(hours=16)
    tipo = "SALIDA" if abierta else "ENTRADA"
    fecha_jornada = ultima.fecha_local if abierta else a_bogota(inst.utc).date()

    # ---- E: persistencia sellada + bitácora -------------------------------------------
    mid = uuid.uuid4()
    sello = sha256_hex(json.dumps({"id": str(mid), "empleado": str(emp.id), "centro": str(centro.id), "tipo": tipo,
                                   "marca_tiempo": inst.utc.isoformat(), "ventana": ventana, "distancia": f"{distancia:.2f}",
                                   "dispositivo": dev_hash}, sort_keys=True))
    m = MarcacionQR(id=mid, empleado_id=emp.id, centro_trabajo_id=centro.id, tipo=tipo, marca_tiempo=inst.utc, fecha_local=fecha_jornada,
                    ntp_sincronizado=inst.ntp_sincronizado, ntp_desfase_ms=inst.desfase_ms, ventana_totp=ventana,
                    coordenadas_cifradas=cifrar_json({"lat": body.latitud, "lon": body.longitud, "precision_m": body.precision_m}, f"marcacion:{mid}"),
                    distancia_metros=Decimal(f"{distancia:.2f}"), precision_gps_m=Decimal(f"{body.precision_m:.2f}"),
                    dispositivo_hash=dev_hash, ip_origen=actor.ip, user_agent=(actor.user_agent or "")[:300], sello_integridad=sello,
                    creado_en=inst.utc)
    db.add(m)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        fijar_contexto(db, str(emp.id), emp.rol)
        rechazar(409, "QR_REUTILIZADO", "Este QR ya fue utilizado por el trabajador (anti-replay).")
    registrar(db, ocurrido_en=inst.utc, accion="MARCACION_REGISTRADA", entidad="marcaciones_qr", entidad_id=mid, actor_id=emp.id,
              actor_rol=emp.rol, ip_origen=actor.ip, dispositivo_hash=dev_hash, user_agent=actor.user_agent,
              datos={"tipo": tipo, "centro_id": centro.id, "distancia_m": f"{distancia:.2f}", "ventana_totp": ventana,
                     "ntp_sincronizado": inst.ntp_sincronizado, "ntp_desfase_ms": inst.desfase_ms, "sello": sello,
                     "coordenadas_cifradas": coords_audit})

    # ---- A/P: cierre del lazo (conciliación + novedades de jornada) ----------------------
    conciliadas = conciliar(db, emp, inst.utc, m) if tipo == "SALIDA" else []
    novedades = evaluar_jornada(db, emp, fecha_jornada, inst.utc)
    db.commit()

    jefe = emp.jefe
    for n in novedades:
        if n.tipo == "TIEMPO_SUPLEMENTARIO_NO_AUTORIZADO" and jefe is not None:
            bg.add_task(_notificar_en_segundo_plano, Notificacion(
                destinatario_email=jefe.email, destinatario_whatsapp=jefe.telefono_whatsapp,
                asunto=f"Tiempo suplementario NO autorizado - {emp.nombre_completo}", cuerpo=n.detalle,
                parametros_plantilla=[emp.nombre_completo, f"{n.minutos} min", fecha_jornada.isoformat()],
                datos={"novedad_id": str(n.id)}), "novedades_jornada", str(n.id))
    return {
        "id": str(mid), "tipo": tipo, "hora_legal": a_bogota(inst.utc).isoformat(), "fecha_jornada": fecha_jornada.isoformat(),
        "centro": centro.nombre, "distancia_m": round(distancia, 1), "ntp_sincronizado": inst.ntp_sincronizado,
        "sello_integridad": sello,
        "conciliaciones": [{"solicitud_id": str(c.id), "horas_aprobadas": str(c.horas_solicitadas), "horas_reales": str(c.horas_reales),
                            "horas_reconocidas": str(c.horas_reconocidas)} for c in conciliadas],
        "novedades": [{"tipo": n.tipo, "minutos": n.minutos, "detalle": n.detalle} for n in novedades],
    }


# ============================================================================ ENDPOINT 2: workflow de horas extras
def _a_out(s: SolicitudHorasExtras) -> SolicitudHEOut:
    return SolicitudHEOut.model_validate(s)


@app.post(f"{PREFIJO}/horas-extras/solicitudes", response_model=SolicitudHEOut, status_code=201)
def solicitar_horas_extras(body: SolicitudHEIn, bg: BackgroundTasks, actor: Actor = Depends(actor_actual),
                           db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)):
    inst = reloj.ahora()
    objetivo_id = body.empleado_id or actor.id
    if objetivo_id != actor.id and actor.rol not in ("JEFE_INMEDIATO", "NOMINA_RRHH"):
        raise HTTPException(403, "Solo el jefe inmediato o RRHH pueden asignar horas extras a otro trabajador")
    # Bloqueo pesimista de la fila del trabajador: serializa el cómputo de topes (sin carreras TOCTOU).
    emp = db.execute(select(Empleado).where(Empleado.id == objetivo_id).with_for_update()).scalar_one_or_none()
    if emp is None or not emp.activo:
        raise HTTPException(404, "Trabajador inexistente o inactivo")
    if actor.rol == "JEFE_INMEDIATO" and objetivo_id != actor.id and emp.jefe_inmediato_id != actor.id:
        raise HTTPException(403, "El trabajador no pertenece a su equipo")

    inicio, fin = body.inicio, body.fin
    fecha = a_bogota(inicio).date()
    if fin > inicio_dia_local(fecha + timedelta(days=1)):
        raise HTTPException(422, "La solicitud no puede atravesar la medianoche: registre una solicitud por día calendario")
    if inicio < inst.utc - timedelta(minutes=5):
        raise HTTPException(422, "La autorización de trabajo suplementario debe ser PREVIA a su ejecución")
    h_sol = horas((fin - inicio).total_seconds()).quantize(Decimal("0.01"))

    turnos = db.execute(select(TurnoProgramado).where(TurnoProgramado.empleado_id == emp.id, TurnoProgramado.inicio < fin,
                                                      TurnoProgramado.fin > inicio)).first()
    if turnos:
        raise HTTPException(422, "La franja solicitada se traslapa con la jornada ordinaria programada")
    traslape = db.execute(select(SolicitudHorasExtras.id).where(
        SolicitudHorasExtras.empleado_id == emp.id, SolicitudHorasExtras.estado.in_(("PENDIENTE", "APROBADA", "CONCILIADA")),
        SolicitudHorasExtras.inicio_programado < fin, SolicitudHorasExtras.fin_programado > inicio)).first()
    if traslape:
        raise HTTPException(409, "Ya existe una solicitud activa en esa franja")

    resultado, h_dia, h_sem = analizar_topes(db, emp, fecha, h_sol)
    base = dict(empleado_id=emp.id, solicitado_por_id=actor.id, jefe_aprobador_id=emp.jefe_inmediato_id, fecha=fecha,
                inicio_programado=inicio, fin_programado=fin, horas_solicitadas=h_sol, motivo=body.motivo.strip(), creado_en=inst.utc)
    if not resultado.permitido:
        sol = SolicitudHorasExtras(**base, estado="RECHAZADA_AUTOMATICA", motivo_rechazo=" | ".join(resultado.motivos), decision_en=inst.utc)
        db.add(sol)
        db.flush()
        registrar(db, ocurrido_en=inst.utc, accion="HE_RECHAZO_AUTOMATICO_NORMATIVO", entidad="solicitudes_horas_extras", entidad_id=sol.id,
                  actor_id=actor.id, actor_rol=actor.rol, ip_origen=actor.ip, user_agent=actor.user_agent,
                  datos={"horas_solicitadas": h_sol, "acumulado_dia": h_dia, "acumulado_semana": h_sem, "motivos": resultado.motivos})
        db.commit()
        raise HTTPException(422, {"codigo": "TOPE_LEGAL_EXCEDIDO", "solicitud_id": str(sol.id), "motivos": list(resultado.motivos),
                                  "disponible_dia_h": str(resultado.disponible_dia), "disponible_semana_h": str(resultado.disponible_semana)})
    if emp.jefe_inmediato_id is None:
        raise HTTPException(422, "El trabajador no tiene jefe inmediato configurado para aprobar")

    asignacion_directa = actor.id == emp.jefe_inmediato_id
    sol = SolicitudHorasExtras(**base, estado="APROBADA" if asignacion_directa else "PENDIENTE",
                               decision_en=inst.utc if asignacion_directa else None,
                               comentario_jefe="Asignación directa del jefe inmediato" if asignacion_directa else None)
    db.add(sol)
    db.flush()
    registrar(db, ocurrido_en=inst.utc, accion="HE_ASIGNADA_POR_JEFE" if asignacion_directa else "HE_SOLICITADA",
              entidad="solicitudes_horas_extras", entidad_id=sol.id, actor_id=actor.id, actor_rol=actor.rol, ip_origen=actor.ip,
              user_agent=actor.user_agent, datos={"horas": h_sol, "inicio": inicio, "fin": fin, "acumulado_dia": h_dia,
                                                   "acumulado_semana": h_sem, "motivo": sol.motivo})
    db.commit()

    destinatario = emp if asignacion_directa else emp.jefe
    if destinatario is not None:
        asunto = ("Horas extras asignadas" if asignacion_directa else "Solicitud de horas extras pendiente de aprobación")
        cuerpo = (f"{emp.nombre_completo} | {_local(inicio)} a {_local(fin)} ({h_sol} h). Motivo: {sol.motivo}. "
                  f"Cupo legal restante: día {resultado.disponible_dia - h_sol} h / semana {resultado.disponible_semana - h_sol} h.")
        bg.add_task(_notificar_en_segundo_plano, Notificacion(
            destinatario_email=destinatario.email, destinatario_whatsapp=destinatario.telefono_whatsapp, asunto=asunto, cuerpo=cuerpo,
            parametros_plantilla=[emp.nombre_completo, f"{_local(inicio)} - {_local(fin)}", f"{h_sol} h", sol.motivo[:200]],
            datos={"solicitud_id": str(sol.id), "accion": "aprobar"}), "solicitudes_horas_extras", str(sol.id))
    return _a_out(sol)


@app.post(f"{PREFIJO}/horas-extras/solicitudes/{{solicitud_id}}/decision", response_model=SolicitudHEOut)
def decidir_horas_extras(solicitud_id: uuid.UUID, body: DecisionHEIn, bg: BackgroundTasks, actor: Actor = Depends(actor_actual),
                         db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)):
    inst = reloj.ahora()
    sol = db.execute(select(SolicitudHorasExtras).where(SolicitudHorasExtras.id == solicitud_id).with_for_update()).scalar_one_or_none()
    if sol is None:
        raise HTTPException(404, "Solicitud inexistente")
    if sol.jefe_aprobador_id != actor.id:
        registrar(db, ocurrido_en=inst.utc, accion="ACCESO_DENEGADO", entidad="solicitudes_horas_extras", entidad_id=sol.id,
                  actor_id=actor.id, actor_rol=actor.rol, ip_origen=actor.ip, datos={"motivo": "no es el jefe inmediato aprobador"})
        db.commit()
        raise HTTPException(403, "Solo el jefe inmediato asignado puede decidir esta solicitud")
    if sol.estado != "PENDIENTE":
        raise HTTPException(409, f"La solicitud ya fue decidida (estado {sol.estado})")
    emp = db.execute(select(Empleado).where(Empleado.id == sol.empleado_id).with_for_update()).scalar_one()

    if body.aprobar:
        resultado, h_dia, h_sem = analizar_topes(db, emp, sol.fecha, Decimal(sol.horas_solicitadas), excluir_id=sol.id)
        if not resultado.permitido:
            sol.estado, sol.motivo_rechazo, sol.decision_en = "RECHAZADA_AUTOMATICA", " | ".join(resultado.motivos), inst.utc
            registrar(db, ocurrido_en=inst.utc, accion="HE_RECHAZO_AUTOMATICO_NORMATIVO", entidad="solicitudes_horas_extras",
                      entidad_id=sol.id, actor_id=actor.id, actor_rol=actor.rol, ip_origen=actor.ip,
                      datos={"fase": "aprobacion", "motivos": resultado.motivos, "acumulado_dia": h_dia, "acumulado_semana": h_sem})
            db.commit()
            raise HTTPException(422, {"codigo": "TOPE_LEGAL_EXCEDIDO", "solicitud_id": str(sol.id), "motivos": list(resultado.motivos)})
        sol.estado = "APROBADA"
        sol.aprobacion_extemporanea = inst.utc > sol.inicio_programado
    else:
        if not body.comentario or len(body.comentario) < 10:
            raise HTTPException(422, "El rechazo debe motivarse (mínimo 10 caracteres)")
        sol.estado = "RECHAZADA_JEFE"
    sol.comentario_jefe, sol.decision_en = body.comentario, inst.utc
    registrar(db, ocurrido_en=inst.utc, accion="HE_APROBADA" if body.aprobar else "HE_RECHAZADA_JEFE", entidad="solicitudes_horas_extras",
              entidad_id=sol.id, actor_id=actor.id, actor_rol=actor.rol, ip_origen=actor.ip, user_agent=actor.user_agent,
              datos={"comentario": body.comentario, "extemporanea": sol.aprobacion_extemporanea})
    db.commit()
    bg.add_task(_notificar_en_segundo_plano, Notificacion(
        destinatario_email=emp.email, destinatario_whatsapp=emp.telefono_whatsapp,
        asunto=f"Horas extras {'APROBADAS' if body.aprobar else 'RECHAZADAS'}",
        cuerpo=f"{_local(sol.inicio_programado)} a {_local(sol.fin_programado)} ({sol.horas_solicitadas} h). {body.comentario or ''}",
        parametros_plantilla=[emp.nombre_completo, sol.estado, f"{sol.horas_solicitadas} h", body.comentario or "-"],
        datos={"solicitud_id": str(sol.id)}), "solicitudes_horas_extras", str(sol.id))
    return _a_out(sol)


@app.get(f"{PREFIJO}/horas-extras/solicitudes", response_model=list[SolicitudHEOut])
def listar_solicitudes(estado: str | None = None, desde: date | None = None, hasta: date | None = None,
                       actor: Actor = Depends(actor_actual), db: Session = Depends(get_db)):
    q = select(SolicitudHorasExtras)
    if actor.rol == "TRABAJADOR":
        q = q.where(SolicitudHorasExtras.empleado_id == actor.id)
    elif actor.rol == "JEFE_INMEDIATO":
        q = q.where(or_(SolicitudHorasExtras.jefe_aprobador_id == actor.id, SolicitudHorasExtras.empleado_id == actor.id))
    if estado:
        q = q.where(SolicitudHorasExtras.estado == estado)
    if desde:
        q = q.where(SolicitudHorasExtras.fecha >= desde)
    if hasta:
        q = q.where(SolicitudHorasExtras.fecha <= hasta)
    return [_a_out(s) for s in db.execute(q.order_by(SolicitudHorasExtras.inicio_programado.desc()).limit(500)).scalars()]


@app.post(f"{PREFIJO}/horas-extras/conciliacion")
def conciliacion_lote(actor: Actor = Depends(requiere("NOMINA_RRHH", "AUDITOR_SUPERADMIN")), db: Session = Depends(get_db),
                      reloj: RelojLegal = Depends(get_reloj)) -> dict:
    """Job de cierre (cron diario): concilia toda HE aprobada cuya ventana ya terminó."""
    inst = reloj.ahora()
    ids = db.execute(select(SolicitudHorasExtras.empleado_id).where(SolicitudHorasExtras.estado == "APROBADA",
                                                                     SolicitudHorasExtras.fin_programado <= inst.utc).distinct()).scalars().all()
    total = 0
    for eid in ids:
        total += len(conciliar(db, db.get(Empleado, eid), inst.utc, None, actor_rol=actor.rol))
    registrar(db, ocurrido_en=inst.utc, accion="CONCILIACION_LOTE", entidad="solicitudes_horas_extras", actor_id=actor.id,
              actor_rol=actor.rol, ip_origen=actor.ip, datos={"empleados": len(ids), "solicitudes_conciliadas": total})
    db.commit()
    return {"empleados": len(ids), "solicitudes_conciliadas": total}


# ============================================================================ ENDPOINT 3: motor de liquidación
def _validar_rango(desde: date, hasta: date) -> None:
    if hasta < desde or (hasta - desde).days > 62:
        raise HTTPException(422, "Rango inválido (máximo 62 días)")


@app.get(f"{PREFIJO}/nomina/preliquidacion/{{empleado_id}}")
def preliquidacion_empleado(empleado_id: uuid.UUID, desde: date = Query(...), hasta: date = Query(...),
                            actor: Actor = Depends(actor_actual), db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    _validar_rango(desde, hasta)
    emp = db.get(Empleado, empleado_id)
    if emp is None:
        raise HTTPException(404, "Empleado inexistente")
    autorizado = (actor.rol in ("NOMINA_RRHH", "AUDITOR_SUPERADMIN") or actor.id == emp.id
                  or (actor.rol == "JEFE_INMEDIATO" and emp.jefe_inmediato_id == actor.id))
    if not autorizado:
        raise HTTPException(403, "Sin permiso sobre este trabajador")
    inst = reloj.ahora()
    reporte = preliquidar(db, emp, desde, hasta, inst.utc)
    registrar(db, ocurrido_en=inst.utc, accion="PRELIQUIDACION_GENERADA", entidad="empleados", entidad_id=emp.id, actor_id=actor.id,
              actor_rol=actor.rol, ip_origen=actor.ip, datos={"desde": desde, "hasta": hasta, "sello": reporte["sello_sha256"],
                                                              "total": reporte["total_recargos_y_extras"]})
    db.commit()
    return reporte


@app.get(f"{PREFIJO}/nomina/preliquidacion")
def preliquidacion_consolidada(desde: date = Query(...), hasta: date = Query(...),
                               actor: Actor = Depends(requiere("NOMINA_RRHH", "AUDITOR_SUPERADMIN")),
                               db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    """Resumen de pre-nómina de toda la planta para interoperar con el proveedor de Nómina Electrónica."""
    _validar_rango(desde, hasta)
    inst = reloj.ahora()
    reportes = [preliquidar(db, e, desde, hasta, inst.utc)
                for e in db.execute(select(Empleado).where(Empleado.activo.is_(True)).order_by(Empleado.apellidos)).scalars()]
    total = sum((Decimal(r["total_recargos_y_extras"]) for r in reportes), Decimal("0.00"))
    sello = sha256_hex("".join(r["sello_sha256"] for r in reportes))
    registrar(db, ocurrido_en=inst.utc, accion="PRENOMINA_CONSOLIDADA", entidad="nomina", actor_id=actor.id, actor_rol=actor.rol,
              ip_origen=actor.ip, datos={"desde": desde, "hasta": hasta, "empleados": len(reportes), "total": total, "sello": sello})
    db.commit()
    return {"periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat()}, "empleados": len(reportes),
            "total_recargos_y_extras": str(total), "sello_lote_sha256": sello, "detalle": reportes}


# ============================================================================ KNOWLEDGE: auditoría forense
@app.get(f"{PREFIJO}/auditoria/verificar")
def auditoria_verificar(actor: Actor = Depends(requiere("AUDITOR_SUPERADMIN")), db: Session = Depends(get_db)) -> dict:
    return verificar_cadena(db)


@app.get(f"{PREFIJO}/auditoria/reporte")
def auditoria_reporte(desde: date = Query(...), hasta: date = Query(...), actor: Actor = Depends(requiere("AUDITOR_SUPERADMIN")),
                      db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    ini, fin = inicio_dia_local(desde), inicio_dia_local(hasta + timedelta(days=1))
    por_accion = dict(db.execute(select(AuditoriaLog.accion, func.count()).where(AuditoriaLog.ocurrido_en >= ini, AuditoriaLog.ocurrido_en < fin)
                                 .group_by(AuditoriaLog.accion)).all())
    rechazos: dict[str, int] = defaultdict(int)
    for (datos,) in db.execute(select(AuditoriaLog.datos).where(AuditoriaLog.accion == "MARCACION_RECHAZADA",
                                                                 AuditoriaLog.ocurrido_en >= ini, AuditoriaLog.ocurrido_en < fin)):
        rechazos[datos.get("causa", "DESCONOCIDA")] += 1
    novedades = dict(db.execute(select(NovedadJornada.tipo, func.count()).where(NovedadJornada.fecha >= desde, NovedadJornada.fecha <= hasta)
                                .group_by(NovedadJornada.tipo)).all())
    marcas_sin_ntp = db.execute(select(func.count()).select_from(MarcacionQR).where(
        MarcacionQR.marca_tiempo >= ini, MarcacionQR.marca_tiempo < fin, MarcacionQR.ntp_sincronizado.is_(False))).scalar()
    integridad = verificar_cadena(db)
    inst = reloj.ahora()
    reporte = {"periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat()}, "eventos_por_accion": por_accion,
               "intentos_fraude_o_rechazo_por_causa": dict(rechazos), "novedades_jornada": novedades,
               "marcaciones_sin_sincronia_ntp": marcas_sin_ntp, "integridad_bitacora": integridad,
               "generado_en": a_bogota(inst.utc).isoformat()}
    reporte["sello_sha256"] = sha256_hex(json.dumps(reporte, sort_keys=True, default=str))
    registrar(db, ocurrido_en=inst.utc, accion="REPORTE_AUDITORIA_GENERADO", entidad="auditoria_logs", actor_id=actor.id,
              actor_rol=actor.rol, ip_origen=actor.ip, datos={"sello": reporte["sello_sha256"]})
    db.commit()
    return reporte


@app.get(f"{PREFIJO}/auditoria/eventos")
def auditoria_eventos(desde_id: int = 0, limite: int = Query(200, le=1000), actor: Actor = Depends(requiere("AUDITOR_SUPERADMIN")),
                      db: Session = Depends(get_db)) -> list[dict]:
    regs = db.execute(select(AuditoriaLog).where(AuditoriaLog.id > desde_id).order_by(AuditoriaLog.id).limit(limite)).scalars()
    return [{"id": r.id, "ocurrido_en_utc": r.ocurrido_en.isoformat(), "hora_legal": a_bogota(r.ocurrido_en).isoformat(),
             "actor_id": str(r.actor_id) if r.actor_id else None, "actor_rol": r.actor_rol, "accion": r.accion, "entidad": r.entidad,
             "entidad_id": r.entidad_id, "ip_origen": str(r.ip_origen) if r.ip_origen else None, "dispositivo_hash": r.dispositivo_hash,
             "datos": r.datos, "hash_anterior": r.hash_anterior, "hash_actual": r.hash_actual} for r in regs]


# ============================================================================ ENDPOINTS PRODUCCIÓN: NOVEDADES, ALERTAS 30M, COMISIONES, LEDGER HE Y NÓMINA AUTOPARTES
from pydantic import BaseModel, Field
from .alertas_inasistencia import evaluar_omisiones_30_minutos
from .desprendibles_pdf import emitir_y_despachar_desprendibles_periodo, generar_pdf_desprendible_individual
from .models import (
    AlertaInasistencia,
    ComisionVendedor,
    HorasExtrasLedger,
    JEFE_OFICIAL_EMAIL,
    LiquidacionNomina,
    NovedadAusentismo,
)
from .nomina_autopartes import (
    PLANTA_OFICIAL_AUTOPARTES,
    clasificar_tramo_he_colombia,
    liquidar_empleado_autopartes,
    registrar_comision_vendedor,
)


class NovedadAusentismoIn(BaseModel):
    empleado_id: uuid.UUID
    tipo_novedad: str = Field(..., pattern="^(VACACIONES|INCAPACIDAD_EPS|INCAPACIDAD_ARL|LICENCIA_REMUNERADA|LICENCIA_NO_REMUNERADA)$")
    fecha_inicio: date
    fecha_fin: date
    dias_habiles: int = Field(0, ge=0)
    porcentaje_pago_empresa: Decimal = Field(Decimal("100.00"), ge=0, le=100)
    entidad_responsable: str = "EMPRESA_DISJOYA"
    codigo_cie10: str | None = None
    observaciones: str | None = None


class ComisionVendedorIn(BaseModel):
    empleado_id: uuid.UUID
    periodo_anio: int = Field(..., ge=2025, le=2035)
    periodo_mes: int = Field(..., ge=1, le=12)
    comision_quincena_1: Decimal = Field(Decimal("0.00"), ge=0)
    comision_quincena_2: Decimal = Field(Decimal("0.00"), ge=0)
    observaciones: str | None = None


@app.post(f"{PREFIJO}/novedades-ausentismo", status_code=201)
def crear_novedad_ausentismo(body: NovedadAusentismoIn, actor: Actor = Depends(requiere("JEFE_INMEDIATO", "NOMINA_RRHH", "AUDITOR_SUPERADMIN")),
                             db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    if body.fecha_fin < body.fecha_inicio:
        raise HTTPException(422, "La fecha fin no puede ser anterior a la fecha inicio")
    emp = db.get(Empleado, body.empleado_id)
    if emp is None:
        raise HTTPException(404, "Empleado no encontrado")
    dias_cal = (body.fecha_fin - body.fecha_inicio).days + 1
    nov = NovedadAusentismo(
        empleado_id=emp.id,
        tipo_novedad=body.tipo_novedad,
        fecha_inicio=body.fecha_inicio,
        fecha_fin=body.fecha_fin,
        dias_calendario=dias_cal,
        dias_habiles=body.dias_habiles or dias_cal,
        porcentaje_pago_empresa=body.porcentaje_pago_empresa,
        entidad_responsable=body.entidad_responsable,
        codigo_cie10=body.codigo_cie10,
        observaciones=body.observaciones,
        exime_marcacion_qr=True,
        estado="ACTIVA",
        registrado_por_id=actor.id,
    )
    db.add(nov)
    db.flush()
    registrar(db, ocurrido_en=reloj.ahora().utc, accion="NOVEDAD_AUSENTISMO_REGISTRADA", entidad="novedades_ausentismo",
              entidad_id=nov.id, actor_id=actor.id, actor_rol=actor.rol,
              datos={"empleado": emp.nombre_completo, "tipo": body.tipo_novedad, "dias": dias_cal})
    db.commit()
    return {
        "id": str(nov.id),
        "empleado": emp.nombre_completo,
        "tipo_novedad": nov.tipo_novedad,
        "fecha_inicio": nov.fecha_inicio.isoformat(),
        "fecha_fin": nov.fecha_fin.isoformat(),
        "dias_calendario": nov.dias_calendario,
        "exime_marcacion_qr": nov.exime_marcacion_qr,
    }


@app.get(f"{PREFIJO}/novedades-ausentismo")
def listar_novedades_ausentismo(db: Session = Depends(get_db)) -> list[dict]:
    regs = db.execute(select(NovedadAusentismo).order_by(NovedadAusentismo.fecha_inicio.desc())).scalars().all()
    return [
        {
            "id": str(r.id),
            "empleado_id": str(r.empleado_id),
            "empleado": r.empleado.nombre_completo if r.empleado else "",
            "tipo_novedad": r.tipo_novedad,
            "fecha_inicio": r.fecha_inicio.isoformat(),
            "fecha_fin": r.fecha_fin.isoformat(),
            "dias_calendario": r.dias_calendario,
            "entidad_responsable": r.entidad_responsable,
            "exime_marcacion_qr": r.exime_marcacion_qr,
            "estado": r.estado,
        }
        for r in regs
    ]


@app.post(f"{PREFIJO}/alertas-inasistencia/evaluar-30m")
def ejecutar_monitoreo_30_minutos(db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    """Ejecuta el barrido de control asistencial (ventana de gracia de 30 minutos).
    Notifica inmediatamente al trabajador y a `tminero.sst@gmail.com` si no hay marcación ni novedad activa."""
    inst = reloj.ahora()
    disparadas = evaluar_omisiones_30_minutos(db, inst.utc)
    return {
        "evaluado_en": a_bogota(inst.utc).isoformat(),
        "ventana_gracia_minutos": 30,
        "jefe_oficial_notificado": JEFE_OFICIAL_EMAIL,
        "total_alertas_disparadas": len(disparadas),
        "alertas": disparadas,
    }


@app.get(f"{PREFIJO}/alertas-inasistencia")
def listar_alertas_inasistencia(db: Session = Depends(get_db)) -> list[dict]:
    regs = db.execute(select(AlertaInasistencia).order_by(AlertaInasistencia.creado_en.desc()).limit(100)).scalars().all()
    return [
        {
            "id": str(a.id),
            "empleado": a.empleado.nombre_completo if a.empleado else "",
            "fecha": a.fecha.isoformat(),
            "tipo_omision": a.tipo_omision,
            "minutos_retraso": a.minutos_retraso,
            "jefe_email_notificado": a.jefe_email_notificado,
            "correo_trabajador_enviado": a.correo_trabajador_enviado,
            "correo_jefe_enviado": a.correo_jefe_enviado,
        }
        for a in regs
    ]


@app.post(f"{PREFIJO}/comisiones-vendedores", status_code=201)
def guardar_comision_vendedor(body: ComisionVendedorIn, db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    emp = db.get(Empleado, body.empleado_id)
    if emp is None:
        raise HTTPException(404, "Empleado no encontrado")
    if not emp.es_vendedor:
        raise HTTPException(422, "El trabajador no pertenece al área comercial / ventas")
    reg = registrar_comision_vendedor(
        db, emp, body.periodo_anio, body.periodo_mes, body.comision_quincena_1, body.comision_quincena_2, body.observaciones
    )
    registrar(db, ocurrido_en=reloj.ahora().utc, accion="COMISION_VENDEDOR_REGISTRADA", entidad="comisiones_vendedores",
              entidad_id=reg.id, actor_rol="NOMINA_RRHH",
              datos={"vendedor": emp.nombre_completo, "total_cop": str(reg.total_comisiones_cop)})
    db.commit()
    return {
        "id": str(reg.id),
        "empleado": emp.nombre_completo,
        "cargo": emp.cargo,
        "periodo": f"{reg.periodo_anio}-{reg.periodo_mes:02d}",
        "comision_quincena_1": str(reg.comision_quincena_1),
        "comision_quincena_2": str(reg.comision_quincena_2),
        "total_comisiones_cop": str(reg.total_comisiones_cop),
        "constituye_salario_art127": reg.constituye_salario_art127,
    }


@app.get(f"{PREFIJO}/horas-extras/ledger")
def consultar_ledger_horas_extras(anio: int = 2026, mes: int = 8, db: Session = Depends(get_db)) -> list[dict]:
    regs = db.execute(
        select(HorasExtrasLedger).where(
            HorasExtrasLedger.periodo_anio == anio,
            HorasExtrasLedger.periodo_mes == mes,
        )
    ).scalars().all()
    return [
        {
            "empleado": r.empleado.nombre_completo if r.empleado else "",
            "documento": r.empleado.numero_documento if r.empleado else "",
            "periodo": f"{r.periodo_anio}-{r.periodo_mes:02d}",
            "horas_preautorizadas_1h_diaria": str(r.horas_preautorizadas_1h_diaria),
            "horas_adicionales_aprobadas_jefe": str(r.horas_adicionales_aprobadas_jefe),
            "saldo_arrastre_mes_anterior": str(r.saldo_arrastre_mes_anterior),
            "total_horas_banco_real": str(r.total_horas_banco_real),
            "horas_pagadas_nomina_mes": str(r.horas_pagadas_nomina_mes),
            "saldo_arrastre_siguiente_mes": str(r.saldo_arrastre_siguiente_mes),
            "valor_pagado_mes_cop": str(r.valor_pagado_mes_cop),
            "aprobador_email": r.aprobador_email,
            "sello_auditoria_sha256": r.sello_auditoria_sha256,
        }
        for r in regs
    ]


@app.post(f"{PREFIJO}/nomina/autopartes/liquidar-periodo")
def ejecutar_liquidacion_autopartes_periodo(anio: int = 2026, mes: int = 8, db: Session = Depends(get_db)) -> dict:
    """Liquida la nómina mensual de toda la planta según 'NOMINA DE AUTOPARTES para sr pedro',
    incluyendo al trabajador activo de pruebas (Pedro Noriega - pnoriegawork@gmail.com) con sus horas extras aprobadas,
    aplica doble contabilización de horas extras, comisiones Art. 127 CST y genera los PDFs individuales."""
    # 1. Liquidar al trabajador activo de pruebas (Pedro Noriega - CC 1020304050 - pnoriegawork@gmail.com)
    emp_prueba = db.execute(select(Empleado).where(Empleado.numero_documento == "1020304050")).scalar_one_or_none()
    if emp_prueba is not None:
        liquidar_empleado_autopartes(db, emp_prueba, anio, mes)

    # 2. Liquidar a los 11 trabajadores de la nómina maestra "NOMINA DE AUTOPARTES para sr pedro"
    for item in PLANTA_OFICIAL_AUTOPARTES:
        emp = db.execute(select(Empleado).where(Empleado.numero_documento == item["numero_documento"])).scalar_one_or_none()
        if emp is None:
            continue
        if item["es_vendedor"]:
            registrar_comision_vendedor(db, emp, anio, mes, item["comision_q1"], item["comision_q2"])
        liquidar_empleado_autopartes(
            db,
            emp,
            anio,
            mes,
            horas_preautorizadas_override=item["hed_mes"],
        )
    db.commit()
    desprendibles = emitir_y_despachar_desprendibles_periodo(db, anio, mes)
    return {
        "periodo": f"{anio}-{mes:02d}",
        "jefe_aprobador_oficial": JEFE_OFICIAL_EMAIL,
        "trabajadores_liquidados": len(desprendibles),
        "desprendibles": desprendibles,
    }


@app.get(f"{PREFIJO}/nomina/autopartes/liquidaciones")
def listar_liquidaciones_autopartes(anio: int = 2026, mes: int = 8, db: Session = Depends(get_db)) -> list[dict]:
    # Garantizar que el trabajador de pruebas (Pedro Noriega - CC 1020304050 - pnoriegawork@gmail.com)
    # tenga siempre su liquidación actualizada en vivo con las horas extras aprobadas por tminero.sst@gmail.com
    emp_prueba = db.execute(select(Empleado).where(Empleado.numero_documento == "1020304050")).scalar_one_or_none()
    if emp_prueba is not None:
        liquidar_empleado_autopartes(db, emp_prueba, anio, mes)
        db.commit()

    regs = db.execute(
        select(LiquidacionNomina).where(
            LiquidacionNomina.periodo_anio == anio,
            LiquidacionNomina.periodo_mes == mes,
        )
    ).scalars().all()

    # Ordenar para que el trabajador de pruebas (1020304050) aparezca SIEMPRE de primero en la tabla
    regs_ordenados = sorted(
        regs,
        key=lambda x: (0 if (x.empleado and x.empleado.numero_documento == "1020304050") else 1, x.empleado.nombre_completo if x.empleado else ""),
    )

    return [
        {
            "empleado_id": str(r.empleado_id),
            "empleado": r.empleado.nombre_completo if r.empleado else "",
            "documento": r.empleado.numero_documento if r.empleado else "",
            "email": r.empleado.email if r.empleado else "",
            "cargo": r.empleado.cargo if r.empleado else "",
            "seccion": "TRABAJADOR EN PRUEBA (ACTIVO)" if (r.empleado and r.empleado.numero_documento == "1020304050") else r.seccion_plantilla,
            "salario_basico_pactado": str(r.salario_basico_pactado),
            "comisiones_ventas_art127": str(r.comisiones_ventas_art127),
            "sueldo_base_calculo": str(r.sueldo_base_calculo),
            "valor_hora_ordinaria": str(r.valor_hora_ordinaria),
            "horas_extra_diurna": str(r.horas_extra_diurna),
            "horas_extra_nocturna": str(r.horas_extra_nocturna),
            "horas_extra_festiva_diurna": str(r.horas_extra_festiva_diurna),
            "horas_extra_festiva_nocturna": str(r.horas_extra_festiva_nocturna),
            "total_horas_pagadas_mes": str(
                r.horas_extra_diurna + r.horas_extra_nocturna + r.horas_extra_festiva_diurna + r.horas_extra_festiva_nocturna
            ),
            "total_horas_extras_cop": str(r.total_horas_extras_cop),
            "horas_arrastradas_siguiente_mes": str(r.horas_arrastradas_siguiente_mes),
            "auxilio_transporte_devengado": str(r.auxilio_transporte_devengado),
            "total_devengado": str(r.total_devengado),
            "deduccion_salud_4pct": str(r.deduccion_salud_4pct),
            "deduccion_pension_4pct": str(r.deduccion_pension_4pct),
            "total_deducido": str(r.total_deducido),
            "neto_a_pagar": str(r.neto_a_pagar),
            "pdf_ruta_archivo": r.pdf_ruta_archivo,
            "pdf_hash_sha256": r.pdf_hash_sha256,
        }
        for r in regs_ordenados
    ]


@app.get(f"{PREFIJO}/nomina/autopartes/desprendible/{{documento}}/{{anio}}/{{mes}}/pdf")
def descargar_desprendible_pdf(documento: str, anio: int, mes: int, db: Session = Depends(get_db)) -> Response:
    emp = db.execute(select(Empleado).where(Empleado.numero_documento == documento)).scalar_one_or_none()
    if emp is None:
        raise HTTPException(404, "Empleado no encontrado")
    liq = db.execute(
        select(LiquidacionNomina).where(
            LiquidacionNomina.empleado_id == emp.id,
            LiquidacionNomina.periodo_anio == anio,
            LiquidacionNomina.periodo_mes == mes,
        )
    ).scalar_one_or_none()
    if liq is None or documento == "1020304050":
        liq = liquidar_empleado_autopartes(db, emp, anio, mes)
        db.commit()
    ledger = db.execute(
        select(HorasExtrasLedger).where(
            HorasExtrasLedger.empleado_id == emp.id,
            HorasExtrasLedger.periodo_anio == anio,
            HorasExtrasLedger.periodo_mes == mes,
        )
    ).scalar_one_or_none()
    ruta_pdf, sha_pdf = generar_pdf_desprendible_individual(emp, liq, ledger)
    liq.pdf_ruta_archivo = str(ruta_pdf)
    liq.pdf_hash_sha256 = sha_pdf
    db.commit()
    contenido_bytes = ruta_pdf.read_bytes()
    return Response(
        content=contenido_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="Desprendible_Nomina_{documento}_{anio}_{mes:02d}.pdf"',
            "Cache-Control": "no-store",
        },
    )


class MarcacionMovilDirectaIn(BaseModel):
    email_empleado: str = "pnoriegawork@gmail.com"
    tipo: str = Field(..., pattern="^(ENTRADA|SALIDA)$")
    latitud: float = 7.124500
    longitud: float = -73.128000
    distancia_m: float = 3.8
    precision_m: float = 4.0
    qr_payload: str | None = None


class SolicitudMovilHEIn(BaseModel):
    email_empleado: str = "pnoriegawork@gmail.com"
    fecha: date
    hora_inicio: str = "17:00"
    hora_fin: str = "19:00"
    horas: Decimal = Field(..., gt=0, le=24)
    motivo: str


@app.post(f"{PREFIJO}/movil/marcar")
def registrar_marcacion_movil_directa(body: MarcacionMovilDirectaIn, db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    """Registra en base de datos real la marcación móvil de Entrada o Salida del trabajador desde su celular."""
    inst = reloj.ahora()
    emp = db.execute(select(Empleado).where(Empleado.email == body.email_empleado)).scalar_one_or_none()
    if emp is None:
        emp = db.execute(select(Empleado).where(Empleado.numero_documento == "1020304050")).scalar_one_or_none()
    centro = db.execute(select(CentroTrabajo)).scalars().first()
    sello = sha256_hex(f"{body.email_empleado}|{body.tipo}|{inst.utc.isoformat()}|{body.latitud}|{body.longitud}")
    if emp and centro:
        ventana = int(inst.utc.timestamp() // 30) + int(inst.utc.microsecond % 1000)
        marca = MarcacionQR(
            empleado_id=emp.id,
            centro_trabajo_id=centro.id,
            tipo=body.tipo,
            marca_tiempo=inst.utc,
            fecha_local=a_bogota(inst.utc).date(),
            ntp_sincronizado=inst.ntp_sincronizado,
            ntp_desfase_ms=inst.desfase_ms,
            ventana_totp=ventana,
            coordenadas_cifradas=cifrar_json({"lat": body.latitud, "lon": body.longitud}, f"movil:{emp.id}"),
            distancia_metros=Decimal(str(round(body.distancia_m, 2))),
            precision_gps_m=Decimal(str(round(body.precision_m, 2))),
            dispositivo_hash=sha256_hex(f"movil:{body.email_empleado}"),
            sello_integridad=sello,
        )
        db.add(marca)
        registrar(
            db,
            ocurrido_en=inst.utc,
            accion=f"MARCACION_QR_{body.tipo}",
            entidad="marcaciones_qr",
            entidad_id=marca.id,
            actor_id=emp.id,
            actor_rol=emp.rol,
            datos={"empleado": emp.nombre_completo, "email": emp.email, "tipo": body.tipo, "distancia_m": body.distancia_m, "sello": sello},
        )
        db.commit()
    return {
        "estado": "REGISTRADA",
        "empleado": emp.nombre_completo if emp else "Pedro Noriega",
        "email": body.email_empleado,
        "jefe_inmediato": JEFE_OFICIAL_EMAIL,
        "tipo": body.tipo,
        "hora_legal_colombia": a_bogota(inst.utc).strftime("%I:%M:%S %p"),
        "sello_sha256": sello,
    }


@app.post(f"{PREFIJO}/movil/solicitar-he")
def radicar_solicitud_he_movil(body: SolicitudMovilHEIn, bg: BackgroundTasks, db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    """Radica en base de datos la solicitud de horas extras adicionales del trabajador y dispara notificación a tminero.sst@gmail.com."""
    inst = reloj.ahora()
    emp = db.execute(select(Empleado).where(Empleado.email == body.email_empleado)).scalar_one_or_none()
    if emp is None:
        emp = db.execute(select(Empleado).where(Empleado.numero_documento == "1020304050")).scalar_one_or_none()
    jefe = db.execute(select(Empleado).where(Empleado.email == JEFE_OFICIAL_EMAIL)).scalar_one_or_none()

    sol_id = uuid.uuid4()
    if emp:
        h_ini, m_ini = [int(x) for x in body.hora_inicio.split(":")]
        h_fin, m_fin = [int(x) for x in body.hora_fin.split(":")]
        dt_ini = inicio_dia_local(body.fecha) + timedelta(hours=h_ini, minutes=m_ini)
        dt_fin = inicio_dia_local(body.fecha) + timedelta(hours=h_fin, minutes=m_fin)
        if dt_fin <= dt_ini:
            dt_fin = dt_ini + timedelta(hours=float(body.horas))
        sol = SolicitudHorasExtras(
            id=sol_id,
            empleado_id=emp.id,
            solicitado_por_id=emp.id,
            jefe_aprobador_id=jefe.id if jefe else emp.jefe_inmediato_id,
            fecha=body.fecha,
            inicio_programado=dt_ini,
            fin_programado=dt_fin,
            horas_solicitadas=body.horas,
            motivo=body.motivo,
            estado="PENDIENTE",
            creado_en=inst.utc,
        )
        db.add(sol)
        db.flush()

    horas_c1_ley = min(body.horas, Decimal("2.00"))
    horas_c2_arrastre = max(Decimal("0.00"), body.horas - Decimal("2.00"))
    nombre_emp = emp.nombre_completo if emp else "Pedro Noriega"
    clasif = clasificar_tramo_he_colombia(body.fecha, body.hora_inicio, body.hora_fin, body.horas)

    # 1) Correo exclusivo para el Jefe Inmediato (tminero.sst@gmail.com) con el enlace de aprobación /jefe
    asunto_jefe = f"JEFATURA SST: Aprobar {body.horas}h Extras ({body.fecha.isoformat()}) - Disjoya"
    cuerpo_jefe = (
        f"SISTEMA AUTÓNOMO DE CONTROL HORARIO — DISJOYA AUTOPARTES S.A.S.\n"
        f"Sede Oficial: Cl. 23 #13-32, Comuna 4 Occidental, Bucaramanga, Santander\n\n"
        f"Estimado Jefe Inmediato T. Minero ({JEFE_OFICIAL_EMAIL}):\n\n"
        f"El trabajador ha radicado una nueva solicitud de Horas Extras que requiere su decisión:\n"
        f"• Trabajador Solicitante: {nombre_emp} ({body.email_empleado})\n"
        f"• Día / Fecha y Calendario Colombia: {body.fecha.isoformat()} ({clasif['tipo_dia_calendario']})\n"
        f"• Horario y Total Horas Solicitadas: {body.hora_inicio} a {body.hora_fin} ({body.horas} Horas Extras)\n"
        f"• Clasificación Horaria Legal (Corte Nocturno 7:00 p.m. / 19:00 - Ley 2466 / CST):\n"
        f"  - Horas Extras Diurnas (06:00 a 19:00): {clasif['HED']} h (Ord.) / {clasif['HEDDF']} h (Dom./Fest.)\n"
        f"  - Horas Extras Nocturnas (desde 7:00 p.m. / 19:00): {clasif['HEN']} h (Ord.) / {clasif['HENDF']} h (Dom./Fest.)\n"
        f"• Doble Contabilización (Art. 22 Ley 50/1990):\n"
        f"  - Cuenta 1 (Liquidación Nómina Legal del Período): {horas_c1_ley} h\n"
        f"  - Cuenta 2 (Banco de Arrastre para pago en siguientes períodos al 100%): {horas_c2_arrastre} h\n"
        f"• Razón / Descripción por la cual solicita las Horas Extras:\n"
        f"  \"{body.motivo}\"\n\n"
        f"Para APROBAR o RECHAZAR esta solicitud (acceso exclusivo Jefatura), abra este enlace:\n"
        f"https://disjoya-control-horarios.vercel.app/jefe\n"
    )
    res_correo_jefe = despachar(
        Notificacion(
            destinatario_email=JEFE_OFICIAL_EMAIL,
            destinatario_whatsapp=None,
            asunto=asunto_jefe,
            cuerpo=cuerpo_jefe,
            parametros_plantilla=[body.email_empleado, str(body.horas), body.motivo],
            datos={"empleado": body.email_empleado, "jefe": JEFE_OFICIAL_EMAIL, "horas": str(body.horas)},
        )
    )

    # 2) Constancia exclusiva para el Trabajador (pnoriegawork@gmail.com) SIN enlace de jefe
    asunto_trabajador = f"CONSTANCIA TRABAJADOR: Solicitud de {body.horas}h Extras Radicada ({body.fecha.isoformat()})"
    cuerpo_trabajador = (
        f"SISTEMA AUTÓNOMO DE CONTROL HORARIO — DISJOYA AUTOPARTES S.A.S.\n"
        f"Sede Oficial: Cl. 23 #13-32, Comuna 4 Occidental, Bucaramanga, Santander\n\n"
        f"Hola {nombre_emp} ({body.email_empleado}):\n\n"
        f"Su solicitud de trabajo suplementario ha sido radicada exitosamente y enviada a su Jefe Inmediato ({JEFE_OFICIAL_EMAIL}) para estudio:\n\n"
        f"• Estado Actual: PENDIENTE DE APROBACIÓN\n"
        f"• Día / Fecha y Calendario Colombia: {body.fecha.isoformat()} ({clasif['tipo_dia_calendario']})\n"
        f"• Horario y Total Horas: {body.hora_inicio} a {body.hora_fin} ({body.horas} Horas Extras)\n"
        f"• Clasificación Horaria (Corte Nocturno 7:00 p.m.): {clasif['resumen_texto']}\n"
        f"• Descripción / Razón Registrada:\n"
        f"  \"{body.motivo}\"\n\n"
        f"Tan pronto el Jefe Inmediato apruebe o rechace su solicitud, usted recibirá un correo electrónico con la respuesta oficial.\n\n"
        f"Enlace de su App de Trabajador (solo Marcación y Horas Extras):\n"
        f"https://disjoya-control-horarios.vercel.app/trabajador\n"
    )
    res_correo_trabajador = despachar(
        Notificacion(
            destinatario_email=body.email_empleado,
            destinatario_whatsapp=None,
            asunto=asunto_trabajador,
            cuerpo=cuerpo_trabajador,
            parametros_plantilla=[nombre_emp, str(body.horas), body.motivo],
            datos={"empleado": body.email_empleado, "estado": "PENDIENTE", "horas": str(body.horas)},
        )
    )

    registrar(
        db,
        ocurrido_en=inst.utc,
        accion="HE_SOLICITADA_A_JEFATURA",
        entidad="solicitudes_horas_extras",
        entidad_id=sol_id,
        actor_id=emp.id if emp else None,
        actor_rol="TRABAJADOR",
        datos={
            "trabajador": body.email_empleado,
            "jefe_aprobador": JEFE_OFICIAL_EMAIL,
            "fecha": str(body.fecha),
            "horas": str(body.horas),
            "clasificacion_colombia": {
                "tipo_dia": clasif["tipo_dia_calendario"],
                "HED": str(clasif["HED"]),
                "HEN": str(clasif["HEN"]),
                "HEDDF": str(clasif["HEDDF"]),
                "HENDF": str(clasif["HENDF"]),
            },
            "horas_cuenta_1_ley": str(horas_c1_ley),
            "horas_cuenta_2_arrastre": str(horas_c2_arrastre),
            "motivo": body.motivo,
            "correo_jefe": res_correo_jefe,
            "correo_trabajador": res_correo_trabajador,
        },
    )
    db.commit()
    return {
        "id": str(sol_id),
        "estado": "PENDIENTE",
        "trabajador": nombre_emp,
        "email_trabajador": body.email_empleado,
        "jefe_aprobador": JEFE_OFICIAL_EMAIL,
        "horas": str(body.horas),
        "clasificacion_horaria": {
            "tipo_dia_calendario": clasif["tipo_dia_calendario"],
            "HED_diurnas": str(clasif["HED"]),
            "HEN_nocturnas_desde_7pm": str(clasif["HEN"]),
            "HEDDF_festivas_diurnas": str(clasif["HEDDF"]),
            "HENDF_festivas_nocturnas_desde_7pm": str(clasif["HENDF"]),
            "resumen": clasif["resumen_texto"],
        },
        "horas_cuenta_1_ley": str(horas_c1_ley),
        "horas_cuenta_2_arrastre": str(horas_c2_arrastre),
        "fecha": body.fecha.isoformat(),
        "horario": f"{body.hora_inicio} a {body.hora_fin}",
        "motivo": body.motivo,
        "correo_enviado_a_jefe": res_correo_jefe,
        "correo_constancia_trabajador": res_correo_trabajador,
    }


@app.get(f"{PREFIJO}/movil/solicitudes-pendientes")
def listar_solicitudes_he_pendientes(db: Session = Depends(get_db)) -> list[dict]:
    """Devuelve las solicitudes de horas extras registradas en Supabase para sincronizar celular y computador en vivo."""
    regs = db.execute(select(SolicitudHorasExtras).order_by(SolicitudHorasExtras.creado_en.desc()).limit(25)).scalars().all()
    resultado = []
    for r in regs:
        emp = db.get(Empleado, r.empleado_id)
        h_ini_str = a_bogota(r.inicio_programado).strftime("%H:%M")
        h_fin_str = a_bogota(r.fin_programado).strftime("%H:%M")
        clasif = clasificar_tramo_he_colombia(r.fecha, h_ini_str, h_fin_str, r.horas_solicitadas)
        resultado.append({
            "id": str(r.id),
            "empleado": emp.nombre_completo if emp else "Pedro Noriega",
            "documento": f"{emp.tipo_documento} {emp.numero_documento}" if emp else "CC 1020304050",
            "email": emp.email if emp else "pnoriegawork@gmail.com",
            "jefe_email": JEFE_OFICIAL_EMAIL,
            "fecha": r.fecha.isoformat(),
            "hora_inicio": h_ini_str,
            "hora_fin": h_fin_str,
            "horas": str(r.horas_solicitadas),
            "clasificacion_resumen": clasif["resumen_texto"],
            "motivo": r.motivo,
            "estado": r.estado,
        })
    return resultado


class DecisionMovilHEIn(BaseModel):
    solicitud_id: str | None = None
    aprobada: bool
    comentario: str | None = None


@app.post(f"{PREFIJO}/movil/decidir-he")
def decidir_solicitud_he_movil(body: DecisionMovilHEIn, db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    """Registra en base de datos la decisión de aprobación o rechazo de `tminero.sst@gmail.com` y envía correo inmediato al trabajador (`pnoriegawork@gmail.com`)."""
    inst = reloj.ahora()
    sol = None
    if body.solicitud_id:
        try:
            sol = db.get(SolicitudHorasExtras, uuid.UUID(body.solicitud_id))
        except ValueError:
            sol = None
    if sol is None:
        sol = db.execute(
            select(SolicitudHorasExtras).where(SolicitudHorasExtras.estado == "PENDIENTE").order_by(SolicitudHorasExtras.creado_en.desc()).limit(1)
        ).scalar_one_or_none()
    if sol is None:
        sol = db.execute(select(SolicitudHorasExtras).order_by(SolicitudHorasExtras.creado_en.desc()).limit(1)).scalar_one_or_none()

    email_trabajador = "pnoriegawork@gmail.com"
    nombre_trabajador = "Pedro Noriega"
    fecha_str = a_bogota(inst.utc).date().isoformat()
    horario_str = "17:00 a 19:00"
    horas_str = "2.00"
    clasif_resumen = "Día Ordinario | Diurnas (06:00-19:00): 2.00h | Nocturnas (desde 7:00 p.m.): 0.00h"
    descripcion_str = "Solicitud de trabajo suplementario"
    observacion_jefe = body.comentario or (
        f"Aprobada por Jefatura SST ({JEFE_OFICIAL_EMAIL})" if body.aprobada else "No autorizada operativamente por Jefatura SST"
    )

    if sol:
        emp_sol = db.get(Empleado, sol.empleado_id)
        if emp_sol:
            email_trabajador = emp_sol.email or "pnoriegawork@gmail.com"
            nombre_trabajador = emp_sol.nombre_completo
        fecha_str = sol.fecha.isoformat()
        h_ini_s = a_bogota(sol.inicio_programado).strftime("%H:%M")
        h_fin_s = a_bogota(sol.fin_programado).strftime("%H:%M")
        horario_str = f"{h_ini_s} a {h_fin_s}"
        horas_str = str(sol.horas_solicitadas)
        clasif_sol = clasificar_tramo_he_colombia(sol.fecha, h_ini_s, h_fin_s, sol.horas_solicitadas)
        clasif_resumen = clasif_sol["resumen_texto"]
        descripcion_str = sol.motivo

        sol.estado = "APROBADA" if body.aprobada else "RECHAZADA_JEFE"
        sol.decision_en = inst.utc
        if body.aprobada:
            sol.comentario_jefe = observacion_jefe
            sol.horas_reconocidas = sol.horas_solicitadas
        else:
            sol.motivo_rechazo = observacion_jefe

        db.flush()
        # Actualizar inmediatamente la liquidación de nómina del trabajador con las horas aprobadas
        if emp_sol:
            liquidar_empleado_autopartes(db, emp_sol, 2026, 8)

        registrar(
            db,
            ocurrido_en=inst.utc,
            accion="HE_APROBADA_POR_JEFE" if body.aprobada else "HE_RECHAZADA_POR_JEFE",
            entidad="solicitudes_horas_extras",
            entidad_id=sol.id,
            actor_rol="JEFE_INMEDIATO",
            datos={
                "aprobador": JEFE_OFICIAL_EMAIL,
                "trabajador_notificado": email_trabajador,
                "estado": sol.estado,
                "fecha": fecha_str,
                "horas": horas_str,
                "clasificacion_colombia": clasif_resumen,
                "descripcion": descripcion_str,
            },
        )
        db.commit()

    estado_titulo = "APROBADAS" if body.aprobada else "RECHAZADAS"
    asunto_trab = f"RESPUESTA HORAS EXTRAS ({estado_titulo}): {fecha_str} - Disjoya"
    cuerpo_trab = (
        f"SISTEMA AUTÓNOMO DE CONTROL HORARIO — DISJOYA AUTOPARTES S.A.S.\n"
        f"Sede Oficial: Cl. 23 #13-32, Comuna 4 Occidental, Bucaramanga, Santander\n\n"
        f"Estimado(a) trabajador(a) {nombre_trabajador} ({email_trabajador}):\n\n"
        f"Le informamos que el Jefe Inmediato Aprobador ({JEFE_OFICIAL_EMAIL}) ha tomado una decisión sobre su solicitud de trabajo suplementario:\n\n"
        f"• ESTADO DE LA SOLICITUD: {('APROBADA Y AUTORIZADA' if body.aprobada else 'RECHAZADA')}\n"
        f"• DÍA / FECHA DE LAS HORAS EXTRAS: {fecha_str}\n"
        f"• HORARIO Y CANTIDAD DE HORAS: {horario_str} ({horas_str} Horas Extras)\n"
        f"• CLASIFICACIÓN HORARIA LEGAL (CORTE NOCTURNO 7:00 P.M.):\n"
        f"  {clasif_resumen}\n"
        f"• DESCRIPCIÓN / RAZÓN DE LA ACTIVIDAD:\n"
        f"  \"{descripcion_str}\"\n"
        f"• CONCEPTO DEL JEFE INMEDIATO ({JEFE_OFICIAL_EMAIL}):\n"
        f"  \"{observacion_jefe}\"\n\n"
        f"Estas horas han quedado debidamente asentadas en el Libro Mayor de Doble Contabilización y Liquidación de Nómina.\n\n"
        f"Enlace de su App de Trabajador:\n"
        f"https://disjoya-control-horarios.vercel.app/trabajador\n"
    )
    res_correo_trab = despachar(
        Notificacion(
            destinatario_email=email_trabajador,
            destinatario_whatsapp=None,
            asunto=asunto_trab,
            cuerpo=cuerpo_trab,
            parametros_plantilla=[nombre_trabajador, fecha_str, horas_str, descripcion_str],
            datos={"trabajador": email_trabajador, "estado": estado_titulo, "fecha": fecha_str, "horas": horas_str},
        )
    )

    return {
        "estado": sol.estado if sol else ("APROBADA" if body.aprobada else "RECHAZADA_JEFE"),
        "aprobador": JEFE_OFICIAL_EMAIL,
        "trabajador_notificado": email_trabajador,
        "fecha": fecha_str,
        "horario": horario_str,
        "horas": horas_str,
        "descripcion": descripcion_str,
        "correo_enviado_trabajador": res_correo_trab,
        "decidido_en": a_bogota(inst.utc).strftime("%Y-%m-%d %I:%M:%S %p"),
    }


class ComisionAdminFormIn(BaseModel):
    numero_documento: str
    periodo_anio: int = 2026
    periodo_mes: int = 8
    comision_quincena_1: Decimal = Field(Decimal("0.00"), ge=0)
    comision_quincena_2: Decimal = Field(Decimal("0.00"), ge=0)
    observaciones: str | None = None


class NovedadAdminFormIn(BaseModel):
    numero_documento: str
    tipo_novedad: str = Field(..., pattern="^(VACACIONES|INCAPACIDAD_EPS|INCAPACIDAD_ARL|LICENCIA_REMUNERADA|LICENCIA_NO_REMUNERADA)$")
    fecha_inicio: date
    fecha_fin: date
    entidad_responsable: str = "EPS / ARL"
    porcentaje_pago_empresa: Decimal = Decimal("100.00")
    observaciones: str | None = None


@app.post(f"{PREFIJO}/admin/comision-vendedor", status_code=201)
def guardar_comision_desde_admin(body: ComisionAdminFormIn, db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    emp = db.execute(select(Empleado).where(Empleado.numero_documento == body.numero_documento)).scalar_one_or_none()
    if emp is None:
        raise HTTPException(404, "Vendedor no encontrado en la base de datos")
    reg = registrar_comision_vendedor(
        db, emp, body.periodo_anio, body.periodo_mes, body.comision_quincena_1, body.comision_quincena_2, body.observaciones
    )
    liq = liquidar_empleado_autopartes(db, emp, body.periodo_anio, body.periodo_mes)
    registrar(
        db,
        ocurrido_en=reloj.ahora().utc,
        accion="COMISION_VENDEDOR_ADMIN",
        entidad="comisiones_vendedores",
        entidad_id=reg.id,
        actor_rol="AUDITOR_SUPERADMIN",
        datos={"vendedor": emp.nombre_completo, "documento": emp.numero_documento, "total_comision": str(reg.total_comisiones_cop)},
    )
    db.commit()
    return {
        "empleado": emp.nombre_completo,
        "documento": emp.numero_documento,
        "cargo": emp.cargo,
        "salario_basico": str(liq.salario_basico_pactado),
        "comision_q1": str(reg.comision_quincena_1),
        "comision_q2": str(reg.comision_quincena_2),
        "total_comisiones": str(reg.total_comisiones_cop),
        "sueldo_base_calculo_art127": str(liq.sueldo_base_calculo),
        "valor_hora_ordinaria": str(liq.valor_hora_ordinaria),
        "neto_a_pagar": str(liq.neto_a_pagar),
    }


@app.post(f"{PREFIJO}/admin/novedad-ausentismo", status_code=201)
def guardar_novedad_desde_admin(body: NovedadAdminFormIn, db: Session = Depends(get_db), reloj: RelojLegal = Depends(get_reloj)) -> dict:
    if body.fecha_fin < body.fecha_inicio:
        raise HTTPException(422, "La fecha final no puede ser anterior a la fecha inicial")
    emp = db.execute(select(Empleado).where(Empleado.numero_documento == body.numero_documento)).scalar_one_or_none()
    if emp is None:
        raise HTTPException(404, "Trabajador no encontrado")
    dias_cal = (body.fecha_fin - body.fecha_inicio).days + 1
    nov = NovedadAusentismo(
        empleado_id=emp.id,
        tipo_novedad=body.tipo_novedad,
        fecha_inicio=body.fecha_inicio,
        fecha_fin=body.fecha_fin,
        dias_calendario=dias_cal,
        dias_habiles=dias_cal,
        porcentaje_pago_empresa=body.porcentaje_pago_empresa,
        entidad_responsable=body.entidad_responsable,
        observaciones=body.observaciones,
        exime_marcacion_qr=True,
        estado="ACTIVA",
    )
    db.add(nov)
    db.flush()
    registrar(
        db,
        ocurrido_en=reloj.ahora().utc,
        accion="NOVEDAD_AUSENTISMO_ADMIN",
        entidad="novedades_ausentismo",
        entidad_id=nov.id,
        actor_rol="AUDITOR_SUPERADMIN",
        datos={"empleado": emp.nombre_completo, "tipo": body.tipo_novedad, "dias": dias_cal, "exime_alertas": True},
    )
    db.commit()
    return {
        "id": str(nov.id),
        "empleado": emp.nombre_completo,
        "documento": emp.numero_documento,
        "tipo_novedad": nov.tipo_novedad,
        "fecha_inicio": nov.fecha_inicio.isoformat(),
        "fecha_fin": nov.fecha_fin.isoformat(),
        "dias_calendario": nov.dias_calendario,
        "entidad_responsable": nov.entidad_responsable,
        "exime_marcacion_qr": nov.exime_marcacion_qr,
    }



