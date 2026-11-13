"""Arranque inicial: crea el primer AUDITOR_SUPERADMIN (y opcionalmente las tablas en SQLite de desarrollo).

Uso:
  python -m app.bootstrap --documento 1000000001 --nombres "Ana" --apellidos "Auditora" \
      --email auditoria@empresa.com.co --password "********" [--crear-tablas]
En PostgreSQL ejecute antes `psql -f sql/001_schema.sql` (como propietario del esquema)."""
from __future__ import annotations

import argparse
import getpass
from datetime import datetime, timezone
from decimal import Decimal

from .audit import registrar
from .database import Base, get_engine, nueva_sesion
from .models import Empleado
from .security import hash_password


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--documento", required=True)
    ap.add_argument("--nombres", required=True)
    ap.add_argument("--apellidos", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--password")
    ap.add_argument("--salario", default="10000000")
    ap.add_argument("--crear-tablas", action="store_true", help="Solo para SQLite/desarrollo")
    a = ap.parse_args()
    if a.crear_tablas:
        Base.metadata.create_all(get_engine())
    password = a.password or getpass.getpass("Contraseña (min. 12): ")
    if len(password) < 12:
        raise SystemExit("La contraseña debe tener al menos 12 caracteres")
    with nueva_sesion(rol="SISTEMA") as s:
        e = Empleado(tipo_documento="CC", numero_documento=a.documento, nombres=a.nombres, apellidos=a.apellidos, email=a.email,
                     rol="AUDITOR_SUPERADMIN", cargo="Auditor / SuperAdmin", salario_base=Decimal(a.salario),
                     es_direccion_confianza=True, password_hash=hash_password(password))
        s.add(e)
        s.flush()
        registrar(s, ocurrido_en=datetime.now(timezone.utc), accion="BOOTSTRAP_SUPERADMIN", entidad="empleados", entidad_id=e.id, datos={})
        s.commit()
        print(f"SuperAdmin creado: {e.id}")


if __name__ == "__main__":
    main()

