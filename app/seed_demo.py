"""Script de inicialización de datos para demostración inmediata de Disjoya Autopartes SAS.
Crea la Sede Principal y 4 usuarios con roles diferentes para probar la aplicación."""
import os
import secrets
import sys
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import nueva_sesion
from app.models import CentroTrabajo, Empleado, TurnoProgramado
from app.security import cifrar, hash_password, nuevo_secreto_totp, sha256_hex
from datetime import datetime, date, timedelta
from app.legal import TZ_BOGOTA, a_utc

def seed():
    with nueva_sesion(rol="SISTEMA") as db:
        # 1. Crear Centro de Trabajo Disjoya
        cid = uuid.UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")
        existente = db.get(CentroTrabajo, cid)
        if not existente:
            kiosk_api_key = "disjoya-kiosco-sede-principal-2026"
            c = CentroTrabajo(
                id=cid,
                nombre="Sede Principal Financiera y Operativa",
                direccion="Calle 72 # 10-07, Chapinero, Bogotá D.C.",
                municipio_dane="11001",
                latitud=Decimal("4.655828"),
                longitud=Decimal("-74.058342"),
                radio_metros=50,
                secreto_totp_cifrado=cifrar(nuevo_secreto_totp(), f"centro:{cid}"),
                kiosco_api_key_hash=sha256_hex(kiosk_api_key),
                activo=True
            )
            db.add(c)
            print(f"[OK] Centro de Trabajo creado: {c.nombre}")
        else:
            print("[INFO] Centro de Trabajo ya existe.")

        # 2. Crear SuperAdmin
        admin_email = "admin@disjoya.com.co"
        if not db.query(Empleado).filter_by(email=admin_email).first():
            admin = Empleado(
                id=uuid.UUID("99999999-9999-9999-9999-999999999999"),
                tipo_documento="CC",
                numero_documento="99999999",
                nombres="SuperAdmin",
                apellidos="Disjoya",
                email=admin_email,
                rol="AUDITOR_SUPERADMIN",
                cargo="Director General / Auditor",
                salario_base=Decimal("12000000.00"),
                es_direccion_confianza=True,
                password_hash=hash_password("Disjoya2026*")
            )
            db.add(admin)
            print(f"[OK] SuperAdmin creado: {admin.email}")

        # 3. Crear Jefe Inmediato
        jefe_email = "carlos.jefe@disjoya.com.co"
        jefe_id = uuid.UUID("88888888-8888-8888-8888-888888888888")
        if not db.query(Empleado).filter_by(email=jefe_email).first():
            jefe = Empleado(
                id=jefe_id,
                tipo_documento="CC",
                numero_documento="11111111",
                nombres="Carlos",
                apellidos="Mendoza",
                email=jefe_email,
                rol="JEFE_INMEDIATO",
                cargo="Líder de Operaciones y Logística",
                salario_base=Decimal("4500000.00"),
                centro_trabajo_id=cid,
                password_hash=hash_password("Disjoya2026*")
            )
            db.add(jefe)
            print(f"[OK] Jefe Inmediato creado: {jefe.email}")

        # 4. Crear Trabajador Operativo (Pedro Noriega)
        emp_email = "pedro.noriega@disjoya.com.co"
        emp_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        if not db.query(Empleado).filter_by(email=emp_email).first():
            emp = Empleado(
                id=emp_id,
                tipo_documento="CC",
                numero_documento="1020304050",
                nombres="Pedro",
                apellidos="Noriega",
                email=emp_email,
                rol="TRABAJADOR",
                cargo="Técnico Especialista en Suspensiones",
                salario_base=Decimal("2200000.00"),
                jefe_inmediato_id=jefe_id,
                centro_trabajo_id=cid,
                password_hash=hash_password("Disjoya2026*")
            )
            db.add(emp)
            print(f"[OK] Trabajador creado: {emp.email}")

        db.commit()
        print("\n=== SEMILLA INICIAL COMPLETADA EXITOSAMENTE ===")
        print("Credenciales por defecto: contraseña 'Disjoya2026*' para todos los usuarios.")

if __name__ == "__main__":
    seed()

