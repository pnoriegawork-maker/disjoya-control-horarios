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
from fastapi.responses import FileResponse
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

