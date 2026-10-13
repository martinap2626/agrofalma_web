"""Pruebas de las alertas de ventas inusuales del mes en curso."""

import calendar
import uuid
from datetime import date, datetime

import pytest

from app.alertas_ventas import detectar_ventas_inusuales
from app.extensions import db
from app.models import Cliente, DetalleVenta, PrediccionDemanda, Sede, Usuario, Venta
from conftest import crear_producto


def p(vendido, esperado, nombre="X", i=1):
    return {"id_producto": i, "nombre": nombre, "vendido": vendido, "esperado": esperado}


def test_vende_el_doble_de_lo_que_tocaba_es_una_alerta_de_mas():
    # a mitad de mes (15 de 30) de 40 esperados tocaban 20; lleva 45
    alertas = detectar_ventas_inusuales([p(45, 40)], 15, 30)
    assert len(alertas) == 1 and alertas[0]["tipo"] == "mas" and alertas[0]["tocaba"] == 20 and alertas[0]["vendido"] == 45


def test_una_diferencia_pequena_no_alerta_aunque_sea_el_doble():
    assert detectar_ventas_inusuales([p(6, 4)], 30, 30) == []   # pasó de 4 a 6: solo 2 unidades de diferencia


def test_vender_poco_pasada_la_mitad_del_mes_es_una_alerta_de_menos():
    alertas = detectar_ventas_inusuales([p(3, 40)], 24, 30)
    assert alertas[0]["tipo"] == "menos" and alertas[0]["tocaba"] == 32


def test_vender_poco_al_inicio_del_mes_no_alerta():
    assert detectar_ventas_inusuales([p(0, 40)], 5, 30) == []


def test_lo_normal_no_alerta_y_sin_venta_esperada_tampoco():
    assert detectar_ventas_inusuales([p(20, 40), p(0, 0, i=2), p(0, 3, i=3)], 15, 30) == []


def test_las_alertas_salen_ordenadas_por_la_diferencia_mas_grande():
    alertas = detectar_ventas_inusuales([p(60, 20, "A", 1), p(200, 40, "B", 2)], 30, 30)
    assert [a["nombre"] for a in alertas] == ["B", "A"]


@pytest.fixture()
def producto_del_mes(app):
    id_producto = crear_producto(app, "ALE-" + uuid.uuid4().hex[:6], precio=10, stock=500)
    inicio = date.today().replace(day=1)
    with app.app_context():
        db.session.add(PrediccionDemanda(id_producto=id_producto, horizonte_semanas=4, demanda_estimada=10, periodo_objetivo=inicio,
                                         confiabilidad="Media"))
        db.session.commit()
    yield id_producto
    with app.app_context():
        PrediccionDemanda.query.filter_by(id_producto=id_producto).delete()
        db.session.commit()


def test_las_ventas_del_mes_se_comparan_con_la_prediccion_y_la_pantalla_avisa(app, admin, producto_del_mes):
    from app.analitica.routes import _alertas_de_ventas

    hoy = date.today()
    with app.app_context():
        usuario, sede, cliente = Usuario.query.first(), Sede.query.first(), Cliente.query.first()
        for anulada, cantidad in ((False, 80), (True, 500)):   # la venta anulada no cuenta
            v = Venta(numero_operacion=uuid.uuid4().hex[:12], fecha_hora=datetime.now(), id_cliente=cliente.id_cliente,
                      id_usuario=usuario.id_usuario, id_sede=sede.id_sede, total=cantidad * 10, anulada=anulada)
            db.session.add(v)
            db.session.flush()
            db.session.add(DetalleVenta(id_venta=v.id_venta, id_producto=producto_del_mes, cantidad=cantidad,
                                        precio_unitario=10, subtotal=cantidad * 10))
        db.session.commit()
        fin_de_mes = hoy.replace(day=calendar.monthrange(hoy.year, hoy.month)[1])
        alertas = [a for a in _alertas_de_ventas(fin_de_mes) if a["id_producto"] == producto_del_mes]
    assert len(alertas) == 1 and alertas[0]["tipo"] == "mas" and alertas[0]["vendido"] == 80 and alertas[0]["esperado_mes"] == 10
    # con la fecha real: 80 vendidos contra 10 esperados es «más de lo normal» en cualquier día del mes
    html = admin.get("/analitica").get_data(as_text=True)
    assert "Ventas inusuales este mes" in html and "Más de lo normal" in html


def test_sin_prediccion_para_este_mes_no_se_alerta_nada(app):
    from app.analitica.routes import _alertas_de_ventas

    with app.app_context():
        assert _alertas_de_ventas(date(2001, 1, 31)) == []
