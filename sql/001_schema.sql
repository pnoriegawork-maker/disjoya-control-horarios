-- ============================================================================
-- SISTEMA AGÉNTICO AUTÓNOMO DE CONTROL HORARIO, HORAS EXTRAS Y PRE-LIQUIDACIÓN
-- Esquema DDL de Base de Datos Relacional (PostgreSQL 15+ / Supabase)
-- Jurisdicción: República de Colombia | Zona Horaria: America/Bogota (UTC-5)
-- Marco Legal: CST, Ley 2101 de 2021, Ley 50 de 1990, Ley 1581 de 2012
-- ============================================================================

-- 0. EXTENSIONES Y CONFIGURACIÓN CRIPTOGRÁFICA
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Parámetros de sesión predeterminados
SET timezone = 'UTC';

-- 1. TIPOS ENUMERADOS DETERMINÍSTICOS
DO $$ BEGIN
    CREATE TYPE rol_usuario AS ENUM (
        'TRABAJADOR',
        'JEFE_INMEDIATO',
        'NOMINA_RRHH',
        'AUDITOR_SUPERADMIN'
    );
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
    CREATE TYPE tipo_marcacion AS ENUM (
        'ENTRADA',
        'SALIDA'
    );
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
    CREATE TYPE estado_solicitud_he AS ENUM (
        'PENDIENTE',
        'APROBADA',
        'RECHAZADA_JEFE',
        'RECHAZADA_AUTOMATICA',
        'CONCILIADA',
        'ANULADA'
    );
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
    CREATE TYPE tipo_novedad_jornada AS ENUM (
        'LLEGADA_TARDE',
        'SALIDA_ANTICIPADA',
        'TIEMPO_SUPLEMENTARIO_NO_AUTORIZADO',
        'HE_APROBADA_NO_LABORADA',
        'JORNADA_SIN_SALIDA',
        'EXCESO_JORNADA_SEMANAL'
    );
EXCEPTION WHEN duplicate_object THEN null; END $$;

-- 2. TABLA: CENTROS DE TRABAJO (Sedes operativas con geocercas)
CREATE TABLE IF NOT EXISTS centros_trabajo (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre VARCHAR(150) NOT NULL,
    direccion VARCHAR(250) NOT NULL,
    municipio_dane VARCHAR(5) NOT NULL,
    latitud NUMERIC(9, 6) NOT NULL,
    longitud NUMERIC(9, 6) NOT NULL,
    radio_metros INTEGER NOT NULL DEFAULT 50,
    poligono JSONB NULL,
    secreto_totp_cifrado BYTEA NOT NULL,
    kiosco_api_key_hash VARCHAR(64) NOT NULL UNIQUE,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_centro_radio CHECK (radio_metros BETWEEN 10 AND 1000),
    CONSTRAINT ck_centro_coords CHECK (
        latitud BETWEEN -4.3 AND 16.0 AND
        longitud BETWEEN -82.0 AND -66.8
    )
);

-- 3. TABLA: EMPLEADOS
CREATE TABLE IF NOT EXISTS empleados (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo_documento VARCHAR(3) NOT NULL CHECK (tipo_documento IN ('CC', 'CE', 'PPT', 'PA', 'TI')),
    numero_documento VARCHAR(20) NOT NULL UNIQUE,
    nombres VARCHAR(120) NOT NULL,
    apellidos VARCHAR(120) NOT NULL,
    email VARCHAR(200) NOT NULL UNIQUE,
    telefono_whatsapp VARCHAR(20) NULL,
    rol rol_usuario NOT NULL DEFAULT 'TRABAJADOR',
    cargo VARCHAR(120) NOT NULL,
    jefe_inmediato_id UUID NULL REFERENCES empleados(id) ON DELETE SET NULL,
    centro_trabajo_id UUID NULL REFERENCES centros_trabajo(id) ON DELETE RESTRICT,
    fecha_nacimiento DATE NULL,
    salario_base NUMERIC(14, 2) NOT NULL,
    salario_integral BOOLEAN NOT NULL DEFAULT FALSE,
    es_direccion_confianza BOOLEAN NOT NULL DEFAULT FALSE,
    password_hash VARCHAR(200) NOT NULL,
    dispositivo_hash VARCHAR(64) NULL,
    dispositivo_registrado_en TIMESTAMPTZ NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_empleado_salario CHECK (salario_base > 0),
    CONSTRAINT ck_empleado_no_autojefe CHECK (jefe_inmediato_id IS NULL OR jefe_inmediato_id <> id)
);

-- 4. TABLA: CONSENTIMIENTOS HABEAS DATA (Ley 1581 de 2012)
CREATE TABLE IF NOT EXISTS consentimientos_habeas_data (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empleado_id UUID NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
    version_politica VARCHAR(20) NOT NULL,
    hash_texto_politica VARCHAR(64) NOT NULL,
    acepta_tratamiento_laboral BOOLEAN NOT NULL,
    acepta_geolocalizacion BOOLEAN NOT NULL,
    otorgado_en TIMESTAMPTZ NOT NULL,
    ip_origen INET NULL,
    user_agent VARCHAR(300) NULL,
    revocado_en TIMESTAMPTZ NULL
);

-- 5. TABLA: TURNOS PROGRAMADOS (Jornada ordinaria contractual)
CREATE TABLE IF NOT EXISTS turnos_programados (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empleado_id UUID NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
    centro_trabajo_id UUID NOT NULL REFERENCES centros_trabajo(id) ON DELETE RESTRICT,
    fecha DATE NOT NULL,
    inicio TIMESTAMPTZ NOT NULL,
    fin TIMESTAMPTZ NOT NULL,
    creado_por_id UUID NOT NULL REFERENCES empleados(id),
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_turno_rango CHECK (fin > inicio),
    CONSTRAINT ck_turno_duracion CHECK (EXTRACT(EPOCH FROM (fin - inicio)) <= 43200),
    CONSTRAINT uq_turno_empleado_inicio UNIQUE (empleado_id, inicio)
);

-- 6. TABLA: MARCACIONES QR DINÁMICAS (Eventos de acceso antifraude)
CREATE TABLE IF NOT EXISTS marcaciones_qr (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empleado_id UUID NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
    centro_trabajo_id UUID NOT NULL REFERENCES centros_trabajo(id) ON DELETE RESTRICT,
    tipo tipo_marcacion NOT NULL,
    marca_tiempo TIMESTAMPTZ NOT NULL,
    fecha_local DATE NOT NULL,
    ntp_sincronizado BOOLEAN NOT NULL,
    ntp_desfase_ms INTEGER NOT NULL,
    ventana_totp BIGINT NOT NULL,
    coordenadas_cifradas BYTEA NOT NULL,
    distancia_metros NUMERIC(8, 2) NOT NULL,
    precision_gps_m NUMERIC(7, 2) NOT NULL,
    dispositivo_hash VARCHAR(64) NOT NULL,
    ip_origen INET NULL,
    user_agent VARCHAR(300) NULL,
    sello_integridad VARCHAR(64) NOT NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_marcacion_antireplay UNIQUE (empleado_id, centro_trabajo_id, ventana_totp)
);

-- 7. TABLA: SOLICITUDES DE HORAS EXTRAS (Workflow legal Art. 22 Ley 50/1990)
CREATE TABLE IF NOT EXISTS solicitudes_horas_extras (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empleado_id UUID NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
    solicitado_por_id UUID NOT NULL REFERENCES empleados(id),
    jefe_aprobador_id UUID NULL REFERENCES empleados(id),
    fecha DATE NOT NULL,
    inicio_programado TIMESTAMPTZ NOT NULL,
    fin_programado TIMESTAMPTZ NOT NULL,
    horas_solicitadas NUMERIC(5, 2) NOT NULL,
    motivo TEXT NOT NULL,
    estado estado_solicitud_he NOT NULL DEFAULT 'PENDIENTE',
    motivo_rechazo TEXT NULL,
    comentario_jefe TEXT NULL,
    decision_en TIMESTAMPTZ NULL,
    aprobacion_extemporanea BOOLEAN NOT NULL DEFAULT FALSE,
    horas_reales NUMERIC(5, 2) NULL,
    horas_reconocidas NUMERIC(5, 2) NULL,
    marcacion_salida_id UUID NULL REFERENCES marcaciones_qr(id) ON DELETE SET NULL,
    conciliado_en TIMESTAMPTZ NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_he_rango CHECK (fin_programado > inicio_programado),
    CONSTRAINT ck_he_max_diario CHECK (horas_solicitadas > 0 AND horas_solicitadas <= 2.00),
    CONSTRAINT ck_he_reconocidas CHECK (horas_reconocidas IS NULL OR horas_reconocidas <= horas_solicitadas)
);

-- 8. TABLA: NOVEDADES DE JORNADA (MAPE-K Monitor/Analyze)
CREATE TABLE IF NOT EXISTS novedades_jornada (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empleado_id UUID NOT NULL REFERENCES empleados(id) ON DELETE CASCADE,
    fecha DATE NOT NULL,
    tipo tipo_novedad_jornada NOT NULL,
    minutos INTEGER NOT NULL DEFAULT 0,
    detalle TEXT NOT NULL,
    resuelta BOOLEAN NOT NULL DEFAULT FALSE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 9. TABLA: BITÁCORA DE AUDITORÍA FORENSE INMUTABLE (Hash-Chain SHA-256)
CREATE TABLE IF NOT EXISTS auditoria_logs (
    id BIGSERIAL PRIMARY KEY,
    ocurrido_en TIMESTAMPTZ NOT NULL,
    actor_id UUID NULL,
    actor_rol VARCHAR(20) NOT NULL,
    accion VARCHAR(60) NOT NULL,
    entidad VARCHAR(60) NOT NULL,
    entidad_id VARCHAR(64) NULL,
    ip_origen INET NULL,
    dispositivo_hash VARCHAR(64) NULL,
    user_agent VARCHAR(300) NULL,
    datos JSONB NOT NULL,
    hash_anterior VARCHAR(64) NOT NULL,
    hash_actual VARCHAR(64) NOT NULL UNIQUE
);

-- ============================================================================
-- ÍNDICES ESTRATÉGICOS PARA RENDIMIENTO Y COMPLIANCE
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_empleados_doc ON empleados (numero_documento);
CREATE INDEX IF NOT EXISTS idx_empleados_jefe ON empleados (jefe_inmediato_id);
CREATE INDEX IF NOT EXISTS idx_empleados_dispositivo ON empleados (dispositivo_hash) WHERE dispositivo_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_turnos_emp_fecha ON turnos_programados (empleado_id, fecha);
CREATE INDEX IF NOT EXISTS idx_marcaciones_emp_tiempo ON marcaciones_qr (empleado_id, marca_tiempo);
CREATE INDEX IF NOT EXISTS idx_marcaciones_centro_tiempo ON marcaciones_qr (centro_trabajo_id, marca_tiempo);
CREATE INDEX IF NOT EXISTS idx_solicitudes_he_emp_fecha ON solicitudes_horas_extras (empleado_id, fecha, estado);
CREATE INDEX IF NOT EXISTS idx_solicitudes_he_jefe ON solicitudes_horas_extras (jefe_aprobador_id, estado);
CREATE INDEX IF NOT EXISTS idx_novedades_emp_fecha ON novedades_jornada (empleado_id, fecha);
CREATE INDEX IF NOT EXISTS idx_auditoria_ocurrido ON auditoria_logs (ocurrido_en);
CREATE INDEX IF NOT EXISTS idx_auditoria_entidad ON auditoria_logs (entidad, entidad_id);

-- ============================================================================
-- TRIGGERS DE SEGURIDAD E INMUTABILIDAD FORENSE (WORM - Write Once, Read Many)
-- ============================================================================

-- A. Prohibición estricta de modificación o eliminación en auditoria_logs
CREATE OR REPLACE FUNCTION trg_auditoria_inmutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'VIOLACIÓN DE INTEGRIDAD FORENSE: La tabla auditoria_logs es estrictamente inmutable (WORM). No se permiten operaciones de UPDATE, DELETE ni TRUNCATE.'
        USING ERRCODE = '55000';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS t_proteger_auditoria ON auditoria_logs;
CREATE TRIGGER t_proteger_auditoria
    BEFORE UPDATE OR DELETE ON auditoria_logs
    FOR EACH ROW EXECUTE FUNCTION trg_auditoria_inmutable();

-- B. Prohibición de manipulación de marcaciones_qr (Inmutabilidad de marcas biométricas/QR)
CREATE OR REPLACE FUNCTION trg_marcacion_inmutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'VIOLACIÓN PROBATORIA: Las marcaciones horarias no pueden ser alteradas ni eliminadas (Art. 158 CST).'
        USING ERRCODE = '55000';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS t_proteger_marcaciones ON marcaciones_qr;
CREATE TRIGGER t_proteger_marcaciones
    BEFORE UPDATE OR DELETE ON marcaciones_qr
    FOR EACH ROW EXECUTE FUNCTION trg_marcacion_inmutable();

-- ============================================================================
-- POLÍTICAS DE SEGURIDAD A NIVEL DE FILA (ROW LEVEL SECURITY - RLS)
-- ============================================================================
ALTER TABLE empleados ENABLE ROW LEVEL SECURITY;
ALTER TABLE consentimientos_habeas_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE turnos_programados ENABLE ROW LEVEL SECURITY;
ALTER TABLE marcaciones_qr ENABLE ROW LEVEL SECURITY;
ALTER TABLE solicitudes_horas_extras ENABLE ROW LEVEL SECURITY;
ALTER TABLE novedades_jornada ENABLE ROW LEVEL SECURITY;
ALTER TABLE auditoria_logs ENABLE ROW LEVEL SECURITY;

-- Helper functions para extraer contexto de sesión
CREATE OR REPLACE FUNCTION current_app_empleado_id() RETURNS UUID AS $$
BEGIN
    RETURN NULLIF(current_setting('app.empleado_id', true), '')::UUID;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE;

CREATE OR REPLACE FUNCTION current_app_rol() RETURNS VARCHAR AS $$
BEGIN
    RETURN COALESCE(NULLIF(current_setting('app.rol', true), ''), 'ANONIMO');
END;
$$ LANGUAGE plpgsql STABLE;

-- RLS: EMPLEADOS
CREATE POLICY p_empleados_select ON empleados FOR SELECT USING (
    current_app_rol() IN ('AUDITOR_SUPERADMIN', 'NOMINA_RRHH', 'SISTEMA')
    OR id = current_app_empleado_id()
    OR jefe_inmediato_id = current_app_empleado_id()
);

-- RLS: MARCACIONES_QR
CREATE POLICY p_marcaciones_select ON marcaciones_qr FOR SELECT USING (
    current_app_rol() IN ('AUDITOR_SUPERADMIN', 'NOMINA_RRHH', 'SISTEMA')
    OR empleado_id = current_app_empleado_id()
    OR EXISTS (
        SELECT 1 FROM empleados e
        WHERE e.id = marcaciones_qr.empleado_id
        AND e.jefe_inmediato_id = current_app_empleado_id()
    )
);

CREATE POLICY p_marcaciones_insert ON marcaciones_qr FOR INSERT WITH CHECK (
    current_app_rol() IN ('SISTEMA')
    OR empleado_id = current_app_empleado_id()
);

-- RLS: SOLICITUDES_HORAS_EXTRAS
CREATE POLICY p_solicitudes_he_select ON solicitudes_horas_extras FOR SELECT USING (
    current_app_rol() IN ('AUDITOR_SUPERADMIN', 'NOMINA_RRHH', 'SISTEMA')
    OR empleado_id = current_app_empleado_id()
    OR jefe_aprobador_id = current_app_empleado_id()
);

CREATE POLICY p_solicitudes_he_insert ON solicitudes_horas_extras FOR INSERT WITH CHECK (
    current_app_rol() IN ('SISTEMA', 'NOMINA_RRHH')
    OR empleado_id = current_app_empleado_id()
    OR jefe_aprobador_id = current_app_empleado_id()
);

CREATE POLICY p_solicitudes_he_update ON solicitudes_horas_extras FOR UPDATE USING (
    current_app_rol() IN ('AUDITOR_SUPERADMIN', 'NOMINA_RRHH', 'SISTEMA')
    OR jefe_aprobador_id = current_app_empleado_id()
);

-- RLS: AUDITORIA_LOGS
CREATE POLICY p_auditoria_select ON auditoria_logs FOR SELECT USING (
    current_app_rol() IN ('AUDITOR_SUPERADMIN', 'SISTEMA')
);

CREATE POLICY p_auditoria_insert ON auditoria_logs FOR INSERT WITH CHECK (
    current_app_rol() IN ('SISTEMA', 'AUDITOR_SUPERADMIN', 'NOMINA_RRHH', 'JEFE_INMEDIATO', 'TRABAJADOR')
);

