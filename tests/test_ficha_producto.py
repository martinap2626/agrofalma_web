"""Pruebas de la ficha de producto: rango probable, riesgo de quedarse sin stock, ventas por mes y la pantalla."""

import uuid
from datetime import date, datetime

import pytest

from app.extensions import db
from app.ficha_producto import error_relativo, rango_probable, riesgo_de_quiebre, ventas_por_mes
from app.models import (Cliente, DetalleVenta, PrediccionDemanda, RecomendacionAbastecimiento, Sede, Venta, Usuario)
from conftest import crear_producto


def texto(respuesta):
    return respuesta.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Cálculos
# ---------------------------------------------------------------------------
def test_el_error_se_acota_y_sin_dato_se_usa_la_confiabilidad():
    assert error_relativo(35, "Alta") == pytest.approx(0.35)
    assert error_relativo(2, "Alta") == pytest.approx(0.10)       # no se promete un rango demasiado estrecho
    assert error_relativo(400, "Baja") == pytest.approx(1.0)      # ni uno enorme
    assert error_relativo(None, "Alta") == pytest.approx(0.20) and error_relativo(None, "Media") == pytest.approx(0.40)
    assert error_relativo(None, None) == pytest.approx(0.70)


def test_el_rango_probable_se_redondea_hacia_afuera_y_no_baja_de_cero():
    assert rango_probable(40, 0.25) == (30, 50)
    assert rango_probable(10.5, 0.30) == (7, 14)   # 7.35 -> 7 ; 13.65 -> 14
    assert rango_probable(3, 1.0) == (0, 6)
    assert rango_probable(0, 0.5) == (0, 0)


def test_sin_stock_registrado_no_hay_riesgo_que_calcular():
    assert riesgo_de_quiebre(None, 40, 0.3, 15) is None


def test_el_riesgo_sube_cuando_el_stock_no_alcanza_para_la_espera():
    # se espera vender 20 en 15 días (demanda 40 al mes)
    sobra = riesgo_de_quiebre(100, 40, 0.3, 15)
    justo = riesgo_de_quiebre(20, 40, 0.3, 15)
    falta = riesgo_de_quiebre(5, 40, 0.3, 15)
    assert sobra["nivel"] == "Bajo" and sobra["probabilidad_pct"] < 10
    assert justo["probabilidad_pct"] == 50 and justo["nivel"] == "Alto"
    assert falta["nivel"] == "Alto" and falta["probabilidad_pct"] > 90
    assert sobra["probabilidad_pct"] < justo["probabilidad_pct"] < falta["probabilidad_pct"]


def test_mas_dias_de_reposicion_aumentan_el_riesgo_con_el_mismo_stock():
    assert riesgo_de_quiebre(30, 40, 0.3, 45)["probabilidad_pct"] > riesgo_de_quiebre(30, 40, 0.3, 15)["probabilidad_pct"]


def test_con_stock_cero_el_riesgo_es_total_y_si_casi_no_se_vende_es_nulo():
    assert riesgo_de_quiebre(0, 40, 0.3, 15) == {"probabilidad_pct": 100, "nivel": "Alto", "espera": 20.0}
    assert riesgo_de_quiebre(0, 0.2, 0.3, 15)["probabilidad_pct"] == 0


def test_las_ventas_por_mes_cubren_los_12_meses_aunque_no_haya_ventas():
    filas = [(date(2026, 10, 3), 5), (date(2026, 10, 20), 7), (date(2026, 8, 1), 4), (date(2024, 1, 1), 99)]
    meses = ventas_por_mes(filas, date(2026, 10, 13))
    assert len(meses) == 12 and meses[0]["etiqueta"] == "11/2025" and meses[-1]["etiqueta"] == "10/2026"
    assert meses[-1]["unidades"] == 12 and meses[-3]["unidades"] == 4 and meses[-2]["unidades"] == 0
    assert meses[-1]["ancho_pct"] == 100 and meses[-3]["ancho_pct"] == 33
    assert sum(m["unidades"] for m in meses) == 16  # la venta de 2024 queda fuera


def test_sin_ninguna_venta_los_anchos_son_cero_y_no_hay_division_por_cero():
    assert {m["ancho_pct"] for m in ventas_por_mes([], date(2026, 1, 15))} == {0}


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------
@pytest.fixture()
def producto_con_analitica(app):
    codigo = "FIC-" + uuid.uuid4().hex[:6]
    id_producto = crear_producto(app, codigo, precio=20, stock=30)
    with app.app_context():
        sede = Sede.query.first()
        db.session.add(PrediccionDemanda(id_producto=id_producto, horizonte_semanas=4, demanda_estimada=40, tendencia_pct=-10,
                                         periodo_objetivo=date(2026, 11, 1), venta_mes_anterior=44, confiabilidad="Media",
                                         error_validacion=30))
        db.session.add(RecomendacionAbastecimiento(
            id_producto=id_producto, id_sede=sede.id_sede, stock_actual=5, demanda_prevista=40, stock_seguridad=10,
            cantidad_sugerida=60, nivel_urgencia="urgente", motivo="Se acabaría antes de la reposición", clase_abc="A",
            stock_objetivo=70, punto_pedido=30, con_inventario=True))
        db.session.commit()
    yield id_producto
    with app.app_context():
        PrediccionDemanda.query.filter_by(id_producto=id_producto).delete()
        RecomendacionAbastecimiento.query.filter_by(id_producto=id_producto).delete()
        db.session.commit()


def test_la_ficha_muestra_rango_riesgo_y_que_hacer(admin, producto_con_analitica):
    r = admin.get(f"/analitica/producto/{producto_con_analitica}")
    html = texto(r)
    assert r.status_code == 200
    assert "Venta esperada del mes" in html and ">40<" in html
    assert "28 a 52" in html            # 40 -/+ 30 %
    assert "Riesgo de quedarse sin producto" in html and "etiqueta-alerta\">Alto" in html
    assert "Reponer" in html and "<strong>60</strong>" in html and "Tener disponible" in html and "70" in html
    assert "Ventas de los últimos 12 meses" in html and "Simular escenarios" in html


def test_la_ficha_solo_es_para_el_administrador_y_valida_el_producto(admin, trabajador_a, producto_con_analitica):
    assert trabajador_a.get(f"/analitica/producto/{producto_con_analitica}").status_code == 403
    assert admin.get("/analitica/producto/99999999").status_code == 404


def test_un_producto_sin_prediccion_explica_que_falta_y_no_se_cae(app, admin):
    id_producto = crear_producto(app, "FIC-" + uuid.uuid4().hex[:6])
    html = texto(admin.get(f"/analitica/producto/{id_producto}"))
    assert "Todavía no hay una predicción" in html and "Ventas de los últimos 12 meses" in html


def test_las_ventas_anuladas_no_cuentan_en_la_ficha(app, admin, datos, producto_con_analitica):
    with app.app_context():
        usuario = Usuario.query.first()
        sede = Sede.query.first()
        cliente = Cliente.query.first()
        for anulada in (False, True):
            v = Venta(numero_operacion=uuid.uuid4().hex[:12], fecha_hora=datetime.now(), id_cliente=cliente.id_cliente,
                      id_usuario=usuario.id_usuario, id_sede=sede.id_sede, total=20, anulada=anulada)
            db.session.add(v)
            db.session.flush()
            db.session.add(DetalleVenta(id_venta=v.id_venta, id_producto=producto_con_analitica, cantidad=7,
                                        precio_unitario=20, subtotal=140))
        db.session.commit()
    html = texto(admin.get(f"/analitica/producto/{producto_con_analitica}"))
    mes_actual = date.today().strftime("%m/%Y")
    assert f"<td>{mes_actual}</td>\n                <td>7</td>" in html


def test_las_tablas_de_analitica_enlazan_a_la_ficha(admin, producto_con_analitica):
    enlace = f"/analitica/producto/{producto_con_analitica}"
    assert enlace in texto(admin.get("/analitica"))


def test_la_ventana_de_12_meses_termina_en_el_mes_de_la_ultima_venta():
    from app.ficha_producto import inicio_de_ventana

    assert inicio_de_ventana(date(2026, 8, 31)) == date(2025, 9, 1)
    assert inicio_de_ventana(date(2026, 1, 15)) == date(2025, 2, 1)
    assert inicio_de_ventana(date(2026, 12, 1)) == date(2026, 1, 1)


def _serie(unidades):
    return [{"etiqueta": f"{i + 1:02d}/2026", "unidades": u} for i, u in enumerate(unidades)]


def test_el_patron_de_demanda_se_explica_en_palabras():
    from app.ficha_producto import patron_de_demanda

    assert patron_de_demanda(_serie([0] * 12))["tipo"] == "Sin ventas"
    esporadica = patron_de_demanda(_serie([0, 5, 0, 0, 0, 0, 3, 0, 0, 0, 0, 0]))
    assert esporadica["tipo"] == "Esporádica" and "2 de 12" in esporadica["texto"]
    temporada = patron_de_demanda(_serie([0, 0, 40, 60, 50, 2, 0, 0, 0, 1, 0, 0]))
    assert temporada["tipo"] == "De temporada" and temporada["meses_fuertes"] == ["04/2026", "05/2026", "03/2026"]
    assert patron_de_demanda(_serie([20, 22, 18, 25, 21, 19, 23, 20, 24, 22, 21, 20]))["tipo"] == "Regular"
    variable = patron_de_demanda(_serie([1, 40, 2, 35, 1, 50, 3, 2, 45, 1, 30, 2]))
    assert variable["tipo"] == "Variable"


def test_la_ficha_muestra_como_se_vende(admin, producto_con_analitica):
    assert "Cómo se vende" in texto(admin.get(f"/analitica/producto/{producto_con_analitica}"))
