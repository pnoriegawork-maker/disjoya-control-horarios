# Memoria Interna Persistente del Proyecto
## Sistema de Control de Horarios, Horas Extras y Pre-liquidación — DISJOYA AUTOPARTES S.A.S.

> [!IMPORTANT]
> **ESTADO DEL PROYECTO: 100% GUARDADO, TESTEADO Y EN PRODUCCIÓN VERCEL**  
> Todo el código fuente, la base de datos en la nube (Supabase), el repositorio en GitHub y el despliegue en Vercel están sincronizados, vinculados y testeados.

---

### 1. Sede Oficial y Geocerca Institucional (Bucaramanga)
- **Razón Social:** Disjoya Autopartes S.A.S.
- **Dirección Oficial:** `Cl. 23 #13-32, Comuna 4 Occidental, Bucaramanga, Santander`
- **Municipio DANE:** `68001` (Bucaramanga)
- **Coordenadas Satelitales (WGS84):**
  - **Latitud:** `7.124500`
  - **Longitud:** `-73.128000`
  - **Radio de Geocerca:** `80 metros`
- **Validación de Presencia:** Tanto el backend (`app/geo.py` con fórmula Haversine geodésica) como el frontend en móviles validan obligatoriamente que el trabajador se encuentre dentro del radio de 80 metros para registrar asistencia legal. Incluye botón de prueba técnica para demostraciones remotas.

---

### 2. Flujo de Marcación Móvil con Código QR Dinámico
- **Sin Cámaras en PC / Cero Reconocimiento Facial:** Cumplimiento total de la Ley 1581 de 2012 (Habeas Data) y Código Sustantivo del Trabajo.
- **Flujo Operativo:**
  1. En la pantalla del Kiosco (Pestaña 5) se proyecta el QR dinámico TOTP (rota cada 30 segundos).
  2. El trabajador enfoca el código QR con la cámara normal de su celular (Android o iOS).
  3. La cámara detecta automáticamente el enlace web: `https://disjoya-control-horarios.vercel.app/?marcar=1&qr=...`.
  4. Al pulsar el enlace, se abre la aplicación móvil de Disjoya solicitando de inmediato la **activación del sensor GPS del celular**.
  5. Una vez validada la presencia física en `Cl. 23 #13-32 (Bucaramanga)`, el trabajador selecciona **🟢 Registrar Entrada** o **🔴 Registrar Salida**.
  6. El sistema sella la marcación con hora legal NTP (UTC-5) y hash SHA-256 inmutable.

---

### 3. Cuentas y Accesos Configurados
Todos los usuarios cuentan con la clave unificada: `Disjoya2026*`

| Rol | Correo Electrónico | Contraseña | Perfil en la Plataforma |
|---|---|---|---|
| **Administrador General** | `pedro.noriega@gmail.com` | `Disjoya2026*` | Acceso a sedes, auditoría SHA-256 y pestaña 7 ("Asignar Jefes") |
| **Jefe Inmediato** | `spramirez2024@gmail.com` | `Disjoya2026*` | Supervisor facultado para aprobar o rechazar horas extras |
| **Trabajador Operativo** | `pnoriegawork@gmail.com` | `Disjoya2026*` | Empleado asignado a `spramirez2024@gmail.com`. Realiza marcaciones QR |

---

### 4. Infraestructura y Nube
- **Producción en Vivo Vercel:**  
  👉 **https://disjoya-control-horarios.vercel.app**
- **Base de Datos PostgreSQL:** [Supabase](https://supabase.com/)
  - Project ID: `vxjlbpvcmdxuaznlqsbz` (Región: US East 2 - Ohio)
  - Pooler IPv4: `aws-0-us-east-2.pooler.supabase.com:6543`
  - Contraseña DB: `2h4StIXc3bOaYPbY`
- **Control de Versiones:** [GitHub](https://github.com/pnoriegawork-maker/disjoya-control-horarios)
  - Repositorio: `pnoriegawork-maker/disjoya-control-horarios` (Rama `main`, al día)
- **Despliegue Rápido a Vercel:**  
  Ejecutar en cualquier momento `python deploy_vercel.py` para subir y compilar cambios en producción en 5 segundos.

---

### 5. Cómo reanudar el proyecto en cualquier momento

```powershell
# 1. Abrir terminal en la carpeta del proyecto
cd "c:\Users\Estudiante 08\Documents\Pedro_Noriega\Aplicaciones IA\Sistema de control de horarios_Disjoya"

# 2. Ejecutar la suite de pruebas automatizadas
.\.venv\Scripts\pytest -v

# 3. Iniciar el servidor local
.\.venv\Scripts\uvicorn app.main:app --reload --port 8000
```
Abrir en el navegador: **`http://localhost:8000`** o directamente en producción: **`https://disjoya-control-horarios.vercel.app`**.
