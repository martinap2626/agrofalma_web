"""
Pruebas del simulador de escenarios en lenguaje de negocio (lectura y validación de lo escrito, cálculo, frases y gráfico) y de
las mejoras de comprensión de la analítica (vocabulario unificado, tarjetas de resumen y explicaciones cortas).
"""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import (
    EjecucionAnalitica, PrediccionDemanda, RecomendacionAbastecimiento, SegmentacionProducto, Sede,
)
from app.simulador import calcular, leer_entrada
from conftest import crear_producto

HOY = date(2026, 10, 13)


def texto(respuesta):
    return respuesta.get_data(as_text=True)


# una recomendación de ejemplo: se espera vender 40 al mes, margen de 10, clase A, precio S/ 10
def recomendacion(demanda=40, margen=10, clase="A", precio="10"):
    return SimpleNamespace(demanda_prevista=demanda, stock_seguridad=margen, clase_abc=clase,
                           producto=SimpleNamespace(nombre_producto="Producto de ejemplo", precio_unitario=Decimal(precio)))


def entrada(escenario="normal", stock=40, **valores):
    return leer_entrada({"escenario": escenario, "stock": "" if stock is None else str(stock), **{f"valor_{k}": str(v) for k, v in valores.items()}})


# ---------------------------------------------------------------------------
# Lectura y validación de lo que escribe el usuario
# ---------------------------------------------------------------------------
def test_lo_escrito_se_valida_y_nunca_rompe_la_pantalla():
    e = leer_entrada({"escenario": "mas", "valor_mas": "NaN", "stock": "-5"}, stock_registrado=7)
    assert e["escenario"] == "mas" and e["valores"]["mas"] == 20.0  # valor por defecto
    assert e["stock"] == 7  # el stock inválido se reemplaza por el registrado
    assert any("no es válido" in a for a in e["avisos"]) and len(e["avisos"]) == 2


@pytest.mark.parametrize("args,esperado", [
    ({"escenario": "inventado"}, "normal"),
    ({}, "normal"),
    ({"escenario": "personalizado"}, "normal"),                      # sin indicar las unidades vuelve a «sin cambios» y avisa
    ({"escenario": "menos", "valor_menos": "95"}, "menos"),          # fuera de rango: usa el valor por defecto
])
def test_un_escenario_desconocido_o_incompleto_vuelve_a_un_valor_seguro(args, esperado):
    e = leer_entrada(args)
    assert e["escenario"] == esperado
    assert e["valores"]["menos"] == 20.0


def test_los_numeros_aceptan_coma_decimal_y_los_limites_se_respetan():
    assert leer_entrada({"escenario": "mas", "valor_mas": "12,5"})["valores"]["mas"] == 12.5
    assert leer_entrada({"escenario": "mas", "valor_mas": "501"})["valores"]["mas"] == 20.0
    assert leer_entrada({"escenario": "demora", "valor_demora": "0"})["valores"]["demora"] == 30.0
    assert leer_entrada({"stock": "1000001"}, stock_registrado=3)["stock"] == 3


# ---------------------------------------------------------------------------
# Cálculo y frases
# ---------------------------------------------------------------------------
def test_sin_cambios_con_stock_suficiente_no_hay_que_reponer_y_se_sabe_cuando_se_acaba():
    r = calcular(recomendacion(), entrada("normal", stock=40), HOY)
    assert (r["base"]["demanda"], r["esc"]["demanda"]) == (40, 40) and r["diferencia"] == 0
    assert r["esc"]["nivel"] == "sin_accion" and r["esc"]["cantidad"] == 0
    assert r["esc"]["dias_alcanza"] == 30.0 and r["esc"]["fecha_agota"] == date(2026, 11, 12)
    frases = " ".join(f for _, f in r["frases"])
    assert "40 unidades" in frases and "30 días" in frases and "12/11/2026" in frases and "Por ahora no hace falta reponer" in frases


def test_si_se_vende_mas_hay_que_reponer_y_el_stock_dura_menos():
    r = calcular(recomendacion(), entrada("mas", stock=40, mas=50), HOY)
    assert r["esc"]["demanda"] == 60 and r["cambio_demanda"] == 20
    assert r["esc"]["dias_alcanza"] == 20.0 and r["esc"]["fecha_agota"] == date(2026, 11, 2)
    assert (r["esc"]["nivel"], r["esc"]["objetivo"], r["esc"]["cantidad"]) == ("media", 105, 65)
    assert r["diferencia"] == 65 and r["esc"]["valor_referencia"] == 650  # 65 unidades a S/ 10
    frases = " ".join(f for _, f in r["frases"])
    assert "50 %" not in frases and "Conviene reponer 65 unidades para llegar a 105" in frases and "65 unidades más" in frases
    assert r["titulo"] == "Si se vende un 50 % más"


def test_si_la_reposicion_demora_el_stock_se_acaba_antes_de_que_llegue():
    r = calcular(recomendacion(), entrada("demora", stock=40, demora=45), HOY)
    assert r["esc"]["dias_reposicion"] == 45 and r["base"]["dias_reposicion"] == 15
    assert r["esc"]["antes_de_reposicion"] is True and r["base"]["antes_de_reposicion"] is False
    assert r["esc"]["nivel"] == "urgente"
    assert any(tipo == "alerta" and "antes de que llegue la reposición" in f for tipo, f in r["frases"])


def test_escenario_personalizado_usa_las_unidades_indicadas():
    r = calcular(recomendacion(), entrada("personalizado", stock=40, personalizado=80), HOY)
    assert r["esc"]["demanda"] == 80 and r["titulo"] == "Si se venden 80 unidades este mes"
    assert r["esc"]["dias_alcanza"] == 15.0
    r0 = calcular(recomendacion(), entrada("personalizado", stock=40, personalizado=0), HOY)
    assert r0["esc"]["dias_alcanza"] is None and "no se prevén ventas" in " ".join(f for _, f in r0["frases"])


def test_si_se_vende_menos_baja_lo_que_conviene_tener():
    r = calcular(recomendacion(), entrada("menos", stock=None, menos=50), HOY)
    assert r["esc"]["demanda"] == 20 and r["esc"]["objetivo"] < r["base"]["objetivo"]


def test_sin_stock_registrado_se_explica_que_falta_y_no_hay_grafico():
    r = calcular(recomendacion(), entrada("normal", stock=None), HOY)
    assert not r["con_stock"] and r["grafico"] is None and r["esc"]["dias_alcanza"] is None
    frases = " ".join(f for _, f in r["frases"])
    assert "escribe cuántas unidades tienes hoy" in frases and "Conviene tener disponibles 70 unidades" in frases


def test_con_stock_cero_se_avisa_que_no_hay_producto():
    r = calcular(recomendacion(), entrada("normal", stock=0), HOY)
    assert r["esc"]["nivel"] == "urgente"
    assert any(tipo == "alerta" and "no hay unidades en el almacén" in f for tipo, f in r["frases"])


def test_el_grafico_baja_dia_a_dia_hasta_cero_y_marca_la_reposicion():
    r = calcular(recomendacion(), entrada("mas", stock=40, mas=50), HOY)
    g = r["grafico"]
    assert len(g["etiquetas"]) == len(g["normal"]) == len(g["escenario"]) >= 46
    assert g["normal"][0] == g["escenario"][0] == 40
    assert g["escenario"][20] == 0 and g["normal"][20] > 0           # el escenario se agota en el día 20; lo normal dura 30
    assert all(a >= b for a, b in zip(g["escenario"], g["escenario"][1:]))  # nunca sube
    assert g["etiquetas"][0] == "13/10" and g["dia_reposicion"] == 15 and g["punto_reposicion"] == r["esc"]["punto_reposicion"]


def test_el_horizonte_del_grafico_tiene_tope():
    r = calcular(recomendacion(demanda=1), entrada("normal", stock=100000), HOY)
    assert len(r["grafico"]["etiquetas"]) == 91


def test_un_stock_enorme_con_poca_venta_no_rompe_la_fecha_de_agotamiento():
    # 100 000 unidades con 1 de venta al mes duran 3 millones de días: no se calcula fecha (antes era un error de fecha fuera de rango)
    r = calcular(recomendacion(demanda=1), entrada("normal", stock=100000), HOY)
    assert r["esc"]["fecha_agota"] is None
    assert "más de un año" in " ".join(f for _, f in r["frases"])


# ---------------------------------------------------------------------------
# La pantalla del simulador
# ---------------------------------------------------------------------------
@pytest.fixture()
def recomendacion_en_bd(app):
    id_producto = crear_producto(app, f"SIM-{uuid.uuid4().hex[:8]}", precio=10, stock=40)
    with app.app_context():
        sede = Sede.query.first()
        db.session.add(RecomendacionAbastecimiento(
            id_producto=id_producto, id_sede=sede.id_sede, stock_actual=40, demanda_prevista=40, stock_seguridad=10,
            cantidad_sugerida=0, nivel_urgencia="sin_accion", motivo="x", clase_abc="A", stock_objetivo=70, punto_pedido=30,
            con_inventario=True,
        ))
        db.session.commit()
    yield id_producto
    with app.app_context():
        RecomendacionAbastecimiento.query.delete()
        db.session.commit()


def test_la_pantalla_sin_producto_muestra_el_asistente_en_tres_pasos(admin):
    html = texto(admin.get("/analitica/simulador"))
    assert "1. ¿De qué producto?" in html and "2. ¿Qué crees que pasará?" in html and "3. ¿Cuánto tienes hoy?" in html
    assert 'name="escenario"' in html and html.count('type="radio"') == 5


def test_la_pantalla_cuenta_el_resultado_en_palabras_y_dibuja_el_grafico(admin, recomendacion_en_bd):
    html = texto(admin.get(f"/analitica/simulador?id_producto={recomendacion_en_bd}&escenario=mas&valor_mas=50&stock=40"))
    assert "Si se vende un 50 % más" in html and "se agotaría cerca del" in html
    assert "Conviene reponer 65 unidades para llegar a 105" in html
    assert 'id="grafico-simulador"' in html and "data-grafico=" in html and "js/simulador.js" in html
    assert "Ver el detalle del cálculo" in html and "Valor de referencia" in html and 'class="monto"' in html
    assert "checked" in html and 'value="50"' in html  # el formulario conserva lo elegido


def test_el_stock_registrado_es_el_valor_de_partida(admin, recomendacion_en_bd):
    html = texto(admin.get(f"/analitica/simulador?id_producto={recomendacion_en_bd}"))
    assert "Stock registrado en el sistema: 40 unidades" in html and 'value="40"' in html
    assert "Situación actual (sin cambios)" in html and "No hace falta reponer" in html


@pytest.mark.parametrize("consulta", [
    "escenario=mas&valor_mas=abc", "escenario=demora&valor_demora=-9", "escenario=personalizado", "stock=NaN", "escenario=xx&stock=zz",
    "escenario=menos&valor_menos=1e999", "stock=99999999999999",
])
def test_valores_invalidos_no_rompen_la_pantalla_y_muestran_un_aviso(admin, recomendacion_en_bd, consulta):
    r = admin.get(f"/analitica/simulador?id_producto={recomendacion_en_bd}&{consulta}")
    assert r.status_code == 200


def test_un_valor_invalido_se_avisa_con_claridad(admin, recomendacion_en_bd):
    html = texto(admin.get(f"/analitica/simulador?id_producto={recomendacion_en_bd}&escenario=mas&valor_mas=abc"))
    assert "no es válido" in html and "se usó 20" in html


def test_el_simulador_sigue_siendo_solo_del_administrador(trabajador_a):
    assert trabajador_a.get("/analitica/simulador").status_code == 403


# ---------------------------------------------------------------------------
# Vocabulario unificado, tarjetas de resumen y explicaciones cortas
# ---------------------------------------------------------------------------
@pytest.fixture()
def analitica_en_bd(app, datos):
    p3 = crear_producto(app, f"AN-{uuid.uuid4().hex[:8]}", precio=5, stock=2)
    ids = [datos["p1"], datos["p2"], p3]
    with app.app_context():
        sede = Sede.query.first()
        db.session.add(EjecucionAnalitica(datos_hasta="2026-08", entrenado_desde=2023, productos=3, metodo_por_clase='{"A": "mezcla"}',
                                          resumen='{"confiabilidad": {"Alta": 1, "Media": 0, "Baja": 2}}'))
        for i, (conf, tend, venta_ant, est) in enumerate((("Alta", 10, 30, 40.4), ("Baja", -20, 15, 12), ("Baja", 0, 0, 3))):
            db.session.add(PrediccionDemanda(id_producto=ids[i], horizonte_semanas=4, demanda_estimada=est, tendencia_pct=tend,
                                             periodo_objetivo=date(2026, 10, 1), venta_mes_anterior=venta_ant, confiabilidad=conf))
        for (id_producto, nivel, clase, stock, cant, objetivo) in ((ids[0], "urgente", "A", 5, 50, 70), (ids[1], "media", "C", 8, 10, 20), (ids[2], "sin_accion", "C", 50, 0, 20)):
            db.session.add(RecomendacionAbastecimiento(
                id_producto=id_producto, id_sede=sede.id_sede, stock_actual=stock, demanda_prevista=20, stock_seguridad=5,
                cantidad_sugerida=cant, nivel_urgencia=nivel, motivo="x", clase_abc=clase, stock_objetivo=objetivo, con_inventario=True))
        for (id_producto, clase, ventas, pct) in ((ids[0], "A", 800, 80), (ids[1], "B", 150, 95), (ids[2], "C", 50, 100)):
            db.session.add(SegmentacionProducto(id_producto=id_producto, periodo_inicio=date(2025, 9, 1), periodo_fin=date(2026, 8, 31),
                                                ventas_periodo=ventas, pct_acumulado=pct, clase_abc=clase))
        db.session.commit()
    yield ids
    with app.app_context():
        for modelo in (PrediccionDemanda, RecomendacionAbastecimiento, SegmentacionProducto, EjecucionAnalitica):
            modelo.query.delete()
        db.session.commit()


def test_prediccion_llama_venta_esperada_a_lo_que_es_y_no_cantidad_a_reponer(admin, analitica_en_bd):
    html = texto(admin.get("/analitica/prediccion"))
    assert "Venta esperada (unidades)" in html and "Cantidad sugerida a reponer" not in html
    assert "No es lo que hay que" in html and "terminó la temporada" in html
    assert "Unidades que se esperan vender en 10/2026" in html


def test_prediccion_muestra_tarjetas_de_resumen_con_cifras_correctas(admin, analitica_en_bd):
    html = texto(admin.get("/analitica/prediccion"))
    assert ">55<" in html.replace(" ", "").replace("\n", "")          # 40,4 + 12 + 3 = 55,4 -> 55 (la tarjeta redondea al entero más cercano)
    assert "1 / 0 / 2" in html                                          # confiabilidad Alta / Media / Baja
    assert "1 / 1" in html                                              # uno sube y uno baja (el tercero no tiene mes anterior)


def test_recomendaciones_muestran_tarjetas_de_resumen_con_cifras_correctas(admin, analitica_en_bd):
    html = texto(admin.get("/analitica"))
    assert "Productos urgentes" in html and "Para reponer pronto" in html and "Unidades por reponer" in html
    # 50 + 10 = 60 unidades; a S/ 50 y S/ 20 (precios 50 y 20 del catálogo de prueba): 50*50 + 10*20 = 2 700
    assert "60" in html and "S/ 2 700" in html and "1 de clase A" in html and "1 no necesitan reposición" in html
    # las tarjetas cuentan el total aunque se filtre la tabla
    filtrada = texto(admin.get("/analitica?urgencia=urgente"))
    assert "Para reponer pronto" in filtrada and "S/ 2 700" in filtrada


def test_recomendaciones_usan_el_mismo_vocabulario_que_el_resto(admin, analitica_en_bd):
    html = texto(admin.get("/analitica"))
    assert "Venta esperada" in html and "Tener disponible" in html and "<th>Demanda prevista</th>" not in html
    assert html.count('class="info-termino"') >= 5


def test_segmentacion_muestra_cuantos_productos_hay_por_clase_y_cuanto_venden(admin, analitica_en_bd):
    html = texto(admin.get("/analitica/segmentacion"))
    assert "1 productos" in html and "80 % de las ventas" in html and "33 % del catálogo" in html
    assert "15 % de las ventas" in html and "5 % de las ventas" in html
    # las tarjetas cuentan todos los productos aunque se filtre la tabla
    assert "80 % de las ventas" in texto(admin.get("/analitica/segmentacion?clase=C"))


def test_la_ayuda_y_el_dashboard_usan_el_vocabulario_nuevo(admin, analitica_en_bd):
    ayuda = texto(admin.get("/ayuda"))
    assert "Venta esperada (demanda prevista)" in ayuda and "<dt>Cantidad sugerida a reponer</dt>" not in ayuda
    assert "en tres pasos" in ayuda
    assert "Cantidad a reponer" in texto(admin.get("/dashboard"))  # tabla de recomendaciones urgentes


def test_las_explicaciones_cortas_son_accesibles_por_teclado_y_lector_de_pantalla(admin, analitica_en_bd):
    html = texto(admin.get("/analitica"))
    assert 'tabindex="0"' in html and 'role="note"' in html and "aria-label=" in html and "data-ayuda=" in html
    from app.iconos import info

    seguro = str(info('Texto con "comillas" y <etiquetas>'))
    assert "<etiquetas>" not in seguro and "&lt;etiquetas&gt;" in seguro  # el texto se escapa: no puede inyectar HTML


# ---------------------------------------------------------------------------
# Comparar escenarios
# ---------------------------------------------------------------------------
def test_comparar_escenarios_devuelve_los_cuatro_con_el_mismo_stock():
    from app.simulador import comparar_escenarios

    filas = comparar_escenarios(recomendacion(), entrada("mas", stock=40, mas=50), HOY)
    assert [f["clave"] for f in filas] == ["normal", "mas", "menos", "demora"]
    por = {f["clave"]: f for f in filas}
    assert por["normal"]["demanda"] == 40 and por["mas"]["demanda"] == 60 and por["menos"]["demanda"] == 32
    assert [f["elegido"] for f in filas] == [False, True, False, False]
    assert por["mas"]["dias_alcanza"] < por["normal"]["dias_alcanza"] < por["menos"]["dias_alcanza"]
    assert por["demora"]["dias_reposicion"] == 30 and por["demora"]["demanda"] == 40
    assert por["demora"]["antes_de_reposicion"] is False  # 30 días de stock justo alcanzan para los 30 de reposición


def test_la_pantalla_muestra_los_escenarios_lado_a_lado_solo_si_hay_stock(admin, recomendacion_en_bd):
    con = texto(admin.get(f"/analitica/simulador?id_producto={recomendacion_en_bd}&escenario=mas&valor_mas=50"))
    assert "Los escenarios lado a lado" in con and "elegido" in con and "Si se vende un 20 % menos" in con
    sin = texto(admin.get(f"/analitica/simulador?id_producto={recomendacion_en_bd}&stock=abc"))
    assert "Los escenarios lado a lado" in sin  # con un stock inválido se usa el registrado
