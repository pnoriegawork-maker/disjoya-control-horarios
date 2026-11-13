import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from app import legal
from app.legal import TZ_BOGOTA, a_bogota
from app.models import Empleado
from app.security import (
    crear_jwt,
    descifrar,
    hash_password,
    nuevo_secreto_totp,
    ventana_totp,
    construir_payload_qr,
)
from app.database import nueva_sesion


def test_flujo_completo_api(entorno):
    client, reloj = entorno
    db = nueva_sesion(rol="SISTEMA")

    # 1. Crear Jefe Inmediato y Trabajador
    jefe_id = uuid.uuid4()
    emp_id = uuid.uuid4()
    cid = uuid.uuid4()

    jefe = Empleado(
        id=jefe_id, tipo_documento="CC", numero_documento="11111111", nombres="Carlos", apellidos="Jefe",
        email="jefe@empresa.com.co", rol="JEFE_INMEDIATO", cargo="Líder Operativo",
        salario_base=Decimal("4500000.00"), password_hash=hash_password("PasswordSegura123!")
    )
    db.add(jefe)
    db.commit()

    token_jefe = crear_jwt(jefe_id, "JEFE_INMEDIATO")
    headers_jefe = {"Authorization": f"Bearer {token_jefe}"}

    # 2. Crear SuperAdmin y sede operativa
    superadmin_id = uuid.uuid4()
    admin = Empleado(
        id=superadmin_id, tipo_documento="CC", numero_documento="99999999", nombres="Super", apellidos="Admin",
        email="admin@empresa.com.co", rol="AUDITOR_SUPERADMIN", cargo="Auditor General",
        salario_base=Decimal("9000000.00"), password_hash=hash_password("AdminSegura123!")
    )
    db.add(admin)
    db.commit()

    token_admin = crear_jwt(superadmin_id, "AUDITOR_SUPERADMIN")
    headers_admin = {"Authorization": f"Bearer {token_admin}"}

    # Crear Centro de Trabajo (Bogotá - Calle 72: 4.655828, -74.058342)
    resp_centro = client.post(
        "/api/v1/centros",
        headers=headers_admin,
        json={
            "nombre": "Sede Principal Financiera",
            "direccion": "Calle 72 # 10-07",
            "municipio_dane": "11001",
            "latitud": "4.655828",
            "longitud": "-74.058342",
            "radio_metros": 50
        }
    )
    assert resp_centro.status_code == 201
    centro_data = resp_centro.json()
    centro_id = centro_data["id"]
    kiosco_key = centro_data["kiosco_api_key"]

    # Crear Trabajador asignado al jefe y al centro
    resp_emp = client.post(
        "/api/v1/empleados",
        headers=headers_admin,
        json={
            "tipo_documento": "CC",
            "numero_documento": "1020304050",
            "nombres": "Pedro",
            "apellidos": "Noriega",
            "email": "pedro.noriega@empresa.com.co",
            "telefono_whatsapp": "573001234567",
            "rol": "TRABAJADOR",
            "cargo": "Desarrollador Full Stack",
            "jefe_inmediato_id": str(jefe_id),
            "centro_trabajo_id": centro_id,
            "salario_base": "2100000.00",
            "password": "PasswordSegura123!"
        }
    )
    assert resp_emp.status_code == 201
    emp_uuid = uuid.UUID(resp_emp.json()["id"])
    token_emp = crear_jwt(emp_uuid, "TRABAJADOR")
    headers_emp = {"Authorization": f"Bearer {token_emp}"}

    # 3. Habeas Data y Registro de Dispositivo
    # Aceptar política
    resp_habeas = client.post(
        "/api/v1/habeas-data/consentimiento",
        headers=headers_emp,
        json={
            "version_politica": "2026.11",
            "acepta_tratamiento_laboral": True,
            "acepta_geolocalizacion": True
        }
    )
    assert resp_habeas.status_code == 201

    # Registrar dispositivo móvil único
    device_uuid = uuid.uuid4()
    resp_dev = client.post(
        "/api/v1/dispositivos",
        headers=headers_emp,
        json={"device_uuid": str(device_uuid)}
    )
    assert resp_dev.status_code == 201

    # 4. Programar Turno: 08:00 a 17:00
    inicio_turno = datetime(2026, 11, 13, 8, 0, tzinfo=TZ_BOGOTA)
    fin_turno = datetime(2026, 11, 13, 17, 0, tzinfo=TZ_BOGOTA)
    resp_turno = client.post(
        "/api/v1/turnos",
        headers=headers_jefe,
        json={
            "empleado_id": str(emp_uuid),
            "inicio": inicio_turno.isoformat(),
            "fin": fin_turno.isoformat()
        }
    )
    assert resp_turno.status_code == 201

    # 5. Generación de QR dinámico (Kiosco) y Marcación de ENTRADA en tiempo real
    reloj.fijar(datetime(2026, 11, 13, 7, 58, 0, tzinfo=TZ_BOGOTA))
    resp_qr = client.get(
        f"/api/v1/qr/centros/{centro_id}/actual",
        headers={"X-Kiosk-Key": kiosco_key}
    )
    assert resp_qr.status_code == 200
    qr_payload = resp_qr.json()["payload"]

    # 6. Marcación de ENTRADA con Geocerca (10 segundos después, misma ventana TOTP)
    reloj.fijar(datetime(2026, 11, 13, 7, 58, 10, tzinfo=TZ_BOGOTA))
    resp_marca_in = client.post(
        "/api/v1/marcaciones",
        headers=headers_emp,
        json={
            "qr_payload": qr_payload,
            "device_uuid": str(device_uuid),
            "latitud": 4.655830,
            "longitud": -74.058340,
            "precision_m": 5.0,
            "ubicacion_simulada": False
        }
    )
    assert resp_marca_in.status_code == 201
    assert resp_marca_in.json()["tipo"] == "ENTRADA"

    # Intento de re-play del mismo QR por el mismo empleado -> debe fallar (409)
    resp_replay = client.post(
        "/api/v1/marcaciones",
        headers=headers_emp,
        json={
            "qr_payload": qr_payload,
            "device_uuid": str(device_uuid),
            "latitud": 4.655830,
            "longitud": -74.058340,
            "precision_m": 5.0,
            "ubicacion_simulada": False
        }
    )
    assert resp_replay.status_code in (409, 429)

    # 7. Solicitud de Horas Extras (17:00 a 19:00 = 2 horas)
    inicio_he = datetime(2026, 11, 13, 17, 0, tzinfo=TZ_BOGOTA)
    fin_he = datetime(2026, 11, 13, 19, 0, tzinfo=TZ_BOGOTA)
    resp_he = client.post(
        "/api/v1/horas-extras/solicitudes",
        headers=headers_emp,
        json={
            "inicio": inicio_he.isoformat(),
            "fin": fin_he.isoformat(),
            "motivo": "Entrega de cierre y balance de nómina mensual en sistemas"
        }
    )
    assert resp_he.status_code == 201
    he_id = resp_he.json()["id"]
    assert resp_he.json()["estado"] == "PENDIENTE"
    assert resp_he.json()["horas_solicitadas"] == "2.00"

    # 8. Intento de solicitar más horas el mismo día -> Debe violar tope diario (2h máx)
    inicio_he_invalida = datetime(2026, 11, 13, 19, 0, tzinfo=TZ_BOGOTA)
    fin_he_invalida = datetime(2026, 11, 13, 20, 0, tzinfo=TZ_BOGOTA)
    resp_he_invalida = client.post(
        "/api/v1/horas-extras/solicitudes",
        headers=headers_emp,
        json={
            "inicio": inicio_he_invalida.isoformat(),
            "fin": fin_he_invalida.isoformat(),
            "motivo": "Soporte adicional extendido fuera del tope legal"
        }
    )
    assert resp_he_invalida.status_code == 422
    assert "TOPE_LEGAL_EXCEDIDO" in resp_he_invalida.text

    # 9. Aprobación por parte del Jefe Inmediato
    resp_decision = client.post(
        f"/api/v1/horas-extras/solicitudes/{he_id}/decision",
        headers=headers_jefe,
        json={
            "aprobar": True,
            "comentario": "Autorizado por requerimiento de cierre operativo"
        }
    )
    assert resp_decision.status_code == 200
    assert resp_decision.json()["estado"] == "APROBADA"

    # 10. Marcación de SALIDA al terminar las horas extras (19:00)
    reloj.fijar(datetime(2026, 11, 13, 19, 0, tzinfo=TZ_BOGOTA))
    resp_qr_salida = client.get(
        f"/api/v1/qr/centros/{centro_id}/actual",
        headers={"X-Kiosk-Key": kiosco_key}
    )
    qr_payload_salida = resp_qr_salida.json()["payload"]

    resp_marca_out = client.post(
        "/api/v1/marcaciones",
        headers=headers_emp,
        json={
            "qr_payload": qr_payload_salida,
            "device_uuid": str(device_uuid),
            "latitud": 4.655832,
            "longitud": -74.058341,
            "precision_m": 6.0,
            "ubicacion_simulada": False
        }
    )
    assert resp_marca_out.status_code == 201
    assert resp_marca_out.json()["tipo"] == "SALIDA"
    # Verificar doble validación / conciliación automática
    conciliaciones = resp_marca_out.json()["conciliaciones"]
    assert len(conciliaciones) == 1
    assert conciliaciones[0]["solicitud_id"] == str(he_id)
    assert conciliaciones[0]["horas_reconocidas"] == "2.00"

    # 11. Pre-liquidación de Nómina Legal (13 de noviembre de 2026)
    resp_liq = client.get(
        f"/api/v1/nomina/preliquidacion/{emp_uuid}?desde=2026-11-13&hasta=2026-11-13",
        headers=headers_jefe
    )
    assert resp_liq.status_code == 200
    liq_data = resp_liq.json()
    assert Decimal(str(liq_data["resumen_horas"]["laboradas"])) >= Decimal("11.0")
    assert Decimal(str(liq_data["resumen_horas"]["extras_reconocidas"])) == Decimal("2.0000")
    # Verificación de firma SHA-256
    assert "sello_sha256" in liq_data

    # 12. Auditoría de integridad forense (Hash-Chain SHA-256)
    resp_audit = client.get("/api/v1/auditoria/verificar", headers=headers_admin)
    assert resp_audit.status_code == 200
    assert resp_audit.json()["integra"] is True
    assert resp_audit.json()["registros_verificados"] > 0
