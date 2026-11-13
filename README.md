# Sistema Autónomo de Control Horario, Horas Extras y Pre-liquidación de Nómina
### DISJOYA AUTOPARTES S.A.S. — Régimen Laboral Colombiano (Noviembre 2026)

[![Tests Status](https://img.shields.io/badge/tests-8%20passed-brightgreen.svg)](#verificación-y-pruebas)
[![Python Version](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-teal.svg)](https://fastapi.tiangolo.com/)
[![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL%2015+-emerald.svg)](https://supabase.com/)
[![Vercel](https://img.shields.io/badge/Vercel-Deployed-black.svg)](https://vercel.com/)
[![License](https://img.shields.io/badge/legal-Colombia%202026-red.svg)](#marco-legal-colombiano)

Sistema integral de gestión de asistencia, solicitud y aprobación jerárquica de horas extras, y pre-liquidación automatizada de recargos laborales para **Disjoya Autopartes S.A.S.**, diseñado bajo el bucle autónomo **MAPE-K (Monitor, Analyze, Plan, Execute, Knowledge)**.

---

## 🚀 Características Principales

1. **Marcación Biométrica con QR Dinámico (Kiosko & Móvil)**:
   - Códigos QR rotativos cada 30 segundos mediante algoritmo RFC 6238 TOTP anti-capturas y anti-compartición.
   - Geocerca de alta precisión con validación geodésica Haversine y polígonos ray-casting en sedes y plantas.
   - Cifrado AES-256-GCM con datos asociados autenticados (AAD) para coordenadas GPS sensibles.

2. **Workflow Jerárquico de Horas Extras**:
   - Solicitud de horas extras por parte de los trabajadores.
   - Asignación dinámica de jefes inmediatos y supervisores desde el módulo de administración.
   - Aprobación y rechazo en tiempo real con validación preventiva de topes legales.

3. **Motor Laboral Colombiano (Noviembre 2026)**:
   - **Ley 2101 de 2021**: Jornada ordinaria máxima de 44 horas semanales con divisor mensual de 220 horas.
   - **Ley 50 de 1990 (Art. 22)**: Aplicación estricta de topes legales: máximo 2 horas extras al día y máximo 12 horas extras a la semana.
   - **Jornada Nocturna**: Horario de 19:00 a 06:00 (recargo del 35%).
   - **Dominicales y Festivos**: Recargo del 90% y horas extras con recargo acumulativo (115% diurna / 165% nocturna).
   - **Calendario Ley Emiliani**: Cálculo matemático determinista (Meeus/Gauss) de los 18 festivos de Colombia con traslado al lunes.

4. **PWA Corporativa Branded Disjoya**:
   - Identidad visual oficial: Azul Petróleo (`#0F2D3D`) y Rojo (`#E32626`).
   - Logotipo institucional de amortiguadores embebido.
   - 7 pestañas interactivas: Marcación QR, Histórico, Aprobación de Horas Extras, Pre-liquidación de Nómina, Pantalla Kiosko en Sede, Auditoría Forense y Asignación de Jefes (Admin).

5. **Auditoría Forense Inmutable**:
   - Hash-chain SHA-256 estilo blockchain donde cada evento referencia criptográficamente al evento anterior.
   - Verificación de integridad en un clic para garantizar que ningún registro haya sido alterado.

---

## 👥 Usuarios de Prueba Configurados

| Rol | Correo Electrónico | Contraseña | Función |
|---|---|---|---|
| **Superadministrador** | `pedro.noriega@gmail.com` | `Disjoya2026*` | Acceso a auditoría, configuración de sedes y asignación de supervisores |
| **Jefe Inmediato** | `spramirez2024@gmail.com` | `Disjoya2026*` | Aprobación / rechazo de horas extras solicitadas por trabajadores a su cargo |
| **Trabajador Operativo** | `pnoriegawork@gmail.com` | `Disjoya2026*` | Marcación de entrada/salida por QR dinámico y solicitud de horas extras |

---

## 🛠️ Puesta en Marcha Local

### 1. Requisitos Previos
- Python 3.12+
- Git

### 2. Instalación de dependencias
```bash
python -m venv .venv
source .venv/bin/activate  # En Linux/macOS
# O en Windows: .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 3. Configuración de Variables de Entorno
Copia la plantilla `.env.example` a `.env` y configura tus claves (ya preconfiguradas en el proyecto local):
```bash
cp .env.example .env
```

### 4. Ejecución de Pruebas Automatizadas
```bash
pytest -v
```

### 5. Iniciar Servidor de Desarrollo
```bash
uvicorn app.main:app --reload --port 8000
```
Abre en tu navegador: [http://localhost:8000](http://localhost:8000)

---

## ☁️ Arquitectura de Despliegue en la Nube (100% Free Tier)

- **Base de Datos:** PostgreSQL en la nube mediante [Supabase](https://supabase.com/) con pooling IPv4 (`aws-0-us-east-2.pooler.supabase.com:6543`).
- **Servidor Web y API:** Serverless ASGI desplegado en [Vercel](https://vercel.com/) con integración directa a GitHub.
- **Control de Versiones:** Repositorio en [GitHub](https://github.com/pnoriegawork-maker/disjoya-control-horarios).

---

## 📂 Memoria Persistente del Proyecto
Para detalles completos sobre la arquitectura, estado de conexiones, tokens, claves maestras y notas operativas, consulta el archivo [PROJECT_MEMORY.md](file:///c:/Users/Estudiante%2008/Documents/Pedro_Noriega/Aplicaciones%20IA/Sistema%20de%20control%20de%20horarios_Disjoya/PROJECT_MEMORY.md).
