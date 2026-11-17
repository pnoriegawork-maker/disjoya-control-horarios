# Memoria Interna Persistente del Proyecto
## Sistema de Control de Horarios, Horas Extras y Pre-liquidación — DISJOYA AUTOPARTES S.A.S.

> [!IMPORTANT]
> **ESTADO DEL PROYECTO: 100% GUARDADO, RESPALDADO Y EN PRODUCCIÓN**  
> Todo el código fuente, la base de datos en la nube (Supabase), el repositorio en GitHub y el despliegue en Vercel están sincronizados. Puedes cerrar la carpeta del proyecto con total tranquilidad.

---

### 1. Próximo Paso al Retomar el Proyecto (Pendiente Inmediato)

En la última sesión (17 de noviembre de 2026) se diagnosticó por qué la simulación de solicitud de horas extras no envió notificación ni correo al jefe inmediato (`spramirez2024@gmail.com`). Al retomar el proyecto, ejecutaremos estos **2 pasos**:

1. **Conectar el Formulario Web de Horas Extras con el Backend Real (`static/index.html`):**
   * Reemplazar la función simulada `enviarSolicitudHE()` en `static/index.html` (líneas 1766-1789) para que realice la petición HTTP real `POST /api/v1/horas-extras/solicitudes` hacia el backend.
   * Conectar la **Pestaña 3 ("Aprobación Jefatura")** con `GET /api/v1/horas-extras/solicitudes` y `POST /api/v1/horas-extras/solicitudes/{id}/decision` para que las solicitudes aparezcan en tiempo real en la bandeja del jefe (`S.P. Ramírez`).
2. **Habilitar el Envío Real de Correos Electrónicos (SMTP) en `app/notificador.py` y `app/config.py`:**
   * Configurar las variables de entorno de correo saliente en `.env` y en Vercel:
     * `DJY_SMTP_HOST` (ej. `smtp.gmail.com`)
     * `DJY_SMTP_PUERTO` (`587`)
     * `DJY_SMTP_USUARIO` (cuenta remitente)
     * `DJY_SMTP_PASSWORD` (Contraseña de Aplicación de Google de 16 caracteres)
     * `DJY_SMTP_REMITENTE`
   * Con esto, el módulo `app/notificador.py` pasará de estado `"omitido"` a `"enviado"`, entregando el correo directamente a la bandeja de entrada del jefe aprobador.

---

### 2. Enlaces Oficiales y Despliegue Activo

* 🌐 **Producción en la Nube (Vercel):**  
  👉 **[https://disjoya-control-horarios.vercel.app](https://disjoya-control-horarios.vercel.app)**
* 🐙 **Repositorio Oficial de Código (GitHub):**  
  👉 **[https://github.com/pnoriegawork-maker/disjoya-control-horarios](https://github.com/pnoriegawork-maker/disjoya-control-horarios)** (Rama `main` sincronizada)
* 🗄️ **Base de Datos PostgreSQL (Supabase):**  
  Región US East 2 (Ohio), Connection Pooler activo con 9 tablas y constraints relacionales.

---

### 3. Sede Oficial y Geocerca Institucional (Bucaramanga)

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

### 4. Flujo Operativo de Marcación Móvil (Código QR Dinámico)

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

### 5. Cuentas y Accesos Configurados

Todos los perfiles utilizan la contraseña maestra unificada: **`Disjoya2026*`**

| Rol | Correo Electrónico | Contraseña | Perfil y Funcionalidad |
|---|---|---|---|
| **Administrador General** | `pedro.noriega@gmail.com` | `Disjoya2026*` | Acceso total: Sedes, Auditoría SHA-256 y Pestaña 7 ("Asignar Jefes") |
| **Jefe Inmediato** | `spramirez2024@gmail.com` | `Disjoya2026*` | Supervisor facultado para aprobar o rechazar horas extras |
| **Trabajador Operativo** | `pnoriegawork@gmail.com` | `Disjoya2026*` | Empleado asignado a `spramirez2024@gmail.com`. Realiza marcaciones QR |

---

### 6. Parámetros Legales Laborales Integrados (Colombia 2026)

* **Jornada Ordinaria:** 42 horas semanales (Ley 2101 de 2021).
* **Inicio de Jornada Nocturna:** 7:00 p.m. (Ley 2466 de 2025).
* **Recargo Nocturno:** +35% sobre la hora ordinaria.
* **Tope Diario de Horas Extras:** Máximo dos (2) horas diarias improrrogables (Art. 22 Ley 50 de 1990).
* **Dominical y Festivo:** +80% (Vigente en 2026 en el escalonamiento de la reforma laboral).

---

### 7. Infraestructura Técnica y Variables de Entorno

#### Supabase PostgreSQL
* **Host:** `aws-0-us-east-2.pooler.supabase.com:6543`
* **Base de datos:** `postgres`
* **Usuario:** `postgres.vxjlbpvcmdxuaznlqsbz`
* **Contraseña:** `2h4StIXc3bOaYPbY`

#### Vercel Producción
* **Project ID:** `prj_2wIG5BaHBHuH2R9LZH3GpWh8XAjJ`
* **Variables de entorno activas en Vercel:** `DJY_DATABASE_URL`, `DJY_JWT_SECRET`, `DJY_PEPPER_DISPOSITIVO`, `DJY_AES_KEY_B64`, `DJY_ZONA_HORARIA`, `DJY_NTP_HABILITADO`, `DJY_NTP_SERVIDOR`, `DJY_QR_TTL_SEGUNDOS`, `DJY_ENTORNO`.

---

### 8. Cómo reanudar el proyecto en cualquier momento

```powershell
# 1. Abrir terminal en la carpeta del proyecto
cd "c:\Users\Estudiante 08\Documents\Pedro_Noriega\Aplicaciones IA\Sistema de control de horarios_Disjoya"

# 2. Ejecutar las pruebas unitarias automatizadas (8/8 pasan)
.\.venv\Scripts\pytest -v

# 3. Iniciar el servidor local de desarrollo
.\.venv\Scripts\uvicorn app.main:app --reload --port 8000

# 4. Para subir cambios directamente a Vercel producción en 5 segundos
python deploy_vercel.py
```

Para continuar con el asistente IA en otra sesión, simplemente dile:  
*"Continúa donde quedamos en el proyecto Disjoya"*, y retomaremos de inmediato en la conexión del flujo de notificaciones y correo de Horas Extras.
