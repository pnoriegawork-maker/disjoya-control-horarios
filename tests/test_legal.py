from datetime import date, datetime, time
from decimal import Decimal

from app import legal
from app.geo import haversine_m, punto_en_poligono
from app.legal import TZ_BOGOTA, Intervalo


def bog(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=TZ_BOGOTA)


def test_festivos_colombia_2026():
    esperados = {
        date(2026, 1, 1), date(2026, 1, 12), date(2026, 3, 23), date(2026, 4, 2), date(2026, 4, 3), date(2026, 5, 1),
        date(2026, 5, 18), date(2026, 6, 8), date(2026, 6, 15), date(2026, 6, 29), date(2026, 7, 20), date(2026, 8, 7),
        date(2026, 8, 17), date(2026, 10, 12), date(2026, 11, 2), date(2026, 11, 16), date(2026, 12, 8), date(2026, 12, 25),
    }
    assert set(legal.festivos_colombia(2026)) == esperados


def test_parametros_versionados():
    p = legal.parametros_vigentes(date(2026, 11, 13))
    assert (p.jornada_semanal, p.horas_mes, p.inicio_nocturno, p.recargo_dominical) == (42, 210, time(19), Decimal("0.90"))
    f = p.factores()
    assert f["HED"] == Decimal("1.25") and f["HEN"] == Decimal("1.75")
    assert f["HEDDF"] == Decimal("2.15") and f["HENDF"] == Decimal("2.65") and f["RNDF"] == Decimal("1.25")
    antes = legal.parametros_vigentes(date(2025, 6, 30))
    assert (antes.jornada_semanal, antes.inicio_nocturno, antes.recargo_dominical) == (46, time(21), Decimal("0.75"))
    assert antes.factores()["HEDDF"] == Decimal("2.00") and antes.factores()["HENDF"] == Decimal("2.50")
    assert legal.parametros_vigentes(date(2026, 7, 14)).jornada_semanal == 44
    assert legal.parametros_vigentes(date(2027, 7, 1)).recargo_dominical == Decimal("1.00")


def test_liquidacion_domingo_con_extras_nocturnas():
    # Domingo 15-nov-2026: ordinario 10:00-18:00, extra 18:00-20:00 (19:00 = inicio nocturno)
    r = legal.liquidar(Decimal("2100000"), [Intervalo(bog(2026, 11, 15, 10), bog(2026, 11, 15, 18))],
                       [Intervalo(bog(2026, 11, 15, 18), bog(2026, 11, 15, 20))])
    por = {ln.concepto: ln for ln in r.lineas}
    assert por["RDD"].horas == Decimal("8.0000") and por["RDD"].valor == Decimal("72000.00")  # 8 x 10.000 x 0.90
    assert por["HEDDF"].valor == Decimal("21500.00")  # 1 x 10.000 x 2.15
    assert por["HENDF"].valor == Decimal("26500.00")  # 1 x 10.000 x 2.65
    assert r.total_recargos_y_extras == Decimal("120000.00")


def test_turno_nocturno_cruza_medianoche_hacia_festivo():
    # Domingo 15-nov 22:00 -> lunes festivo 16-nov 06:00 (Independencia de Cartagena trasladado)
    r = legal.liquidar(Decimal("2100000"), [Intervalo(bog(2026, 11, 15, 22), bog(2026, 11, 16, 6))], [])
    assert [(ln.concepto, ln.horas) for ln in r.lineas] == [("RNDF", Decimal("8.0000"))]
    # Sábado 14-nov 22:00 -> domingo 15-nov 02:00: 2h RN (sábado) + 2h RNDF (domingo)
    r2 = legal.liquidar(Decimal("2100000"), [Intervalo(bog(2026, 11, 14, 22), bog(2026, 11, 15, 2))], [])
    assert {ln.concepto: ln.horas for ln in r2.lineas} == {"RN": Decimal("2.0000"), "RNDF": Decimal("2.0000")}


def test_cambio_normativo_dentro_del_periodo():
    r = legal.liquidar(Decimal("2200000"), [], [Intervalo(bog(2026, 7, 14, 17), bog(2026, 7, 14, 18)),
                                                Intervalo(bog(2026, 7, 16, 17), bog(2026, 7, 16, 18))])
    valores = sorted(ln.valor_hora for ln in r.lineas)
    assert valores == [Decimal("2200000") / 220, Decimal("2200000") / 210]


def test_topes_art_22_ley_50():
    ok = legal.validar_topes(Decimal("2"), Decimal("0"), Decimal("10"), Decimal("8"), False, 30)
    assert ok.permitido
    dia = legal.validar_topes(Decimal("0.5"), Decimal("2"), Decimal("2"), Decimal("8"), False, 30)
    assert not dia.permitido and "diario" in dia.motivos[0]
    sem = legal.validar_topes(Decimal("2"), Decimal("0"), Decimal("11"), Decimal("8"), False, 30)
    assert not sem.permitido and "semanal" in sem.motivos[0]
    assert not legal.validar_topes(Decimal("1"), Decimal("0"), Decimal("0"), Decimal("10"), False, 30).permitido
    assert not legal.validar_topes(Decimal("1"), Decimal("0"), Decimal("0"), Decimal("8"), True, 30).permitido
    assert not legal.validar_topes(Decimal("1"), Decimal("0"), Decimal("0"), Decimal("6"), False, 17).permitido


def test_geocerca():
    d = haversine_m(4.710989, -74.072090, 4.711079, -74.072090)
    assert 9.9 < d < 10.1
    poligono = [[4.7105, -74.0725], [4.7105, -74.0715], [4.7115, -74.0715], [4.7115, -74.0725]]
    assert punto_en_poligono(4.7110, -74.0720, poligono)
    assert not punto_en_poligono(4.7130, -74.0720, poligono)

