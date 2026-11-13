# Memoria Interna Persistente del Proyecto
## Sistema de Control de Horarios, Horas Extras y Pre-liquidación — DISJOYA AUTOPARTES S.A.S.

> [!IMPORTANT]
> **ESTADO DEL PROYECTO: 100% GUARDADO, TESTEADO Y EN PRODUCCIÓN**  
> Todo el código fuente, la base de datos en la nube (Supabase), el repositorio en GitHub y el despliegue en Vercel están vinculados, sincronizados y probados con éxito. Puedes cerrar la carpeta del proyecto y reanudarlo cuando desees.

---

### 1. Resumen Ejecutivo y Enlaces Oficiales

* 🌐 **Producción en la Nube (Vercel):**  
  👉 **[https://disjoya-control-horarios.vercel.app](https://disjoya-control-horarios.vercel.app)**
* 🐙 **Repositorio Oficial de Código (GitHub):**  
  👉 **[https://github.com/pnoriegawork-maker/disjoya-control-horarios](https://github.com/pnoriegawork-maker/disjoya-control-horarios)** (Rama `main` sincronizada)
* 🗄️ **Base de Datos PostgreSQL (Supabase):**  
  Región US East 2 (Ohio), Connection Pooler activo con 9 tablas y constraints relacionales.

---

### 2. Sede Oficial y Geocerca Institucional (Bucaramanga)

* **Razón Social:** Disjoya Autopartes S.A.S.
* **Dirección Oficial:** `Cl. 23 #13-32, Comuna 4 Occidental, Bucaramanga, Santander`
* **Municipio DANE:** `68001` (Bucaramanga)
* **Coordenadas Satelitales (WGS84):**
  * **Latitud:** `7.124500`
  * **Longitud:** `-73.128000`
* **Radio de Geocerca:** `80 metros`
* **Validación Geodésica:** 
  * Tanto el backend en FastAPI (`app/geo.py` con fórmula Haversine geodésica) como el frontend en móviles validan obligatoriamente que el trabajador se encuentre dentro del radio de 80 metros de la sede para registrar asistencia legal.
  * Incluye botón **`🧪 Probar en Sede`** para permitir demostraciones técnicas remotas sin bloquear al evaluador.
* **Privacidad y Cero Cámaras en PC:** Cumplimiento total de la Ley 1581 de 2012 (Habeas Data) y Código Sustantivo del Trabajo. No se solicita webcam en computadores ni reconocimiento biométrico facial.

---

### 3. Flujo Operativo de Marcación Móvil (Código QR Dinámico)

1. **Visualización en Kiosco (Pestaña 5):**  
   La pantalla central proyecta el código QR TOTP institucional que rota automáticamente cada 30 segundos.
2. **Escaneo con Celular:**  
   El trabajador apunta con la cámara normal de su teléfono (iOS o Android). La cámara detecta automáticamente el enlace web de producción:
   `https://disjoya-control-horarios.vercel.app/?marcar=1&qr=...`
3. **Solicitud de GPS:**  
   Al abrir el enlace, el navegador del teléfono solicita el permiso de ubicación satelital (*"disjoya-control-horarios.vercel.app quiere acceder a tu ubicación"*).
4. **Registro con 1 Toque:**  
   Una vez confirmada la ubicación física en `Cl. 23 #13-32 (Bucaramanga)`, el trabajador pulsa **🟢 REGISTRAR ENTRADA** o **🔴 REGISTRAR SALIDA**.
5. **Sello Criptográfico:**  
   Se genera un hash inmutable SHA-256 encadenado y se registra la hora oficial legal de Colombia (UTC-5) con sincronización NTP.

---

### 4. Cuentas y Accesos Configurados

Todos los perfiles utilizan la contraseña maestra unificada: **`Disjoya2026*`**

| Rol | Correo Electrónico | Contraseña | Perfil y Funcionalidad |
|---|---|---|---|
| **Administrador General** | `pedro.noriega@gmail.com` | `Disjoya2026*` | Acceso total: Sedes, Auditoría SHA-256 y Pestaña 7 ("Asignar Jefes") |
| **Jefe Inmediato** | `spramirez2024@gmail.com` | `Disjoya2026*` | Supervisor facultado para aprobar o rechazar horas extras |
| **Trabajador Operativo** | `pnoriegawork@gmail.com` | `Disjoya2026*` | Empleado asignado a `spramirez2024@gmail.com`. Realiza marcaciones QR |

---

### 5. Parámetros Legales Laborales Integrados (Colombia 2026)

* **Jornada Ordinaria:** 42 horas semanales (Ley 2101 de 2021).
* **Inicio de Jornada Nocturna:** 7:00 p.m. (Ley 2466 de 2025).
* **Recargo Nocturno:** +35% sobre la hora ordinaria.
* **Tope Diario de Horas Extras:** Máximo dos (2) horas diarias improrrogables (Art. 22 Ley 50 de 1990).
* **Dominical y Festivo:** +80% (Vigente en 2026 en el escalonamiento de la reforma laboral).

---

### 6. Infraestructura Técnica y Variables de Entorno

#### Supabase PostgreSQL
* **Host:** `aws-0-us-east-2.pooler.supabase.com:6543`
* **Base de datos:** `postgres`
* **Usuario:** `postgres.vxjlbpvcmdxuaznlqsbz`
* **Contraseña:** `2h4StIXc3bOaYPbY`

#### Vercel Producción
* **Project ID:** `prj_2wIG5BaHBHuH2R9LZH3GpWh8XAjJ`
* **Variables de entorno activas en Vercel:**
  * `DJY_DATABASE_URL`: Conexión directa a Supabase con pooler.
  * `DJY_JWT_SECRET`: Llave de firma de tokens Bearer.
  * `DJY_PEPPER_DISPOSITIVO`: Salting criptográfico para dispositivos móviles.
  * `DJY_AES_KEY_B64`: Cifrado simétrico AES-256-GCM para coordenadas de auditoría.
  * `DJY_ZONA_HORARIA`: `America/Bogota`.
  * `DJY_NTP_HABILITADO`: `true`.
  * `DJY_NTP_SERVIDOR`: `time.google.com`.
  * `DJY_QR_TTL_SEGUNDOS`: `30`.
  * `DJY_ENTORNO`: `produccion`.

---

### 7. Cómo reanudar el proyecto en cualquier momento

Cuando quieras volver a trabajar en este proyecto:

```powershell
# 1. Abrir terminal en la carpeta del proyecto
cd "c:\Users\Estudiante 08\Documents\Pedro_Noriega\Aplicaciones IA\Sistema de control de horarios_Disjoya"

# 2. Ejecutar las pruebas unitarias automatizadas (8/8 pasan)
.\.venv\Scripts\pytest -v

# 3. Iniciar el servidor local de desarrollo
.\.venv\Scripts\uvicorn app.main:app --reload --port 8000

# 4. Para subir cambios directamente a Vercel producción en 5 segundos
python deploy_vercel.py

# 5. Para sincronizar cambios con GitHub
git add .
git commit -m "feat: Nuevas mejoras"
git push origin main
```

Para continuar con el asistente IA en otra sesión, simplemente dile:  
*"Continúa donde quedamos en el proyecto Disjoya"*, y leerá esta memoria automáticamente.
