"""Pruebas del historial de aciertos: cálculo del acierto de cada mes y la pantalla que compara lo esperado con lo vendido."""

import uuid
from datetime import date, datetime

import pytest

from app.aciertos import evaluar, mes_cerrado, resumir_periodo, texto_acierto
from app.extensions import db
from app.models import Cliente, DetalleVenta, PrediccionDemanda, Sede, Usuario, Venta
from conftest import crear_producto


def texto(respuesta):
    return respuesta.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Cálculos
# ---------------------------------------------------------------------------
def test_un_mes_esta_cerrado_solo_cuando_ya_empezo_el_siguiente():
    assert mes_cerrado(date(2026, 9, 1), date(2026, 10, 1))
    assert not mes_cerrado(date(2026, 10, 1), date(2026, 10, 31))
    assert mes_cerrado(date(2025, 12, 1), date(2026, 1, 2))


def test_evaluar_marca_si_la_venta_cayo_dentro_del_rango_probable():
    dentro = evaluar(40, 50, error_validacion=30)   # rango 28 a 52
    fuera = evaluar(40, 60, error_validacion=30)
    assert dentro["dentro_rango"] and dentro["diferencia"] == 10
    assert not fuera["dentro_rango"]


def test_el_acierto_del_mes_es_uno_menos_el_error_total():
    filas = [evaluar(10, 8, 30), evaluar(20, 30, 30)]   # errores 2 y 10 sobre 38 vendidos
    r = resumir_periodo(filas)
    assert r["esperado"] == 30 and r["vendido"] == 38 and r["acierto_pct"] == round((1 - 12 / 38) * 100)
    assert r["productos"] == 2 and r["sesgo"] == "subestima"


def test_el_acierto_no_baja_de_cero_y_sin_ventas_no_se_calcula():
    assert resumir_periodo([evaluar(100, 5, 30)])["acierto_pct"] == 0
    sin = resumir_periodo([evaluar(10, 0, 30)])
    assert sin["acierto_pct"] is None and "no hay contra qué comparar" in texto_acierto(sin)


def test_el_texto_dice_si_se_esperaba_mas_o_menos():
    assert "más de lo que se vendió" in texto_acierto(resumir_periodo([evaluar(50, 10, 30)]))
    assert "parecidos" in texto_acierto(resumir_periodo([evaluar(50, 50, 30)]))


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------
def _vender(app, id_producto, fecha, cantidad, anulada=False):
    with app.app_context():
        v = Venta(numero_operacion=uuid.uuid4().hex[:12], fecha_hora=fecha, id_cliente=Cliente.query.first().id_cliente,
                  id_usuario=Usuario.query.first().id_usuario, id_sede=Sede.query.first().id_sede, total=cantidad * 10, anulada=anulada)
        db.session.add(v)
        db.session.flush()
        db.session.add(DetalleVenta(id_venta=v.id_venta, id_producto=id_producto, cantidad=cantidad, precio_unitario=10,
                                    subtotal=cantidad * 10))
        db.session.commit()


@pytest.fixture()
def producto_con_pronostico_pasado(app):
    id_producto = crear_producto(app, "ACI-" + uuid.uuid4().hex[:6], precio=10, stock=50)
    with app.app_context():
        # mes pasado: primero un pronóstico viejo y luego uno recalculado (vale el último); y uno del mes en curso, que no cuenta
        for fecha, demanda in ((datetime(2026, 1, 5), 99), (datetime(2026, 1, 20), 40)):
            db.session.add(PrediccionDemanda(id_producto=id_producto, fecha_generacion=fecha, horizonte_semanas=4,
                                             demanda_estimada=demanda, periodo_objetivo=date(2026, 2, 1), confiabilidad="Media",
                                             error_validacion=30))
        db.session.add(PrediccionDemanda(id_producto=id_producto, horizonte_semanas=4, demanda_estimada=77,
                                         periodo_objetivo=date.today().replace(day=1), confiabilidad="Media"))
        db.session.commit()
    yield id_producto
    with app.app_context():
        PrediccionDemanda.query.filter_by(id_producto=id_producto).delete()
        db.session.commit()


def test_la_pantalla_compara_el_ultimo_pronostico_con_lo_vendido_sin_contar_anuladas(app, admin, producto_con_pronostico_pasado):
    id_producto = producto_con_pronostico_pasado
    _vender(app, id_producto, datetime(2026, 2, 10), 30)
    _vender(app, id_producto, datetime(2026, 2, 12), 5)
    _vender(app, id_producto, datetime(2026, 2, 14), 500, anulada=True)
    r = admin.get("/analitica/aciertos")
    html = texto(r)
    assert r.status_code == 200 and "02/2026" in html
    assert "<td>40</td>" in html and "<td>35</td>" in html          # esperado 40 (no 99) y vendido 35 (sin la anulada)
    assert "77" not in html                                          # el mes en curso todavía no se compara
    ficha = texto(admin.get(f"/analitica/producto/{id_producto}"))
    assert "Cuánto acertó el pronóstico en este producto" in ficha and "Dentro del rango probable" in ficha


def test_sin_meses_terminados_la_pantalla_lo_explica(app, admin):
    with app.app_context():
        pendientes = PrediccionDemanda.query.filter(PrediccionDemanda.periodo_objetivo < date.today().replace(day=1)).count()
    html = texto(admin.get("/analitica/aciertos"))
    if pendientes == 0:
        assert "Todavía no hay meses terminados" in html


def test_solo_el_administrador_ve_los_aciertos(trabajador_a):
    assert trabajador_a.get("/analitica/aciertos").status_code == 403
