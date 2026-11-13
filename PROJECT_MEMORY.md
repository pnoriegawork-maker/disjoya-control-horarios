# MEMORIA INTERNA Y ESTADO DEL PROYECTO
## Sistema Autónomo de Control Horario, Horas Extras y Pre-liquidación de Nómina
**Empresa:** DISJOYA AUTOPARTES S.A.S. (Colombia)  
**Marco Legal:** Noviembre 2026 (CST, Ley 2101/2021, Ley 50/1990 Art. 22, Ley 1846/2017, Ley 2466/2025, Ley 1581/2012)  
**Fecha de guardado:** 13 de Noviembre de 2026

---

## 1. RESUMEN EJECUTIVO Y OBJETIVO
Este proyecto es una solución integral "llave en mano", de despliegue 100% gratuito (Zero Cost), desarrollada bajo la arquitectura **MAPE-K (Monitor, Analyze, Plan, Execute, Knowledge)**.
Gestiona el control biométrico-digital de asistencia mediante códigos QR rotativos dinámicos (TOTP 30s) con validación estricta de geocerca en planta/sedes de Disjoya, workflow jerárquico de horas extras con aprobaciones multinivel y pre-liquidación automatizada de recargos laborales bajo la legislación laboral colombiana vigente en 2026.

---

## 2. USUARIOS DE PRUEBA Y ROLES CONFIGURADOS

Todos los usuarios tienen la contraseña unificada: `Disjoya2026*`

| Nombre | Correo Electrónico | Rol del Sistema | Asignación / Jerarquía | Tipo Documento / Número |
|---|---|---|---|---|
| **Pedro Noriega (Admin)** | `pedro.noriega@gmail.com` | `AUDITOR_SUPERADMIN` | Administrador total. Puede reasignar jefes y supervisores en la PWA (Pestaña 7). | CC `1020304050` |
| **S. P. Ramírez (Jefe)** | `spramirez2024@gmail.com` | `JEFE_INMEDIATO` | Supervisor inmediato. Aprueba/Rechaza horas extras de los trabajadores a su cargo. | CC `1020304051` |
| **Pedro Noriega (Trabajador)**| `pnoriegawork@gmail.com` | `TRABAJADOR` | Empleado operativo. Realiza marcación QR y solicita horas extras a `spramirez2024@gmail.com`. | CC `1020304052` |

---

## 3. INFRAESTRUCTURA Y SERVICIOS EN LA NUBE (100% GRATIS)

### A. Base de Datos: Supabase (PostgreSQL 15+)
- **Project Reference:** `vxjlbpvcmdxuaznlqsbz`
- **Región:** US East 2 (Ohio)
- **Host Pooler IPv4 (Supavisor):** `aws-0-us-east-2.pooler.supabase.com` (Puerto `6543`)
- **Base de datos:** `postgres`
- **Usuario:** `postgres.vxjlbpvcmdxuaznlqsbz`
- **Contraseña:** `2h4StIXc3bOaYPbY`
- **URI de conexión SQLAlchemy / Psycopg3:**
  ```text
  postgresql+psycopg://postgres.vxjlbpvcmdxuaznlqsbz:2h4StIXc3bOaYPbY@aws-0-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require
  ```
- **Esquema aplicado:** `sql/001_schema.sql` (11 tablas relacionales, RLS activado, triggers WORM de inmutabilidad en auditoría, enums nativos).
- **Nota técnica de red:** Usar siempre el endpoint de pooling IPv4 (`aws-0-us-east-2.pooler.supabase.com:6543`) en entornos Windows/locales para evitar fallos de resolución de DNS IPv6 (`getaddrinfo failed`).

### B. Repositorio de Código: GitHub
- **Cuenta origen:** `pnoriegawork-maker`
- **URL del Repositorio:** `https://github.com/pnoriegawork-maker/disjoya-control-horarios`
- **Rama principal:** `main`
- **Visibilidad:** Pública (para permitir la clonación/importación libre en Vercel entre cuentas).

### C. Despliegue y Hosting: Vercel Serverless
- **Equipo/Cuenta Vercel:** `pedronoriega-eng`
- **Proyecto Vercel:** `mi-repositorio` (creado clonando `pnoriegawork-maker/disjoya-control-horarios`)
- **Estado:** Implementado en producción (Build completado con éxito).
- **Arquitectura Vercel:** Serverless ASGI a través de `api/index.py` y `vercel.json`.

---

## 4. VARIABLES DE ENTORNO REQUERIDAS (`.env`)

El archivo `.env` local en la raíz del proyecto contiene los siguientes valores criptográficos y de conexión:

```env
DJY_DATABASE_URL=postgresql+psycopg://postgres.vxjlbpvcmdxuaznlqsbz:2h4StIXc3bOaYPbY@aws-0-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require
DJY_JWT_SECRET=e9b2f48d8c9a3b7e1f5d6c8b2a4e9f1a0c3b5d7e9f2a4c6e8b0d2f4a6c8e0b2d
DJY_PEPPER_DISPOSITIVO=c7e9a1b3d5f8e0c2b4a6f9d1e3c5a7b9e1f3d5c7b9a1e3f5d7c9b1a3e5f7d9c1
DJY_AES_KEY_B64=4q2+X8rY1Zp9Qv7Wx3Ku6Lt5Ps4Rw1Vz9Ty8Ui7Ok6M=
DJY_ZONA_HORARIA=America/Bogota
DJY_NTP_HABILITADO=true
DJY_NTP_SERVIDOR=time.google.com
DJY_QR_TTL_SEGUNDOS=30
DJY_ENTORNO=produccion
```

> **IMPORTANTE PARA VERCEL:**  
> En el panel de Vercel (`https://vercel.com/pedronoriega-engs-projects`), entrar al proyecto -> **Settings** -> **Environment Variables**, y agregar estas mismas variables:
> 1. `DJY_DATABASE_URL`
> 2. `DJY_JWT_SECRET`
> 3. `DJY_PEPPER_DISPOSITIVO`
> 4. `DJY_AES_KEY_B64`
> 5. `DJY_ZONA_HORARIA`
> Luego pulsar en **Deployments** -> **Redeploy** para que la función serverless se conecte en vivo a Supabase.

---

## 5. CÓMO REANUDAR Y CORRER EL PROYECTO LOCALMENTE

Cuando abras la carpeta en cualquier momento futuro, sigue estos sencillos pasos en la terminal PowerShell:

### Paso 1: Activar el entorno virtual de Python
```powershell
.\.venv\Scripts\Activate.ps1
```

### Paso 2: Ejecutar los tests automatizados (Verificación de salud)
```powershell
.\.venv\Scripts\pytest -v
```
*(Debe reportar `8 passed` en menos de 1 segundo)*.

### Paso 3: Iniciar el servidor local
```powershell
.\.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

### Paso 4: Abrir la aplicación
Navega en tu navegador a:
`http://localhost:8000`

Verás la interfaz web PWA completa de **Disjoya Autopartes S.A.S.** con sus colores corporativos (Azul Petróleo `#0F2D3D` y Rojo `#E32626`), el logotipo institucional de amortiguadores y las 7 pestañas interactivas.

---

## 6. ESTRUCTURA Y MÓDULOS DEL CÓDIGO FUENTE

```text
Sistema de control de horarios_Disjoya/
├── api/
│   └── index.py               # Handler serverless ASGI para despliegue en Vercel
├── app/
│   ├── __init__.py
│   ├── audit.py               # Cadena de bloques SHA-256 inmutable de auditoría forense
│   ├── config.py              # Carga de configuraciones desde .env con Pydantic Settings
│   ├── database.py            # Motor de conexión SQLAlchemy y sesiones de BD
│   ├── geo.py                 # Cálculos geodésicos (Haversine) y polígonos de geocerca
│   ├── habeas.py              # Gestión y hash de consentimiento Ley 1581 Habeas Data
│   ├── legal.py               # Motor laboral colombiano 2026 (CST, Ley 2101, Ley 50)
│   ├── main.py                # Rutas FastAPI, endpoints REST y servidor de archivos estáticos
│   ├── models.py              # Modelos ORM SQLAlchemy de las 11 tablas
│   ├── notificador.py         # Despacho asíncrono de notificaciones push/email
│   ├── schemas.py             # Esquemas Pydantic v2 para validación de entrada/salida
│   ├── security.py            # Cifrado AES-256-GCM, TOTP QR RFC 6238, JWT y scrypt
│   ├── seed_direct.py         # Script para sembrar centros de trabajo y turnos
│   ├── setup_custom_users.py  # Script que configuró los 3 usuarios de prueba reales
│   ├── servicios.py           # Bucle autónomo MAPE-K y conciliación de turnos
│   └── tiempo.py              # Sincronización horaria legal SNTP con time.google.com
├── sql/
│   └── 001_schema.sql         # DDL completo con tablas, tipos ENUM, RLS y triggers WORM
├── static/
│   ├── index.html             # Interfaz PWA moderna responsive (Tailwind, Lucide, HTML5-QRCode)
│   ├── logo_disjoya.png       # Logo corporativo Disjoya (amortiguador)
│   └── manifest.json          # Manifiesto PWA para instalación en Android/iOS/Desktop
├── tests/
│   ├── test_api.py            # Tests de integración del flujo completo de la API
│   └── test_legal.py          # Tests unitarios del motor laboral y topes legales
├── .env                       # Variables de entorno secretas (no subir a repositorios públicos)
├── .env.example               # Plantilla de variables para nuevos entornos
├── .gitignore                 # Archivos excluidos de control de versiones
├── contraseñas.txt            # Registro local de contraseñas de acceso
├── Dockerfile                 # Contenedor para despliegues en Docker/Kubernetes/Render
├── Procfile                   # Comando de inicio para plataformas PaaS
├── PROJECT_MEMORY.md          # Este documento de memoria persistente
├── README.md                  # Guía general de uso y arquitectura del proyecto
├── requirements.txt           # Dependencias Python fijadas
└── vercel.json                # Configuración de rutas y runtime Python para Vercel
```

---

## 7. MOTOR LABORAL COLOMBIANO VIGENTE (NOVIEMBRE 2026)

El motor `app/legal.py` implementa con exactitud matemática las normas laborales colombianas:

1. **Jornada Ordinaria Progresiva (Ley 2101 de 2021):**
   - Reducción gradual: 44 horas semanales vigentes desde julio 2025; divisor mensual 220 horas.
   - Parámetros versionados por fecha de ocurrencia del turno.
2. **Topes Máximos de Horas Extras (Ley 50 de 1990, Art. 22):**
   - **Máximo diario:** 2 horas extras. Si se solicitan más, el sistema rechaza automáticamente.
   - **Máximo semanal:** 12 horas extras acumuladas. El motor bloquea cualquier exceso semanal.
3. **Jornada Nocturna y Recargos:**
   - Horario nocturno: 19:00 a 06:00 (recargo del 35%).
   - Hora extra diurna (HED): +25%.
   - Hora extra nocturna (HEN): +75%.
   - Recargo dominical/festivo: 90% (según el cronograma de la reforma 2024-2026).
   - Hora extra dominical diurna (HEDDF): 90% + 25% = 115%.
   - Hora extra dominical nocturna (HENDF): 90% + 75% = 165%.
4. **Calendario Dinámico de 18 Días Festivos (Ley Emiliani):**
   - Algoritmo de Meeus/Gauss para cálculo exacto del Domingo de Pascua de cualquier año.
   - Traslado automático de festivos al lunes siguiente según la Ley 51 de 1983.

---

## 8. CÓMO CONTINUAR DONDE QUEDAMOS
Cuando abras este proyecto en el futuro:
1. Dile al asistente de IA:  
   *"Hola, revisa el archivo PROJECT_MEMORY.md y continuemos trabajando en el sistema de control de horarios de Disjoya."*
2. Cualquier nuevo usuario o turno que desees agregar puede gestionarse desde la pestaña **"7. Asignar Jefes (Admin)"** en la aplicación web, o mediante los scripts `app/setup_custom_users.py`.
3. Tu trabajo, base de datos y despliegue están 100% a salvo y respaldados.
