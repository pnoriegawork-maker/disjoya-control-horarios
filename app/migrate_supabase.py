"""Script para aplicar automáticamente el esquema SQL y la semilla en Supabase."""
import sys
import psycopg
from pathlib import Path

def aplicar_migracion(password: str, project_ref: str = "vxjlbpvcmdxuaznlqsbz"):
    print(f"[*] Conectando a Supabase ({project_ref})...")
    
    # Intentar conexión directa y conexión por pooler
    urls = [
        f"postgresql://postgres:{password}@db.{project_ref}.supabase.co:5432/postgres?sslmode=require",
        f"postgresql://postgres.{project_ref}:{password}@aws-0-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require",
        f"postgresql://postgres.{project_ref}:{password}@aws-0-us-east-2.pooler.supabase.com:5432/postgres?sslmode=require"
    ]
    
    conn = None
    for url in urls:
        try:
            print(f"[*] Probando endpoint de conexión...")
            conn = psycopg.connect(url, autocommit=True)
            print("[+] Conexión exitosa a Supabase!")
            break
        except Exception as e:
            last_err = e
            
    if not conn:
        print(f"[-] Error de autenticación o conexión: {last_err}")
        return False

    schema_file = Path(__file__).resolve().parents[1] / "sql" / "001_schema.sql"
    sql = schema_file.read_text(encoding="utf-8")
    
    print("[*] Ejecutando esquema SQL DDL completo (Tablas, RLS, Triggers)...")
    with conn.cursor() as cur:
        cur.execute(sql)
    print("[+] Esquema DDL aplicado con éxito en Supabase!")
    
    # Actualizar .env con la URL funcional
    env_file = Path(__file__).resolve().parents[1] / ".env"
    env_content = f"""DJY_DATABASE_URL=postgresql+psycopg://postgres:{password}@db.{project_ref}.supabase.co:5432/postgres?sslmode=require
DJY_JWT_SECRET=e9b2f48d8c9a3b7e1f5d6c8b2a4e9f1a0c3b5d7e9f2a4c6e8b0d2f4a6c8e0b2d
DJY_PEPPER_DISPOSITIVO=c7e9a1b3d5f8e0c2b4a6f9d1e3c5a7b9e1f3d5c7b9a1e3f5d7c9b1a3e5f7d9c1
DJY_AES_KEY_B64=4q2+X8rY1Zp9Qv7Wx3Ku6Lt5Ps4Rw1Vz9Ty8Ui7Ok6M=
DJY_ZONA_HORARIA=America/Bogota
DJY_NTP_HABILITADO=true
DJY_NTP_SERVIDOR=time.google.com
DJY_QR_TTL_SEGUNDOS=30
DJY_ENTORNO=produccion
"""
    env_file.write_text(env_content, encoding="utf-8")
    print("[+] Archivo .env configurado automáticamente!")
    
    # Ejecutar la semilla inicial
    print("[*] Insertando datos iniciales de Disjoya (Sede, SuperAdmin, Jefe, Trabajador)...")
    from app.seed_demo import seed
    seed()
    print("[+] Datos de demostración insertados con éxito!")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python -m app.migrate_supabase <PASSWORD_DE_SUPABASE>")
        sys.exit(1)
    pwd = sys.argv[1]
    aplicar_migracion(pwd)

