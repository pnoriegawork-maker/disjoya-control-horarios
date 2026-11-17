"""Actualiza en Supabase y localmente los usuarios oficiales y la planta de nómina de Disjoya Autopartes S.A.S.:
- Administrador: pedro.noriega@gmail.com
- Jefe Inmediato / Aprobador Oficial SST: tminero.sst@gmail.com (Cuenta anterior depurada)
- Trabajador Operativo: pnoriegawork@gmail.com (asignado a tminero.sst@gmail.com)
- Planta Oficial de 11 trabajadores del archivo maestro 'NOMINA DE AUTOPARTES para sr pedro.xlsx'
"""
import uuid
from decimal import Decimal
import psycopg
from app.database import Base, get_engine
from app.nomina_autopartes import PLANTA_OFICIAL_AUTOPARTES
from app.security import hash_password


def configurar_usuarios_reales(password: str = "2h4StIXc3bOaYPbY", project_ref: str = "vxjlbpvcmdxuaznlqsbz"):
    # Asegurar que todas las tablas ORM existan en el motor configurado
    Base.metadata.create_all(bind=get_engine())

    pooler_url = f"postgresql://postgres.{project_ref}:{password}@aws-0-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require"
    print("[*] Conectando a Supabase para depurar cuenta anterior y registrar usuarios oficiales...")

    with psycopg.connect(pooler_url, autocommit=True, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            # 0. REGLA DE DEPURACIÓN ESTRICTA: Eliminar definitivamente spramirez2024@gmail.com
            cur.execute("UPDATE empleados SET jefe_inmediato_id = NULL WHERE jefe_inmediato_id IN (SELECT id FROM empleados WHERE email LIKE '%spramirez%')")
            cur.execute("DELETE FROM solicitudes_horas_extras WHERE jefe_aprobador_id IN (SELECT id FROM empleados WHERE email LIKE '%spramirez%')")
            cur.execute("DELETE FROM empleados WHERE email LIKE '%spramirez%'")
            print("[+] Regla de depuración ejecutada: Cualquier referencia previa fue eliminada de la base de datos.")

            # Obtener Sede Principal Bucaramanga
            cur.execute("SELECT id FROM centros_trabajo LIMIT 1")
            row_centro = cur.fetchone()
            cid = row_centro[0] if row_centro else uuid.UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")

            pwd_hash = hash_password("Disjoya2026*")

            # 1. Administrador General: pedro.noriega@gmail.com
            admin_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
            cur.execute(
                """
                INSERT INTO empleados (id, tipo_documento, numero_documento, nombres, apellidos, email, rol, cargo, salario_base, es_direccion_confianza, password_hash, centro_trabajo_id, activo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (numero_documento) DO UPDATE SET
                    email = EXCLUDED.email, nombres = EXCLUDED.nombres, apellidos = EXCLUDED.apellidos,
                    rol = EXCLUDED.rol, cargo = EXCLUDED.cargo, activo = TRUE
                """,
                (
                    admin_id,
                    "CC",
                    "79001001",
                    "Pedro",
                    "Noriega",
                    "pedro.noriega@gmail.com",
                    "AUDITOR_SUPERADMIN",
                    "Administrador General de la Plataforma",
                    Decimal("12000000.00"),
                    True,
                    pwd_hash,
                    cid,
                    True,
                ),
            )
            print("[+] Administrador registrado: pedro.noriega@gmail.com (Rol: AUDITOR_SUPERADMIN)")

            # 2. Jefe Inmediato y Aprobador Oficial: tminero.sst@gmail.com
            jefe_id = uuid.UUID("22222222-3333-4444-5555-666666666666")
            cur.execute(
                """
                INSERT INTO empleados (id, tipo_documento, numero_documento, nombres, apellidos, email, rol, cargo, salario_base, password_hash, centro_trabajo_id, activo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (numero_documento) DO UPDATE SET
                    email = EXCLUDED.email, nombres = EXCLUDED.nombres, apellidos = EXCLUDED.apellidos,
                    rol = EXCLUDED.rol, cargo = EXCLUDED.cargo, activo = TRUE
                """,
                (
                    jefe_id,
                    "CC",
                    "52002002",
                    "T. Minero",
                    "Jefatura SST",
                    "tminero.sst@gmail.com",
                    "JEFE_INMEDIATO",
                    "Jefe Inmediato SST / Aprobador Oficial de Horas Extras",
                    Decimal("5000000.00"),
                    pwd_hash,
                    cid,
                    True,
                ),
            )
            print("[+] Jefe Inmediato Oficial registrado: tminero.sst@gmail.com (Rol: JEFE_INMEDIATO)")

            # 3. Trabajador Operativo: pnoriegawork@gmail.com asignado a tminero.sst@gmail.com
            emp_id = uuid.UUID("33333333-4444-5555-6666-777777777777")
            cur.execute(
                """
                INSERT INTO empleados (id, tipo_documento, numero_documento, nombres, apellidos, email, rol, cargo, salario_base, jefe_inmediato_id, centro_trabajo_id, password_hash, activo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (numero_documento) DO UPDATE SET
                    jefe_inmediato_id = EXCLUDED.jefe_inmediato_id, email = EXCLUDED.email, activo = TRUE
                """,
                (
                    emp_id,
                    "CC",
                    "1020304050",
                    "Pedro",
                    "Noriega",
                    "pnoriegawork@gmail.com",
                    "TRABAJADOR",
                    "Técnico Operativo de Autopartes",
                    Decimal("2200000.00"),
                    jefe_id,
                    cid,
                    pwd_hash,
                    True,
                ),
            )
            print("[+] Trabajador registrado: pnoriegawork@gmail.com (Asignado a: tminero.sst@gmail.com)")

            # 4. Registrar los 11 Empleados Oficiales de "NOMINA DE AUTOPARTES para sr pedro.xlsx"
            for idx, item in enumerate(PLANTA_OFICIAL_AUTOPARTES, start=1):
                uid = uuid.uuid5(uuid.NAMESPACE_DNS, f"disjoya.nomina.{item['numero_documento']}")
                cur.execute(
                    """
                    INSERT INTO empleados (id, tipo_documento, numero_documento, nombres, apellidos, email, rol, cargo, salario_base, jefe_inmediato_id, centro_trabajo_id, password_hash, activo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (numero_documento) DO UPDATE SET
                        nombres = EXCLUDED.nombres,
                        apellidos = EXCLUDED.apellidos,
                        cargo = EXCLUDED.cargo,
                        salario_base = EXCLUDED.salario_base,
                        jefe_inmediato_id = EXCLUDED.jefe_inmediato_id,
                        activo = TRUE
                    """,
                    (
                        uid,
                        "CC",
                        item["numero_documento"],
                        item["nombres"],
                        item["apellidos"],
                        item["email"],
                        "TRABAJADOR",
                        item["cargo"],
                        item["salario_basico"],
                        jefe_id,
                        cid,
                        pwd_hash,
                        True,
                    ),
                )
            print(f"[+] {len(PLANTA_OFICIAL_AUTOPARTES)} trabajadores de 'NOMINA DE AUTOPARTES para sr pedro' sincronizados.")

    print("\n[=== CONFIGURACIÓN OFICIAL COMPLETADA CON ÉXITO ===]")


if __name__ == "__main__":
    configurar_usuarios_reales()
