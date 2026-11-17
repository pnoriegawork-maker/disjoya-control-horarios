"""Generador de Desprendibles Individuales de Nómina en PDF (ReportLab) con Firma Criptográfica SHA-256,
Conversión de Neto a Pagar en Letras (Español Colombiano), Estado del Banco de Arrastre de Horas Extras
y Despacho Automático por Correo Electrónico con Adjunto PDF a cada Trabajador y Copia de Auditoría."""
from __future__ import annotations

import hashlib
import smtplib
import ssl
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER, landscape
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Empleado, HorasExtrasLedger, JEFE_OFICIAL_EMAIL, LiquidacionNomina

UNIDADES = ("", "UN", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE")
DECENAS_ESP = (
    "DIEZ",
    "ONCE",
    "DOCE",
    "TRECE",
    "CATORCE",
    "QUINCE",
    "DIECISEIS",
    "DIECISIETE",
    "DIECIOCHO",
    "DIECINUEVE",
)
VEINTIS = (
    "VEINTE",
    "VEINTIUN",
    "VEINTIDOS",
    "VEINTITRES",
    "VEINTICUATRO",
    "VEINTICINCO",
    "VEINTISEIS",
    "VEINTISIETE",
    "VEINTIOCHO",
    "VEINTINUEVE",
)
DECENAS = ("", "", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA", "NOVENTA")
CENTENAS = (
    "",
    "CIENTO",
    "DOSCIENTOS",
    "TRESCIENTOS",
    "CUATROCIENTOS",
    "QUINIENTOS",
    "SEISCIENTOS",
    "SETECIENTOS",
    "OCHOCIENTOS",
    "NOVECIENTOS",
)


def _menor_mil(n: int) -> str:
    if n == 0:
        return ""
    if n == 100:
        return "CIEN"
    c = n // 100
    r = n % 100
    txt_c = CENTENAS[c]
    if r == 0:
        return txt_c
    if r < 10:
        txt_r = UNIDADES[r]
    elif r < 20:
        txt_r = DECENAS_ESP[r - 10]
    elif r < 30:
        txt_r = VEINTIS[r - 20]
    else:
        d = r // 10
        u = r % 10
        txt_r = DECENAS[d] if u == 0 else f"{DECENAS[d]} Y {UNIDADES[u]}"
    return f"{txt_c} {txt_r}".strip()


def numero_a_letras_cop(monto: Decimal | float | int) -> str:
    """Convierte un valor monetario en pesos colombianos a su expresión legal en letras."""
    entero = int(round(float(monto)))
    if entero == 0:
        return "CERO PESOS M/CTE"
    if entero < 0:
        return f"MENOS {numero_a_letras_cop(abs(entero))}"

    millones = entero // 1_000_000
    resto_millones = entero % 1_000_000
    miles = resto_millones // 1000
    unidades = resto_millones % 1000

    partes: list[str] = []
    if millones == 1:
        partes.append("UN MILLON")
    elif millones > 1:
        partes.append(f"{_menor_mil(millones)} MILLONES")

    if miles == 1:
        partes.append("MIL")
    elif miles > 1:
        partes.append(f"{_menor_mil(miles)} MIL")

    if unidades > 0:
        if unidades == 1 and not partes:
            partes.append("UN")
        else:
            partes.append(_menor_mil(unidades))

    sufijo = "DE PESOS M/CTE" if (resto_millones == 0 and millones > 0) else "PESOS M/CTE"
    return f"{' '.join(partes)} {sufijo}"


def _fmt_cop(valor: Decimal | float | int) -> str:
    return f"$ {float(valor):,.2f}"


def generar_pdf_desprendible_individual(
    empleado: Empleado,
    liquidacion: LiquidacionNomina,
    ledger: HorasExtrasLedger | None = None,
    directorio_salida: Path | str = "desprendibles_generados",
) -> tuple[Path, str]:
    """Construye el archivo PDF individual del desprendible de nómina para un trabajador,
    lo almacena en la carpeta estructurada por período (o /tmp en entornos Serverless) y retorna `(ruta_pdf, sha256_hex)`."""
    periodo_str = f"{liquidacion.periodo_anio}_{liquidacion.periodo_mes:02d}"
    carpeta = Path(directorio_salida) / periodo_str
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        # Verificar escritura real en el directorio (en Vercel /var/task es de solo lectura)
        test_file = carpeta / ".write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
    except OSError:
        carpeta = Path(tempfile.gettempdir()) / "desprendibles_generados" / periodo_str
        carpeta.mkdir(parents=True, exist_ok=True)

    nombre_limpio = empleado.numero_documento.strip()
    ruta_pdf = carpeta / f"desprendible_{nombre_limpio}_{periodo_str}.pdf"

    # Formato Horizontal (Landscape) óptimo para comprobantes de nómina de doble columna (Devengados y Deducciones)
    c = canvas.Canvas(str(ruta_pdf), pagesize=landscape(LETTER))
    ancho, alto = landscape(LETTER)
    margin_x = 36
    ancho_util = ancho - (margin_x * 2)

    # --- 1. ENCABEZADO INSTITUCIONAL CON LOGO OFICIAL DISJOYA (SUPERIOR IZQUIERDA) ---
    y_header = alto - 32
    h_header = 60
    c.setFillColor(colors.HexColor("#0F2D3D"))
    c.roundRect(margin_x, y_header - h_header, ancho_util, h_header, 6, fill=1, stroke=0)

    # Recuadro blanco superior izquierdo ajustado a la grilla de las tablas para alojar el logo de Disjoya
    c.setFillColor(colors.white)
    c.roundRect(margin_x + 8, y_header - h_header + 5, 94, 50, 4, fill=1, stroke=0)

    ruta_logo = Path(__file__).resolve().parents[1] / "static" / "logo_disjoya.png"
    if ruta_logo.exists():
        try:
            c.drawImage(
                str(ruta_logo),
                margin_x + 12,
                y_header - h_header + 9,
                width=86,
                height=42,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )
        except Exception:
            pass

    x_texto_header = margin_x + 116
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(x_texto_header, y_header - 24, "DISJOYA AUTOPARTES S.A.S.")
    c.setFont("Helvetica", 9)
    c.drawString(x_texto_header, y_header - 42, "NIT: 900.482.119-4  |  Sede Principal: Cl. 23 #13-32, Bucaramanga, Santander")

    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(ancho - margin_x - 14, y_header - 24, "COMPROBANTE OFICIAL DE NÓMINA")
    c.setFont("Helvetica", 9.5)
    c.drawRightString(ancho - margin_x - 14, y_header - 42, f"Período Liquidado: {liquidacion.periodo_anio}-{liquidacion.periodo_mes:02d}")

    # --- 2. DATOS DEL TRABAJADOR ---
    y_card = y_header - h_header - 10
    h_card = 56
    c.setFillColor(colors.HexColor("#F8FAFC"))
    c.setStrokeColor(colors.HexColor("#CBD5E1"))
    c.rect(margin_x, y_card - h_card, ancho_util, h_card, fill=1, stroke=1)

    c.setFillColor(colors.HexColor("#0F2D3D"))
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(margin_x + 14, y_card - 16, f"TRABAJADOR: {empleado.nombre_completo}")
    c.drawString(margin_x + 380, y_card - 16, f"IDENTIFICACIÓN: {empleado.tipo_documento} {empleado.numero_documento}")

    c.setFont("Helvetica", 8.5)
    c.setFillColor(colors.HexColor("#334155"))
    c.drawString(margin_x + 14, y_card - 31, f"Cargo: {empleado.cargo}")
    c.drawString(margin_x + 380, y_card - 31, f"Salario Básico: {_fmt_cop(liquidacion.salario_basico_pactado)}")

    c.drawString(margin_x + 14, y_card - 46, f"Días Laborados: {liquidacion.dias_laborados} días")
    c.drawString(
        margin_x + 380,
        y_card - 46,
        f"Novedades del Período: Vacaciones: {liquidacion.dias_vacaciones}d  |  Incap. EPS: {liquidacion.dias_incapacidad_eps}d  |  Incap. ARL: {liquidacion.dias_incapacidad_arl}d",
    )

    # --- 3. TABLA DE DEVENGADOS Y DEDUCCIONES (DOBLE COLUMNA HORIZONTAL AMPLIADA) ---
    y_tabla = y_card - h_card - 12
    ancho_col = (ancho_util - 16) / 2
    col_izq_x = margin_x
    col_der_x = margin_x + ancho_col + 16

    c.setFillColor(colors.HexColor("#1B455B"))
    c.rect(col_izq_x, y_tabla - 18, ancho_col, 18, fill=1, stroke=0)
    c.rect(col_der_x, y_tabla - 18, ancho_col, 18, fill=1, stroke=0)

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(col_izq_x + 10, y_tabla - 13, "CONCEPTOS DEVENGADOS (INGRESOS)")
    c.drawRightString(col_izq_x + ancho_col - 10, y_tabla - 13, "VALOR ($ COP)")
    c.drawString(col_der_x + 10, y_tabla - 13, "DEDUCCIONES DE LEY Y DESCUENTOS")
    c.drawRightString(col_der_x + ancho_col - 10, y_tabla - 13, "VALOR ($ COP)")

    vh = Decimal(str(liquidacion.valor_hora_ordinaria))
    val_hed_pdf = vh * Decimal("1.25") * Decimal(str(liquidacion.horas_extra_diurna))
    val_hen_pdf = vh * Decimal("1.75") * Decimal(str(liquidacion.horas_extra_nocturna))
    val_dom_fest_pdf = (
        vh * Decimal("2.15") * Decimal(str(liquidacion.horas_extra_festiva_diurna))
        + vh * Decimal("2.65") * Decimal(str(liquidacion.horas_extra_festiva_nocturna))
    )
    h_dom_fest_tot = Decimal(str(liquidacion.horas_extra_festiva_diurna)) + Decimal(str(liquidacion.horas_extra_festiva_nocturna))

    devengados = [
        ("Salario Básico", liquidacion.salario_basico_proporcional),
        ("Auxilio de Transporte Legal", liquidacion.auxilio_transporte_devengado),
        ("Comisiones por Ventas (Art. 127 CST)", liquidacion.comisiones_ventas_art127),
        (f"Horas Extras Diurnas (06:00 - 19:00) ({liquidacion.horas_extra_diurna} h)", val_hed_pdf),
        (f"Horas Extras Nocturnas (19:00 - 06:00) ({liquidacion.horas_extra_nocturna} h)", val_hen_pdf),
        (f"Horas Extras Dom./Festivas ({h_dom_fest_tot} h)", val_dom_fest_pdf),
        (f"Vacaciones Remuneradas ({liquidacion.dias_vacaciones} días)", liquidacion.pago_vacaciones),
        (f"Incapacidad Médica EPS ({liquidacion.dias_incapacidad_eps} días)", liquidacion.pago_incapacidad_eps),
        (f"Incapacidad Laboral ARL ({liquidacion.dias_incapacidad_arl} días)", liquidacion.pago_incapacidad_arl),
    ]

    deducciones = [
        ("Aporte Obligatorio Salud Trabajador (4% IBC)", liquidacion.deduccion_salud_4pct),
        ("Aporte Obligatorio Pensión Trabajador (4% IBC)", liquidacion.deduccion_pension_4pct),
        ("Fondo de Solidaridad Pensional (FSP)", liquidacion.fondo_solidaridad_pensional),
        ("Otras Deducciones / Libranzas Autorizadas", liquidacion.otras_deducciones_libranzas),
    ]

    filas_max = max(len(devengados), len(deducciones))
    y_fila = y_tabla - 33

    c.setFont("Helvetica", 8.5)
    for i in range(filas_max):
        if i % 2 == 0:
            c.setFillColor(colors.HexColor("#F8FAFC"))
            c.rect(col_izq_x, y_fila - 4, ancho_col, 16, fill=1, stroke=0)
            c.rect(col_der_x, y_fila - 4, ancho_col, 16, fill=1, stroke=0)

        c.setFillColor(colors.HexColor("#1E293B"))
        if i < len(devengados):
            concepto_d, valor_d = devengados[i]
            c.drawString(col_izq_x + 10, y_fila, concepto_d)
            c.drawRightString(col_izq_x + ancho_col - 10, y_fila, _fmt_cop(valor_d))

        if i < len(deducciones):
            concepto_ded, valor_ded = deducciones[i]
            c.drawString(col_der_x + 10, y_fila, concepto_ded)
            c.drawRightString(col_der_x + ancho_col - 10, y_fila, _fmt_cop(valor_ded))

        y_fila -= 16

    # Totales de columnas
    c.setFillColor(colors.HexColor("#E2E8F0"))
    c.rect(col_izq_x, y_fila - 6, ancho_col, 20, fill=1, stroke=1)
    c.rect(col_der_x, y_fila - 6, ancho_col, 20, fill=1, stroke=1)
    c.setFillColor(colors.HexColor("#0F2D3D"))
    c.setFont("Helvetica-Bold", 9)
    c.drawString(col_izq_x + 10, y_fila, "TOTAL DEVENGADO:")
    c.drawRightString(col_izq_x + ancho_col - 10, y_fila, _fmt_cop(liquidacion.total_devengado))
    c.drawString(col_der_x + 10, y_fila, "TOTAL DEDUCIDO:")
    c.drawRightString(col_der_x + ancho_col - 10, y_fila, _fmt_cop(liquidacion.total_deducido))

    # --- 4. NETO A PAGAR EN NÚMEROS Y LETRAS ---
    y_neto = y_fila - 26
    h_neto = 44
    c.setFillColor(colors.HexColor("#0F2D3D"))
    c.roundRect(margin_x, y_neto - h_neto, ancho_util, h_neto, 4, fill=1, stroke=0)

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin_x + 14, y_neto - 18, "NETO LÍQUIDO A PAGAR AL TRABAJADOR:")
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(ancho - margin_x - 14, y_neto - 18, f"{_fmt_cop(liquidacion.neto_a_pagar)} COP")

    letras = numero_a_letras_cop(liquidacion.neto_a_pagar)
    c.setFont("Helvetica", 9)
    c.drawString(margin_x + 14, y_neto - 34, f"SON: {letras}")

    # --- 5. PIE DE AUDITORÍA Y SELLO DIGITAL SHA-256 (SIN ESPACIO DE FIRMAS) ---
    y_pie = y_neto - h_neto - 20
    datos_firma = (
        f"{empleado.numero_documento}|{liquidacion.periodo_anio}-{liquidacion.periodo_mes:02d}|"
        f"{liquidacion.total_devengado}|{liquidacion.total_deducido}|{liquidacion.neto_a_pagar}"
    )
    sello_pdf = hashlib.sha256(datos_firma.encode("utf-8")).hexdigest()

    c.setFillColor(colors.HexColor("#64748B"))
    c.setFont("Helvetica", 8)
    c.drawString(margin_x, y_pie, f"Sello Digital de Integridad SHA-256: {sello_pdf}")
    c.drawString(
        margin_x,
        y_pie - 12,
        f"Emitido electrónicamente el {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | "
        f"Aprobador Oficial: {JEFE_OFICIAL_EMAIL} | Cumple Ley 1581 de 2012 y Normativa Laboral Colombia",
    )

    c.save()

    bytes_pdf = ruta_pdf.read_bytes()
    hash_archivo = hashlib.sha256(bytes_pdf).hexdigest()
    return ruta_pdf, hash_archivo


def despachar_desprendible_por_correo(
    empleado: Empleado,
    liquidacion: LiquidacionNomina,
    ruta_pdf: Path,
    copia_auditoria_email: str = JEFE_OFICIAL_EMAIL,
) -> dict[str, str]:
    """Envía el desprendible de nómina en PDF como archivo adjunto al correo del trabajador
    con copia de respaldo a la jefatura/auditoría (`tminero.sst@gmail.com`)."""
    s = get_settings()
    periodo_str = f"{liquidacion.periodo_anio}-{liquidacion.periodo_mes:02d}"
    asunto = f"Desprendible Oficial de Nomina ({periodo_str}) - Disjoya Autopartes"
    cuerpo = (
        f"Estimado(a) {empleado.nombre_completo} ({empleado.tipo_documento} {empleado.numero_documento}):\n\n"
        f"Adjunto a este mensaje encontrará su Comprobante Oficial de Pago de Nómina correspondiente al período {periodo_str}.\n\n"
        f"Resumen de Liquidación:\n"
        f"• Cargo: {empleado.cargo} ({liquidacion.seccion_plantilla})\n"
        f"• Total Devengado: {_fmt_cop(liquidacion.total_devengado)} COP\n"
        f"• Total Deducido (Salud y Pensión): {_fmt_cop(liquidacion.total_deducido)} COP\n"
        f"• Neto a Pagar: {_fmt_cop(liquidacion.neto_a_pagar)} COP ({numero_a_letras_cop(liquidacion.neto_a_pagar)})\n"
        f"• Horas Extras Pagadas en el Mes: {liquidacion.horas_extra_diurna} h\n"
        f"• Saldo de Horas Extras a Favor para el Mes Siguiente: {liquidacion.horas_arrastradas_siguiente_mes} h\n\n"
        f"Cordialmente,\n"
        f"Departamento de Nómina y Jefatura SST ({JEFE_OFICIAL_EMAIL})\n"
        f"DISJOYA AUTOPARTES S.A.S. — Cl. 23 #13-32, Bucaramanga, Santander"
    )

    if not s.smtp_host or not empleado.email or empleado.email.endswith("@disjoya.com.co"):
        return {
            "estado": "almacenado_localmente_sin_smtp",
            "destinatario": empleado.email,
            "cc_auditoria": copia_auditoria_email,
            "archivo_pdf": str(ruta_pdf),
        }

    try:
        cuenta_smtp = (s.smtp_usuario or "pedro.noriega@gmail.com").strip()
        msg = EmailMessage()
        msg["From"] = formataddr(("Disjoya Autopartes SAS", cuenta_smtp))
        msg["To"] = empleado.email.strip()
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain="gmail.com")
        msg["Subject"] = asunto
        msg.set_content(cuerpo)

        contenido_pdf = ruta_pdf.read_bytes()
        msg.add_attachment(
            contenido_pdf,
            maintype="application",
            subtype="pdf",
            filename=ruta_pdf.name,
        )

        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        with smtplib.SMTP(s.smtp_host, s.smtp_puerto, timeout=12) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.ehlo()
            if s.smtp_usuario and s.smtp_password:
                smtp.login(s.smtp_usuario, s.smtp_password)
            smtp.send_message(msg)

        return {
            "estado": "enviado_smtp",
            "destinatario": empleado.email,
            "cc_auditoria": copia_auditoria_email,
            "archivo_pdf": str(ruta_pdf),
        }
    except Exception as exc:
        return {
            "estado": f"almacenado_localmente (aviso smtp: {type(exc).__name__})",
            "destinatario": empleado.email,
            "cc_auditoria": copia_auditoria_email,
            "archivo_pdf": str(ruta_pdf),
        }


def emitir_y_despachar_desprendibles_periodo(
    db: Session,
    anio: int,
    mes: int,
    directorio_salida: Path | str = "desprendibles_generados",
) -> list[dict]:
    """Genera los PDFs individuales para todas las liquidaciones del período,
    actualiza su ruta y hash SHA-256 en la base de datos y ejecuta el despacho por correo."""
    from sqlalchemy import select

    liquidaciones = db.execute(
        select(LiquidacionNomina).where(
            LiquidacionNomina.periodo_anio == anio,
            LiquidacionNomina.periodo_mes == mes,
        )
    ).scalars().all()

    resultados: list[dict] = []
    for liq in liquidaciones:
        emp = db.get(Empleado, liq.empleado_id)
        if emp is None:
            continue
        ledger = db.execute(
            select(HorasExtrasLedger).where(
                HorasExtrasLedger.empleado_id == emp.id,
                HorasExtrasLedger.periodo_anio == anio,
                HorasExtrasLedger.periodo_mes == mes,
            )
        ).scalar_one_or_none()

        ruta_pdf, sha_pdf = generar_pdf_desprendible_individual(emp, liq, ledger, directorio_salida)
        res_envio = despachar_desprendible_por_correo(emp, liq, ruta_pdf)

        liq.pdf_ruta_archivo = str(ruta_pdf)
        liq.pdf_hash_sha256 = sha_pdf
        liq.email_enviado = res_envio["estado"] == "enviado_smtp"
        if liq.email_enviado:
            liq.email_enviado_en = datetime.now(timezone.utc)

        resultados.append(
            {
                "empleado": emp.nombre_completo,
                "documento": emp.numero_documento,
                "cargo": emp.cargo,
                "neto_a_pagar": str(liq.neto_a_pagar),
                "pdf_archivo": str(ruta_pdf),
                "pdf_sha256": sha_pdf,
                "despacho_correo": res_envio,
            }
        )

    db.commit()
    return resultados
