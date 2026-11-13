"""Actualiza en Supabase y localmente los usuarios solicitados por el usuario:
- Administrador: pedro.noriega@gmail.com
- Jefe que autoriza: spramirez2024@gmail.com
- Trabajador que solicita HE: pnoriegawork@gmail.com (asignado al jefe spramirez2024@gmail.com)
"""
import psycopg
import uuid
from decimal import Decimal
from app.security import hash_password

def configurar_usuarios_reales(password: str = "2h4StIXc3bOaYPbY", project_ref: str = "vxjlbpvcmdxuaznlqsbz"):
    pooler_url = f"postgresql://postgres.{project_ref}:{password}@aws-0-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require"
    print(f"[*] Conectando a Supabase para registrar usuarios de prueba personalizados...")
    
    with psycopg.connect(pooler_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Obtener Sede Principal
            cur.execute("SELECT id FROM centros_trabajo LIMIT 1")
            row_centro = cur.fetchone()
            cid = row_centro[0] if row_centro else uuid.UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")

            # 1. Administrador de la plataforma: pedro.noriega@gmail.com
            admin_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
            cur.execute("DELETE FROM empleados WHERE email = 'pedro.noriega@gmail.com' OR numero_documento = '79001001'")
            cur.execute("""
                INSERT INTO empleados (id, tipo_documento, numero_documento, nombres, apellidos, email, rol, cargo, salario_base, es_direccion_confianza, password_hash, centro_trabajo_id, activo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (admin_id, "CC", "79001001", "Pedro", "Noriega", "pedro.noriega@gmail.com", "AUDITOR_SUPERADMIN",
                  "Administrador General de la Plataforma", Decimal("12000000.00"), True, hash_password("Disjoya2026*"), cid, True))
            print("[+] Administrador registrado: pedro.noriega@gmail.com (Rol: AUDITOR_SUPERADMIN)")

            # 2. Jefe que autoriza horas extras: spramirez2024@gmail.com
            jefe_id = uuid.UUID("22222222-3333-4444-5555-666666666666")
            cur.execute("DELETE FROM empleados WHERE email = 'spramirez2024@gmail.com' OR numero_documento = '52002002'")
            cur.execute("""
                INSERT INTO empleados (id, tipo_documento, numero_documento, nombres, apellidos, email, rol, cargo, salario_base, password_hash, centro_trabajo_id, activo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (jefe_id, "CC", "52002002", "S.P.", "Ramírez", "spramirez2024@gmail.com", "JEFE_INMEDIATO",
                  "Jefe de Operaciones / Aprobador de Horas Extras", Decimal("5000000.00"), hash_password("Disjoya2026*"), cid, True))
            print("[+] Jefe autorizador registrado: spramirez2024@gmail.com (Rol: JEFE_INMEDIATO)")

            # 3. Trabajador que solicita horas extras: pnoriegawork@gmail.com asignado al jefe spramirez2024@gmail.com
            emp_id = uuid.UUID("33333333-4444-5555-6666-777777777777")
            cur.execute("DELETE FROM empleados WHERE email = 'pnoriegawork@gmail.com' OR numero_documento = '1020304050'")
            cur.execute("""
                INSERT INTO empleados (id, tipo_documento, numero_documento, nombres, apellidos, email, rol, cargo, salario_base, jefe_inmediato_id, centro_trabajo_id, password_hash, activo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (emp_id, "CC", "1020304050", "Pedro", "Noriega", "pnoriegawork@gmail.com", "TRABAJADOR",
                  "Técnico Operativo de Autopartes", Decimal("2200000.00"), jefe_id, cid, hash_password("Disjoya2026*"), True))
            print("[+] Trabajador registrado: pnoriegawork@gmail.com (Asignado al jefe: spramirez2024@gmail.com)")

    print("\n[=== CONFIGURACIÓN DE USUARIOS SOLICITADOS COMPLETADA ===]")

if __name__ == "__main__":
    configurar_usuarios_reales()
