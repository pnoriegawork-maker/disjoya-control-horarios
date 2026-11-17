# Memoria Interna Persistente del Proyecto
## Sistema de Control de Horarios, Horas Extras y Pre-Liquidación de Nómina
### DISJOYA AUTOPARTES S.A.S. — Bucaramanga, Santander

> [!IMPORTANT]
> **ESTADO DEL PROYECTO: 100% OPERATIVO, VERIFICADO Y EN PRODUCCIÓN (VERCEL)**  
> Todo el código fuente, la base de datos en la nube (Supabase PostgreSQL), los módulos legales de nómina colombiana, las notificaciones por correo SMTP y los desprendibles PDF oficiales en orientación horizontal están sincronizados y probados con 11/11 tests unitarios superados. La carpeta y la sesión pueden cerrarse con total tranquilidad.

---

### 1. Resumen Ejecutivo de lo Implementado y Consolidado

1. **Portales Segregados por Rol y Acceso Móvil (PWA):**
   * **Portal del Trabajador (`/trabajador`):** Diseñado para guardarse como aplicación móvil en celulares (PWA con `manifest.json` y `sw.js`). Interfaz simplificada que solo permite registrar entrada/salida por geocerca y radicar solicitudes de horas extras. **Aislado al 100%:** el trabajador nunca ve enlaces ni opciones de aprobación del jefe.
   * **Bandeja de Aprobación de la Jefatura (`/jefe`):** Acceso exclusivo para el jefe inmediato oficial (`tminero.sst@gmail.com`). Solo visualiza la bandeja de aprobación y rechazo de horas extras con botón directo de decisión.
   * **Consola de Superadministrador (`/admin`):** Acceso integral con todos los recursos, geocercas, asignación de jefaturas, novedades de ausentismo, registro de comisiones y recálculo y liquidación de nómina con emisión de 11 PDFs individuales.

2. **Motor de Doble Contabilización de Horas Extras y Banco de Arrastre (Carry-Over Ledger):**
   * **Cuenta 1 (Nómina Legal Mensual):** Se liquida cumpliendo estrictamente los topes legales de Colombia (máx. 2h/día y 12h/semana de acuerdo con el Art. 22 de la Ley 50 de 1990 y Ley 2101).
   * **Cuenta 2 (Banco Real de Horas Extras del Trabajador):** Registra el 100% de las horas efectivas aprobadas por la jefatura. El excedente que no quepa en el tope legal mensual se arrastra de forma automática al mes siguiente en la base de datos, garantizando que el trabajador cobre el 100% de sus horas trabajadas en los siguientes períodos.
   * **Corte de Hora Extra Nocturna:** Liquidado a partir de las **7:00 p.m. (19:00 a 06:00)** con recargo del 35% / 75% conforme a la normativa colombiana vigente (Ley 2466 / CST Art. 160).
   * **Dominicales y Festivos:** Calendario legal colombiano integrado automáticamente según la Ley 51 de 1983 (Ley Emiliani).

3. **Notificaciones por Correo Electrónico (SMTP y Prevención de Bloqueos DKIM):**
   * Remitente corporativo estructurado conforme a RFC 5322: `"Disjoya Autopartes SAS" <pedro.noriega@gmail.com>`.
   * Asuntos limpios en ASCII plano sin emojis excesivos ni caracteres especiales para evitar bloqueos por políticas DKIM/DMARC de Google (`550 5.7.30`).
   * Envío automático de correo con enlace de decisión al jefe inmediato (`tminero.sst@gmail.com`) cuando el trabajador radica horas extras.
   * Envío de confirmación al correo del trabajador (`pnoriegawork@gmail.com`) sin mostrar enlaces del jefe.

4. **Desprendible Oficial de Nómina en PDF (`app/desprendibles_pdf.py`):**
   * **Formato Horizontal (Landscape 792 × 612 pt):** Optimizado para legibilidad en comprobantes de dos columnas (Devengados y Deducciones).
   * **Logo Oficial:** Incorporado en la esquina superior izquierda sobre recuadro institucional.
   * **Limpieza de Encabezado:** Se eliminaron menciones de SST y sección en el header para un diseño corporativo limpio.
   * **Tarjeta del Trabajador Reestructurada (Cero Cortes):** Dividida en 3 filas y 2 columnas con márgenes holgados, garantizando que todos los datos y novedades (`Vacaciones`, `Incap. EPS` e `Incap. ARL: 0d`) sean 100% visibles.
   * **Conceptos de Devengados:**
     * `Salario Básico` limpio (sin "Proporcional" ni "(30 días)").
     * Horas extras sin factores multiplicadores (`x 1.25`, `x 1.75`), indicando el período horario, la cantidad en horas y el valor liquidado:
       * `Horas Extras Diurnas (06:00 - 19:00) (XX.XX h)`
       * `Horas Extras Nocturnas (19:00 - 06:00) (XX.XX h)`
       * `Horas Extras Dom./Festivas (XX.XX h)`
   * **Supresión de Firmas:** Espacio de firmas retirado en su totalidad a solicitud del usuario.
   * **Cierre Criptográfico:** Banner de *Neto Líquido a Pagar* en letras y números, y pie de integridad digital con hash SHA-256 inmutable.

---

### 2. Enlaces de Acceso y Producción en Vivo

* 🌐 **Aplicación Web Principal:**  
  👉 **[https://disjoya-control-horarios.vercel.app](https://disjoya-control-horarios.vercel.app)**
* 📱 **Acceso Trabajador (PWA Celular):**  
  👉 **[https://disjoya-control-horarios.vercel.app/trabajador](https://disjoya-control-horarios.vercel.app/trabajador)**
* 👔 **Bandeja de Decisión Jefatura SST:**  
  👉 **[https://disjoya-control-horarios.vercel.app/jefe](https://disjoya-control-horarios.vercel.app/jefe)**
* ⚙️ **Panel Superadministrador:**  
  👉 **[https://disjoya-control-horarios.vercel.app/admin](https://disjoya-control-horarios.vercel.app/admin)**
* 📄 **Desprendible PDF de Pruebas (Pedro Noriega):**  
  👉 **[Descargar PDF Pedro Noriega (2026-08)](https://disjoya-control-horarios.vercel.app/api/v1/nomina/autopartes/desprendible/1020304050/2026/8/pdf)**

---

### 3. Cuentas de Acceso del Sistema

Contraseña unificada para pruebas: **`Disjoya2026*`**

| Rol | Correo Electrónico | Documento | Funcionalidad Clave |
|---|---|---|---|
| **Administrador** | `pedro.noriega@gmail.com` | `1020304050` | Control total, liquidación nómina, auditoría |
| **Jefe Inmediato SST** | `tminero.sst@gmail.com` | N/A | Aprobación / Rechazo de Horas Extras |
| **Trabajador Operativo**| `pnoriegawork@gmail.com` | `1020304050` | Marcación entrada/salida y solicitud de horas extras |

---

### 4. Arquitectura y Base de Datos (Supabase PostgreSQL)

* **Host:** `aws-0-us-east-2.pooler.supabase.com:6543` / `postgres`
* **Tablas activas:**
  1. `empleados`
  2. `sedes`
  3. `turnos`
  4. `marcaciones`
  5. `solicitudes_horas_extras`
  6. `novedades_ausentismo`
  7. `alertas_inasistencia`
  8. `horas_extras_ledger` (Doble contabilización y banco de arrastre)
  9. `comisiones_vendedores` (Art. 127 CST)
  10. `liquidaciones_nomina` (Liquidación oficial y hash de desprendible)

---

### 5. Comandos para Reanudar en la Siguiente Sesión

```powershell
# 1. Abrir terminal en la carpeta del proyecto
cd "c:\Users\Estudiante 08\Documents\Pedro_Noriega\Aplicaciones IA\Sistema de control de horarios_Disjoya"

# 2. Correr las pruebas unitarias (11/11 tests passing)
.\.venv\Scripts\pytest -v

# 3. Iniciar el servidor local si se requiere desarrollo
.\.venv\Scripts\uvicorn app.main:app --reload --port 8000

# 4. Desplegar cualquier cambio a producción en Vercel
.\.venv\Scripts\python.exe deploy_vercel.py
```
