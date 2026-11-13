"""Motor jurídico-laboral determinístico (Colombia).

Módulo puro (sin E/S): parámetros legales versionados por fecha de causación, calendario de
festivos (Ley 51 de 1983 - Ley Emiliani), álgebra de intervalos, clasificación de horas en
diurnas/nocturnas/dominicales-festivas y validación de topes de trabajo suplementario.

Fuentes normativas parametrizadas:
* Jornada máxima semanal: Art. 161 CST mod. Ley 2101 de 2021 (47h 15-jul-2023, 46h 15-jul-2024,
  44h 15-jul-2025, 42h 15-jul-2026).
* Trabajo nocturno: Art. 160 CST. 22:00 (Ley 789/2002) -> 21:00 (Ley 1846/2017) ->
  19:00 desde el 25-dic-2025 (Art. 10 Ley 2466 de 2025).
* Recargo dominical/festivo: Art. 179 CST mod. Ley 2466 de 2025: 75% -> 80% (1-jul-2025)
  -> 90% (1-jul-2026) -> 100% (1-jul-2027).
* Recargo nocturno 35% (Art. 168 CST); HED 25% y HEN 75% (Art. 168 CST).
* Topes de trabajo suplementario: 2h diarias / 12h semanales (Art. 22 Ley 50 de 1990);
  si la jornada se amplía por acuerdo a 10h diarias no puede haber horas extras ese día.
* Salario integral (Art. 132 CST): compensa de antemano recargos y trabajo extraordinario.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

TZ_BOGOTA = ZoneInfo("America/Bogota")
UTC = timezone.utc

CERO = Decimal("0")
MAX_HE_DIARIAS = Decimal("2")
MAX_HE_SEMANALES = Decimal("12")
JORNADA_AMPLIADA_SIN_HE = Decimal("10")
MAX_ORDINARIA_DIARIA_FLEXIBLE = Decimal("9")
FIN_NOCTURNO = time(6, 0)

RECARGO_NOCTURNO = Decimal("0.35")
RECARGO_HED = Decimal("0.25")
RECARGO_HEN = Decimal("0.75")

_JORNADA_SEMANAL: list[tuple[date, int]] = [
    (date(1950, 1, 1), 48),
    (date(2023, 7, 15), 47),
    (date(2024, 7, 15), 46),
    (date(2025, 7, 15), 44),
    (date(2026, 7, 15), 42),
]
_INICIO_NOCTURNO: list[tuple[date, time]] = [
    (date(1950, 1, 1), time(22, 0)),
    (date(2017, 7, 18), time(21, 0)),
    (date(2025, 12, 25), time(19, 0)),
]
_RECARGO_DOMINICAL: list[tuple[date, Decimal]] = [
    (date(1950, 1, 1), Decimal("0.75")),
    (date(2025, 7, 1), Decimal("0.80")),
    (date(2026, 7, 1), Decimal("0.90")),
    (date(2027, 7, 1), Decimal("1.00")),
]


def _vigente(tabla: list[tuple[date, object]], fecha: date):
    idx = bisect_right([d for d, _ in tabla], fecha) - 1
    if idx < 0:
        raise ValueError(f"Sin parámetro legal para {fecha}")
    return tabla[idx][1]


@dataclass(frozen=True)
class ParametrosLegales:
    fecha: date
    jornada_semanal: int
    inicio_nocturno: time
    recargo_dominical: Decimal

    @property
    def horas_mes(self) -> int:
        """Divisor mensual (jornada semanal / 6 días x 30 días): 44h->220, 42h->210."""
        return self.jornada_semanal * 5

    @property
    def fin_nocturno(self) -> time:
        return FIN_NOCTURNO

    def factores(self) -> dict[str, Decimal]:
        """Factor de pago sobre el valor de la hora ordinaria (para salario mensual fijo:
        HOD ya está incluida en el básico; los recargos ordinarios pagan solo el adicional)."""
        rd = self.recargo_dominical
        return {
            "HOD": CERO,
            "RN": RECARGO_NOCTURNO,
            "RDD": rd,
            "RNDF": rd + RECARGO_NOCTURNO,
            "HED": Decimal("1") + RECARGO_HED,
            "HEN": Decimal("1") + RECARGO_HEN,
            "HEDDF": Decimal("1") + RECARGO_HED + rd,
            "HENDF": Decimal("1") + RECARGO_HEN + rd,
        }

    def porcentajes(self) -> dict[str, Decimal]:
        """Porcentaje de recargo reportado en Nómina Electrónica DIAN (Res. 000013 de 2021)."""
        rd = self.recargo_dominical
        return {
            "HOD": CERO,
            "RN": RECARGO_NOCTURNO * 100,
            "RDD": rd * 100,
            "RNDF": (rd + RECARGO_NOCTURNO) * 100,
            "HED": RECARGO_HED * 100,
            "HEN": RECARGO_HEN * 100,
            "HEDDF": (RECARGO_HED + rd) * 100,
            "HENDF": (RECARGO_HEN + rd) * 100,
        }


@lru_cache(maxsize=4096)
def parametros_vigentes(fecha: date) -> ParametrosLegales:
    return ParametrosLegales(
        fecha=fecha,
        jornada_semanal=_vigente(_JORNADA_SEMANAL, fecha),
        inicio_nocturno=_vigente(_INICIO_NOCTURNO, fecha),
        recargo_dominical=_vigente(_RECARGO_DOMINICAL, fecha),
    )


CONCEPTOS: dict[str, dict[str, str]] = {
    "HOD": {"nombre": "Hora ordinaria diurna", "dian": "Basico", "base": "Art. 158-161 CST; Ley 2101/2021"},
    "RN": {"nombre": "Recargo nocturno ordinario", "dian": "HRN", "base": "Arts. 160 y 168 CST; Ley 2466/2025"},
    "RDD": {"nombre": "Recargo diurno dominical/festivo", "dian": "HRDDF", "base": "Art. 179 CST mod. Ley 2466/2025"},
    "RNDF": {"nombre": "Recargo nocturno dominical/festivo", "dian": "HRNDF", "base": "Arts. 168 y 179 CST"},
    "HED": {"nombre": "Hora extra diurna", "dian": "HED", "base": "Art. 168 CST; Art. 22 Ley 50/1990"},
    "HEN": {"nombre": "Hora extra nocturna", "dian": "HEN", "base": "Art. 168 CST; Art. 22 Ley 50/1990"},
    "HEDDF": {"nombre": "Hora extra diurna dominical/festiva", "dian": "HEDDF", "base": "Arts. 168 y 179 CST"},
    "HENDF": {"nombre": "Hora extra nocturna dominical/festiva", "dian": "HENDF", "base": "Arts. 168 y 179 CST"},
}


# ------------------------------------------------------------------------------------
# Festivos nacionales de Colombia (Ley 51 de 1983, Ley 37 de 1905, Concordato)
# ------------------------------------------------------------------------------------
def domingo_de_pascua(anio: int) -> date:
    """Algoritmo gregoriano anónimo (Meeus/Jones/Butcher)."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mes = (h + l_ - 7 * m + 114) // 31
    dia = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(anio, mes, dia)


def _al_lunes(d: date) -> date:
    return d + timedelta(days=(7 - d.weekday()) % 7)


@lru_cache(maxsize=256)
def festivos_colombia(anio: int) -> dict[date, str]:
    p = domingo_de_pascua(anio)
    fest = {
        date(anio, 1, 1): "Año Nuevo",
        date(anio, 5, 1): "Día del Trabajo",
        date(anio, 7, 20): "Independencia de Colombia",
        date(anio, 8, 7): "Batalla de Boyacá",
        date(anio, 12, 8): "Inmaculada Concepción",
        date(anio, 12, 25): "Navidad",
        p - timedelta(days=3): "Jueves Santo",
        p - timedelta(days=2): "Viernes Santo",
        _al_lunes(date(anio, 1, 6)): "Reyes Magos",
        _al_lunes(date(anio, 3, 19)): "San José",
        _al_lunes(date(anio, 6, 29)): "San Pedro y San Pablo",
        _al_lunes(date(anio, 8, 15)): "Asunción de la Virgen",
        _al_lunes(date(anio, 10, 12)): "Día de la Diversidad Étnica y Cultural",
        _al_lunes(date(anio, 11, 1)): "Todos los Santos",
        _al_lunes(date(anio, 11, 11)): "Independencia de Cartagena",
        p + timedelta(days=43): "Ascensión del Señor",
        p + timedelta(days=64): "Corpus Christi",
        p + timedelta(days=71): "Sagrado Corazón de Jesús",
    }
    return dict(sorted(fest.items()))


def es_dominical_o_festivo(d: date) -> bool:
    return d.weekday() == 6 or d in festivos_colombia(d.year)


# ------------------------------------------------------------------------------------
# Tiempo e intervalos
# ------------------------------------------------------------------------------------
def a_utc(dt: datetime) -> datetime:
    """Normaliza a UTC; las fechas ingenuas se interpretan como hora legal de Colombia."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_BOGOTA)
    return dt.astimezone(UTC)


def a_bogota(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TZ_BOGOTA)


def inicio_dia_local(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=TZ_BOGOTA).astimezone(UTC)


def inicio_semana(d: date) -> date:
    """Semana laboral lunes-domingo."""
    return d - timedelta(days=d.weekday())


@dataclass(frozen=True, order=True)
class Intervalo:
    inicio: datetime
    fin: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "inicio", a_utc(self.inicio))
        object.__setattr__(self, "fin", a_utc(self.fin))
        if self.fin < self.inicio:
            raise ValueError("Intervalo inválido: fin anterior al inicio")

    @property
    def segundos(self) -> int:
        return int((self.fin - self.inicio).total_seconds())


def horas(segundos: int | float) -> Decimal:
    return (Decimal(int(segundos)) / Decimal(3600)).quantize(Decimal("0.0001"), ROUND_HALF_UP)


def normalizar(intervalos: Iterable[Intervalo]) -> list[Intervalo]:
    orden = sorted(i for i in intervalos if i.fin > i.inicio)
    res: list[Intervalo] = []
    for it in orden:
        if res and it.inicio <= res[-1].fin:
            res[-1] = Intervalo(res[-1].inicio, max(res[-1].fin, it.fin))
        else:
            res.append(it)
    return res


def intersectar(a: Iterable[Intervalo], b: Iterable[Intervalo]) -> list[Intervalo]:
    res: list[Intervalo] = []
    for x in normalizar(a):
        for y in normalizar(b):
            ini, fin = max(x.inicio, y.inicio), min(x.fin, y.fin)
            if fin > ini:
                res.append(Intervalo(ini, fin))
    return normalizar(res)


def restar(a: Iterable[Intervalo], b: Iterable[Intervalo]) -> list[Intervalo]:
    res = normalizar(a)
    for y in normalizar(b):
        nuevo: list[Intervalo] = []
        for x in res:
            if y.fin <= x.inicio or y.inicio >= x.fin:
                nuevo.append(x)
                continue
            if y.inicio > x.inicio:
                nuevo.append(Intervalo(x.inicio, y.inicio))
            if y.fin < x.fin:
                nuevo.append(Intervalo(y.fin, x.fin))
        res = nuevo
    return normalizar(res)


def total_segundos(intervalos: Iterable[Intervalo]) -> int:
    return sum(i.segundos for i in normalizar(intervalos))


@dataclass(frozen=True)
class Segmento:
    inicio: datetime
    fin: datetime
    fecha_local: date
    nocturno: bool
    dominical_festivo: bool
    extra: bool

    @property
    def segundos(self) -> int:
        return int((self.fin - self.inicio).total_seconds())

    @property
    def concepto(self) -> str:
        if self.extra:
            if self.dominical_festivo:
                return "HENDF" if self.nocturno else "HEDDF"
            return "HEN" if self.nocturno else "HED"
        if self.dominical_festivo:
            return "RNDF" if self.nocturno else "RDD"
        return "RN" if self.nocturno else "HOD"


def segmentar(intervalo: Intervalo, extra: bool) -> Iterator[Segmento]:
    """Parte un intervalo en las fronteras legales: medianoche local (cambio de día calendario
    dominical/festivo), 06:00 (fin nocturno) e inicio nocturno vigente en esa fecha."""
    cur = a_bogota(intervalo.inicio)
    fin = a_bogota(intervalo.fin)
    while cur < fin:
        d = cur.date()
        p = parametros_vigentes(d)
        fronteras = [
            datetime.combine(d, FIN_NOCTURNO, tzinfo=TZ_BOGOTA),
            datetime.combine(d, p.inicio_nocturno, tzinfo=TZ_BOGOTA),
            datetime.combine(d + timedelta(days=1), time(0, 0), tzinfo=TZ_BOGOTA),
        ]
        siguiente = min(f for f in fronteras if f > cur)
        tramo_fin = min(siguiente, fin)
        t = cur.timetz().replace(tzinfo=None)
        nocturno = t >= p.inicio_nocturno or t < FIN_NOCTURNO
        yield Segmento(
            inicio=cur.astimezone(UTC),
            fin=tramo_fin.astimezone(UTC),
            fecha_local=d,
            nocturno=nocturno,
            dominical_festivo=es_dominical_o_festivo(d),
            extra=extra,
        )
        cur = tramo_fin


# ------------------------------------------------------------------------------------
# Liquidación
# ------------------------------------------------------------------------------------
@dataclass
class LineaLiquidacion:
    concepto: str
    codigo_dian: str
    nombre: str
    base_legal: str
    porcentaje_recargo: Decimal
    factor_pago: Decimal
    valor_hora: Decimal
    segundos: int = 0

    @property
    def horas(self) -> Decimal:
        return horas(self.segundos)

    @property
    def valor(self) -> Decimal:
        bruto = Decimal(self.segundos) / Decimal(3600) * self.valor_hora * self.factor_pago
        return bruto.quantize(Decimal("0.01"), ROUND_HALF_UP)


@dataclass
class ResultadoLiquidacion:
    lineas: list[LineaLiquidacion] = field(default_factory=list)
    salario_integral: bool = False

    @property
    def total_recargos_y_extras(self) -> Decimal:
        if self.salario_integral:
            return CERO.quantize(Decimal("0.01"))
        return sum((ln.valor for ln in self.lineas), CERO).quantize(Decimal("0.01"))


def liquidar(
    salario_mensual: Decimal,
    ordinarios: Iterable[Intervalo],
    extras: Iterable[Intervalo],
    salario_integral: bool = False,
) -> ResultadoLiquidacion:
    """Clasifica cada segundo laborado y lo valoriza con los parámetros vigentes en la fecha
    de causación (soporta períodos que atraviesan cambios normativos, p.ej. 15-jul-2026)."""
    ord_norm = normalizar(ordinarios)
    ext_norm = normalizar(extras)
    if intersectar(ord_norm, ext_norm):
        raise ValueError("Un mismo tramo no puede ser ordinario y suplementario a la vez")
    acumulado: dict[tuple, LineaLiquidacion] = {}
    for lista, es_extra in ((ord_norm, False), (ext_norm, True)):
        for it in lista:
            for seg in segmentar(it, es_extra):
                p = parametros_vigentes(seg.fecha_local)
                c = seg.concepto
                valor_hora = Decimal(salario_mensual) / Decimal(p.horas_mes)
                factor = CERO if salario_integral else p.factores()[c]
                clave = (c, p.porcentajes()[c], factor, valor_hora)
                if clave not in acumulado:
                    acumulado[clave] = LineaLiquidacion(
                        concepto=c,
                        codigo_dian=CONCEPTOS[c]["dian"],
                        nombre=CONCEPTOS[c]["nombre"],
                        base_legal=CONCEPTOS[c]["base"] + ("; Art. 132 CST (salario integral)" if salario_integral else ""),
                        porcentaje_recargo=p.porcentajes()[c],
                        factor_pago=factor,
                        valor_hora=valor_hora,
                    )
                acumulado[clave].segundos += seg.segundos
    orden = list(CONCEPTOS)
    lineas = sorted(acumulado.values(), key=lambda x: (orden.index(x.concepto), x.porcentaje_recargo))
    return ResultadoLiquidacion(lineas=lineas, salario_integral=salario_integral)


# ------------------------------------------------------------------------------------
# Topes de trabajo suplementario (Art. 22 Ley 50 de 1990)
# ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ResultadoTopes:
    permitido: bool
    motivos: tuple[str, ...]
    disponible_dia: Decimal
    disponible_semana: Decimal


def edad_en(fecha_nacimiento: date | None, fecha: date) -> int | None:
    if fecha_nacimiento is None:
        return None
    return fecha.year - fecha_nacimiento.year - ((fecha.month, fecha.day) < (fecha_nacimiento.month, fecha_nacimiento.day))


def validar_topes(
    horas_solicitadas: Decimal,
    horas_he_dia: Decimal,
    horas_he_semana: Decimal,
    horas_ordinarias_programadas_dia: Decimal,
    es_direccion_confianza: bool,
    edad: int | None,
) -> ResultadoTopes:
    motivos: list[str] = []
    disp_dia = max(CERO, MAX_HE_DIARIAS - horas_he_dia)
    disp_sem = max(CERO, MAX_HE_SEMANALES - horas_he_semana)
    if horas_solicitadas <= CERO:
        motivos.append("La cantidad de horas solicitadas debe ser positiva.")
    if es_direccion_confianza:
        motivos.append("Trabajador de dirección, confianza y manejo: excluido de jornada máxima y de horas extras (Art. 162 lit. a CST).")
    if edad is not None and edad < 18:
        motivos.append("Trabajador menor de edad: prohibido el trabajo suplementario (Art. 114 Ley 1098 de 2006).")
    if horas_ordinarias_programadas_dia >= JORNADA_AMPLIADA_SIN_HE:
        motivos.append("Jornada ampliada a 10 horas o más en el día: no se pueden laborar horas extras (Art. 22 Ley 50 de 1990).")
    if horas_he_dia + horas_solicitadas > MAX_HE_DIARIAS:
        motivos.append(
            f"Excede el tope diario de 2 horas extras (Art. 22 Ley 50 de 1990): acumuladas {horas_he_dia} h + solicitadas {horas_solicitadas} h."
        )
    if horas_he_semana + horas_solicitadas > MAX_HE_SEMANALES:
        motivos.append(
            f"Excede el tope semanal de 12 horas extras (Art. 22 Ley 50 de 1990): acumuladas {horas_he_semana} h + solicitadas {horas_solicitadas} h."
        )
    return ResultadoTopes(not motivos, tuple(motivos), disp_dia, disp_sem)

