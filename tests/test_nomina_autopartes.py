"""Pruebas integrales de producción para:
- Doble Contabilización y Libro Mayor de Arrastre de Horas Extras (Carry-Over Ledger)
- Gestión de Novedades (Vacaciones Art. 186 CST, Incapacidad EPS, Incapacidad ARL)
- Alerta Automática de Inasistencia a los 30 minutos hacia `tminero.sst@gmail.com`
- Comisiones Salariales de Vendedores (Art. 127 CST) y Liquidación Plantilla Autopartes
- Generación de Desprendibles Individuales en PDF con firma SHA-256."""
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.alertas_inasistencia import evaluar_omisiones_30_minutos
from app.database import Base
from app.desprendibles_pdf import generar_pdf_desprendible_individual, numero_a_letras_cop
from app.models import CentroTrabajo, Empleado, JEFE_OFICIAL_EMAIL, NovedadAusentismo, TurnoProgramado
from app.nomina_autopartes import (
    ejecutar_doble_contabilizacion_he,
    liquidar_empleado_autopartes,
    registrar_comision_vendedor,
)
from app.security import cifrar, hash_password, sha256_hex


def _crear_sesion_memoria():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, expire_on_commit=False)
    return Session()


def test_doble_contabilizacion_y_arrastre_mes_a_mes():
    db = _crear_sesion_memoria()
    emp = Empleado(
        tipo_documento="CC",
        numero_documento="91272054",
        nombres="HUMBERTO",
        apellidos="AVILA ACUÑA",
        email="havila@disjoya.com.co",
        rol="TRABAJADOR",
        cargo="Vendedor / Asesor Comercial",
        salario_base=Decimal("1750905.00"),
        password_hash=hash_password("Disjoya2026*"),
    )
    db.add(emp)
    db.commit()

    # Mes 8 (Agosto 2026): 19h preautorizadas (1h diaria L-V) + 35h adicionales aprobadas por tminero.sst@gmail.com = 54h reales.
    # Tope legal pagable en el mes = 48h -> Paga 48h en Cuenta 1 y arrastra 6h en Cuenta 2 para Septiembre.
    ledger_ago = ejecutar_doble_contabilizacion_he(
        db=db,
        empleado=emp,
        anio=2026,
        mes=8,
        valor_hora_ordinaria=Decimal("10816.64"),
        horas_preautorizadas_override=Decimal("19.00"),
        horas_adicionales_override=Decimal("35.00"),
        tope_mensual_pagable=Decimal("48.00"),
    )
    db.commit()

    assert ledger_ago.total_horas_banco_real == Decimal("54.00")
    assert ledger_ago.horas_pagadas_nomina_mes == Decimal("48.00")
    assert ledger_ago.saldo_arrastre_siguiente_mes == Decimal("6.00")
    assert ledger_ago.aprobador_email == JEFE_OFICIAL_EMAIL

    # Mes 9 (Septiembre 2026): 19h preautorizadas + 0h adicionales + 6h arrastradas de Agosto = 25h -> Paga 25h y deja saldo en 0.00
    ledger_sep = ejecutar_doble_contabilizacion_he(
        db=db,
        empleado=emp,
        anio=2026,
        mes=9,
        valor_hora_ordinaria=Decimal("10816.64"),
        horas_preautorizadas_override=Decimal("19.00"),
        horas_adicionales_override=Decimal("0.00"),
        tope_mensual_pagable=Decimal("48.00"),
    )
    db.commit()

    assert ledger_sep.saldo_arrastre_mes_anterior == Decimal("6.00")
    assert ledger_sep.total_horas_banco_real == Decimal("25.00")
    assert ledger_sep.horas_pagadas_nomina_mes == Decimal("25.00")
    assert ledger_sep.saldo_arrastre_siguiente_mes == Decimal("0.00")


def test_alerta_30_minutos_y_bloqueo_por_vacaciones_o_incapacidad():
    db = _crear_sesion_memoria()
    centro = CentroTrabajo(
        nombre="Sede Principal Disjoya Bucaramanga",
        direccion="Cl. 23 #13-32",
        municipio_dane="68001",
        latitud=Decimal("7.124500"),
        longitud=Decimal("-73.128000"),
        radio_metros=80,
        secreto_totp_cifrado=cifrar(b"12345678901234567890", "centro:test"),
        kiosco_api_key_hash=sha256_hex("test-key"),
    )
    db.add(centro)
    db.flush()

    emp_activo = Empleado(
        tipo_documento="CC",
        numero_documento="1005333205",
        nombres="KARON NATHALIA",
        apellidos="GALVIS RODRIGUEZ",
        email="kgalvis@disjoya.com.co",
        rol="TRABAJADOR",
        cargo="Auxiliar Administrativa",
        salario_base=Decimal("2340000.00"),
        centro_trabajo_id=centro.id,
        password_hash=hash_password("Disjoya2026*"),
    )
    emp_incapacitado = Empleado(
        tipo_documento="CC",
        numero_documento="63332050",
        nombres="ELIZABETH",
        apellidos="PRADA RODRIGUEZ",
        email="eprada@disjoya.com.co",
        rol="TRABAJADOR",
        cargo="Analista Contable",
        salario_base=Decimal("2596000.00"),
        centro_trabajo_id=centro.id,
        password_hash=hash_password("Disjoya2026*"),
    )
    db.add_all([emp_activo, emp_incapacitado])
    db.flush()

    # Turno programado 2026-11-17 08:00 a.m. Bogotá (13:00 UTC)
    inicio_turno = datetime(2026, 11, 17, 13, 0, tzinfo=timezone.utc)
    fin_turno = datetime(2026, 11, 17, 22, 0, tzinfo=timezone.utc)
    db.add_all(
        [
            TurnoProgramado(
                empleado_id=emp_activo.id,
                centro_trabajo_id=centro.id,
                fecha=date(2026, 11, 17),
                inicio=inicio_turno,
                fin=fin_turno,
                creado_por_id=emp_activo.id,
            ),
            TurnoProgramado(
                empleado_id=emp_incapacitado.id,
                centro_trabajo_id=centro.id,
                fecha=date(2026, 11, 17),
                inicio=inicio_turno,
                fin=fin_turno,
                creado_por_id=emp_incapacitado.id,
            ),
        ]
    )

    # Registrar incapacidad médica EPS activa para Elizabeth Prada -> bloquea exigencia de marcación y alerta
    db.add(
        NovedadAusentismo(
            empleado_id=emp_incapacitado.id,
            tipo_novedad="INCAPACIDAD_EPS",
            fecha_inicio=date(2026, 11, 16),
            fecha_fin=date(2026, 11, 20),
            dias_calendario=5,
            dias_habiles=5,
            entidad_responsable="EPS Sanitas",
            exime_marcacion_qr=True,
            estado="ACTIVA",
        )
    )
    db.commit()

    # Evaluar a las 08:35 a.m. Bogotá (13:35 UTC -> +35 minutos de retraso)
    ahora_evaluacion = datetime(2026, 11, 17, 13, 35, tzinfo=timezone.utc)
    alertas = evaluar_omisiones_30_minutos(db, ahora_evaluacion)

    # Solo debe disparar alerta para Karon Nathalia (emp_activo) y exonerar a Elizabeth (emp_incapacitado)
    assert len(alertas) == 1
    assert alertas[0]["documento"] == "1005333205"
    assert alertas[0]["jefe_notificado"] == "tminero.sst@gmail.com"
    assert alertas[0]["minutos_retraso"] == 35


def test_comisiones_vendedores_y_generacion_pdf(tmp_path):
    db = _crear_sesion_memoria()
    vendedor = Empleado(
        tipo_documento="CC",
        numero_documento="91495502",
        nombres="JOHN JAIRO",
        apellidos="ROJAS HERREÑO",
        email="jrojas@disjoya.com.co",
        rol="TRABAJADOR",
        cargo="Vendedor / Asesor Comercial",
        salario_base=Decimal("1750905.00"),
        password_hash=hash_password("Disjoya2026*"),
    )
    db.add(vendedor)
    db.commit()

    registrar_comision_vendedor(db, vendedor, 2026, 8, Decimal("0"), Decimal("465004"))
    liq = liquidar_empleado_autopartes(
        db,
        vendedor,
        2026,
        8,
        horas_preautorizadas_override=Decimal("19"),
    )
    db.commit()

    # Verificar cuadre exacto con fila 17 y fila 26 de "NOMINA DE AUTOPARTES para sr pedro.xlsx"
    assert liq.sueldo_base_calculo == Decimal("2215909.00")
    assert liq.total_horas_extras_cop == Decimal("250608.76")

    ruta_pdf, sha = generar_pdf_desprendible_individual(vendedor, liq, directorio_salida=tmp_path)
    assert ruta_pdf.exists()
    assert len(sha) == 64
    assert "PESOS M/CTE" in numero_a_letras_cop(liq.neto_a_pagar)
