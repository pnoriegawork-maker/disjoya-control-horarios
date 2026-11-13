"""Inserta los datos de prueba directamente usando psycopg con la conexión exitosa del pooler."""
import psycopg
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from app.security import cifrar, hash_password, nuevo_secreto_totp, sha256_hex

def seed_direct(password: str, project_ref: str = "vxjlbpvcmdxuaznlqsbz"):
    pooler_url = f"postgresql://postgres.{project_ref}:{password}@aws-0-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require"
    print(f"[*] Conectando al pooler de Supabase para insertar semillas...")
    
    with psycopg.connect(pooler_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Sede Principal
            cid = uuid.UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")
            cur.execute("SELECT id FROM centros_trabajo WHERE id = %s", (cid,))
            if not cur.fetchone():
                kiosk_key = "disjoya-kiosco-sede-principal-2026"
                totp_sec = cifrar(nuevo_secreto_totp(), f"centro:{cid}")
                cur.execute("""
                    INSERT INTO centros_trabajo (id, nombre, direccion, municipio_dane, latitud, longitud, radio_metros, secreto_totp_cifrado, kiosco_api_key_hash, activo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (cid, "Sede Principal Financiera y Operativa", "Calle 72 # 10-07, Chapinero, Bogotá D.C.", "11001",
                      Decimal("4.655828"), Decimal("-74.058342"), 50, totp_sec, sha256_hex(kiosk_key), True))
                print("[+] Sede Principal creada exitosamente!")

            # 2. SuperAdmin
            admin_id = uuid.UUID("99999999-9999-9999-9999-999999999999")
            cur.execute("SELECT id FROM empleados WHERE id = %s", (admin_id,))
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO empleados (id, tipo_documento, numero_documento, nombres, apellidos, email, rol, cargo, salario_base, es_direccion_confianza, password_hash, activo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (admin_id, "CC", "99999999", "SuperAdmin", "Disjoya", "admin@disjoya.com.co", "AUDITOR_SUPERADMIN",
                      "Director General / Auditor", Decimal("12000000.00"), True, hash_password("Disjoya2026*"), True))
                print("[+] SuperAdmin creado exitosamente!")

            # 3. Jefe Inmediato
            jefe_id = uuid.UUID("88888888-8888-8888-8888-888888888888")
            cur.execute("SELECT id FROM empleados WHERE id = %s", (jefe_id,))
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO empleados (id, tipo_documento, numero_documento, nombres, apellidos, email, rol, cargo, salario_base, centro_trabajo_id, password_hash, activo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (jefe_id, "CC", "11111111", "Carlos", "Mendoza", "carlos.jefe@disjoya.com.co", "JEFE_INMEDIATO",
                      "Líder de Operaciones y Logística", Decimal("4500000.00"), cid, hash_password("Disjoya2026*"), True))
                print("[+] Jefe Inmediato creado exitosamente!")

            # 4. Trabajador Operativo (Pedro Noriega)
            emp_id = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
            cur.execute("SELECT id FROM empleados WHERE id = %s", (emp_id,))
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO empleados (id, tipo_documento, numero_documento, nombres, apellidos, email, rol, cargo, salario_base, jefe_inmediato_id, centro_trabajo_id, password_hash, activo)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (emp_id, "CC", "1020304050", "Pedro", "Noriega", "pedro.noriega@disjoya.com.co", "TRABAJADOR",
                      "Técnico Especialista en Suspensiones", Decimal("2200000.00"), jefe_id, cid, hash_password("Disjoya2026*"), True))
                print("[+] Trabajador Pedro Noriega creado exitosamente!")

    # Escribir el .env definitivo con el pooler IPv4
    env_content = f"""DJY_DATABASE_URL=postgresql+psycopg://postgres.{project_ref}:{password}@aws-0-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require
DJY_JWT_SECRET=e9b2f48d8c9a3b7e1f5d6c8b2a4e9f1a0c3b5d7e9f2a4c6e8b0d2f4a6c8e0b2d
DJY_PEPPER_DISPOSITIVO=c7e9a1b3d5f8e0c2b4a6f9d1e3c5a7b9e1f3d5c7b9a1e3f5d7c9b1a3e5f7d9c1
DJY_AES_KEY_B64=4q2+X8rY1Zp9Qv7Wx3Ku6Lt5Ps4Rw1Vz9Ty8Ui7Ok6M=
DJY_ZONA_HORARIA=America/Bogota
DJY_NTP_HABILITADO=true
DJY_NTP_SERVIDOR=time.google.com
DJY_QR_TTL_SEGUNDOS=30
DJY_ENTORNO=produccion
"""
    with open(".env", "w", encoding="utf-8") as f:
        f.write(env_content)
    print("\n[=== SUPABASE 100% INICIALIZADO Y CONECTADO EXITOSAMENTE ===]")

if __name__ == "__main__":
    import sys
    pwd = sys.argv[1] if len(sys.argv) > 1 else "2h4StIXc3bOaYPbY"
    seed_direct(pwd)

