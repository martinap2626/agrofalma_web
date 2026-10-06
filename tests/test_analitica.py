"""
Pruebas del módulo de analítica: limpieza de nombres, ABC, método de pronóstico (sin
mirar el futuro), regla de recomendaciones, catálogo base, inventario y su guardado
en MySQL, y las pantallas con y sin ejecución real.
"""

import re

import numpy as np
import pandas as pd
import pymysql
import pytest

from analitica_job.pipeline import limpieza, modelos
from analitica_job.pipeline.clasificacion_abc import segmentar
from analitica_job.pipeline.ejecutar import ResultadoAnalitica, confiabilidad
from analitica_job.pipeline.recomendaciones import PARAMETROS, generar
from app import create_app
from app.extensions import db
from app.models import (
    Categoria, EjecucionAnalitica, PrediccionDemanda, Producto, RecomendacionAbastecimiento, Rol, Sede,
    SegmentacionProducto, StockPorSede, Usuario, Venta,
)
from app import servicios_analitica as srv
from config import Config
from conftest import ConfigPruebas

BD_ANALITICA = "agrofalma_analitica_test"


# ---------------------------------------------------------------------------
# Limpieza de nombres
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("nombre,basura", [
    ("0003031", True), ("003111", True), ("JULIO", True), ("TOTAL", True), ("1 2", True),
    ("AGRO RAZ", False), ("FF 20.20.20", False), ("MAX 250 ML", False),
])
def test_se_descartan_comprobantes_meses_y_totales(nombre, basura):
    assert limpieza.es_basura(nombre) is basura


@pytest.mark.parametrize("nombre,esperado", [
    ("AGRO RAZ 250 ML", ("AGRO RAZ", "250 ML")),
    ("PENTA X 500", ("PENTA", "500 ML")) if False else ("PENTA 500 GR", ("PENTA", "500 GR")),
    ("MENORES X GALON", ("MENORES", "4 L (galón)")),
    ("RAZ X LITRO", ("RAZ", "1 L")),
    ("SULFATO X 50 KG", ("SULFATO", "50 KG")),
    ("BENZOMIL", ("BENZOMIL", limpieza.SIN_PRESENTACION)),
])
def test_interpretar_nombre_separa_base_y_presentacion(nombre, esperado):
    assert limpieza.interpretar_nombre(nombre) == esperado


def test_diagnostico_detecta_un_anio_copiado_y_no_acusa_a_datos_independientes():
    azar = np.random.default_rng(3)
    base = [f"P{i}" for i in range(60)]
    v2025 = azar.integers(10, 60, size=(12, 60))

    def df_de(valores_por_anio):
        filas = []
        for anio, matriz in valores_por_anio.items():
            for m in range(12):
                for i, b in enumerate(base):
                    filas.append({"anio": anio, "mes": m + 1, "base": b, "unidades": float(matriz[m, i])})
        return pd.DataFrame(filas)

    copiado = df_de({2025: v2025, 2026: v2025.copy()})
    independiente = df_de({2025: v2025, 2026: azar.integers(10, 60, size=(12, 60))})
    assert limpieza.diagnostico_calidad(copiado)[0]["atipico"] is True
    assert limpieza.diagnostico_calidad(independiente)[0]["atipico"] is False


# ---------------------------------------------------------------------------
# Segmentación ABC
# ---------------------------------------------------------------------------
def _ventas(valores):
    return pd.DataFrame([{"anio": 2026, "mes": 8, "base": b, "soles": s, "categoria": "QUIMICOS"} for b, s in valores.items()])


def test_abc_aplica_pareto_y_clasifica_a_todos_los_productos_del_catalogo():
    df = _ventas({"A1": 800, "B1": 100, "B2": 40, "C1": 40, "C2": 20})
    abc = segmentar(df, 2026, 8, ["A1", "B1", "B2", "C1", "C2", "SIN_VENTAS"]).set_index("base")
    assert abc.loc["A1", "clase_abc"] == "A"           # 80% acumulado
    assert abc.loc["B1", "clase_abc"] == "B" and abc.loc["B2", "clase_abc"] == "B"
    assert abc.loc["C1", "clase_abc"] == "C" and abc.loc["C2", "clase_abc"] == "C"
    assert abc.loc["SIN_VENTAS", "clase_abc"] == "C" and abc.loc["SIN_VENTAS", "ventas_periodo"] == 0
    assert len(abc) == 6 and abc["clase_abc"].notna().all()  # indicador del proyecto: 100% clasificados


def test_abc_usa_solo_los_ultimos_12_meses():
    df = pd.DataFrame([
        {"anio": 2026, "mes": 8, "base": "NUEVO", "soles": 100.0, "categoria": "QUIMICOS"},
        {"anio": 2025, "mes": 7, "base": "VIEJO", "soles": 9999.0, "categoria": "QUIMICOS"},  # fuera de la ventana
    ])
    abc = segmentar(df, 2026, 8, ["NUEVO", "VIEJO"]).set_index("base")
    assert abc.loc["VIEJO", "ventas_periodo"] == 0 and abc.loc["NUEVO", "ventas_periodo"] == 100


# ---------------------------------------------------------------------------
# Pronóstico: métodos simples y ausencia de fuga del futuro
# ---------------------------------------------------------------------------
def _serie(matriz, inicio="2025-01"):
    matriz = np.asarray(matriz, dtype=float)
    periodos = pd.period_range(inicio, periods=matriz.shape[1], freq="M")
    return modelos.Serie(Y=matriz, productos=[f"P{i}" for i in range(matriz.shape[0])], periodos=periodos,
                         categoria=np.zeros(matriz.shape[0]))


def test_metodos_simples_con_una_serie_conocida():
    Y = np.array([[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 20]])
    assert modelos.pronostico_simple(Y, 12, "ingenuo")[0] == 12
    assert modelos.pronostico_simple(Y, 12, "promedio_3")[0] == pytest.approx((10 + 11 + 12) / 3)
    assert modelos.pronostico_simple(Y, 12, "estacional")[0] == 1            # mismo mes del año anterior
    assert modelos.pronostico_simple(Y, 12, "mezcla")[0] == pytest.approx(0.5 * 1 + 0.5 * 11)


@pytest.mark.parametrize("metodo", ["ingenuo", "promedio_3", "promedio_6", "estacional", "mezcla"])
def test_el_pronostico_no_depende_del_mes_que_se_predice(metodo):
    azar = np.random.default_rng(5)
    Y = azar.integers(0, 30, size=(10, 18)).astype(float)
    antes = modelos.pronostico_simple(Y, 14, metodo)
    Y2 = Y.copy()
    Y2[:, 14:] = 9999  # se cambia el "futuro": el pronóstico no debe moverse
    assert np.array_equal(antes, modelos.pronostico_simple(Y2, 14, metodo))


def test_el_modelo_de_aprendizaje_tampoco_mira_el_futuro():
    azar = np.random.default_rng(6)
    Y = azar.integers(0, 30, size=(12, 20)).astype(float)
    s = _serie(Y)
    antes = modelos.pronostico_ml(s, 15)
    Y2 = Y.copy()
    Y2[:, 15:] = 5000
    despues = modelos.pronostico_ml(_serie(Y2), 15)
    assert np.allclose(antes, despues)


def test_la_validacion_devuelve_todos_los_metodos_por_cada_mes_y_producto():
    azar = np.random.default_rng(8)
    s = _serie(azar.integers(0, 20, size=(6, 20)))
    val = modelos.validar(s, n_origenes=4)
    assert set(val["metodo"]) == set(modelos.METODOS)
    assert len(val) == 4 * 6 * len(modelos.METODOS) and (val["pred"] >= 0).all()


def test_se_prefiere_el_metodo_simple_si_el_complejo_no_mejora_de_verdad():
    met = pd.DataFrame([
        {"metodo": "ml_poisson", "clase": "A", "WAPE_%": 40.0},
        {"metodo": "ingenuo", "clase": "A", "WAPE_%": 41.0},   # dentro del margen: gana el simple
        {"metodo": "ml_poisson", "clase": "B", "WAPE_%": 30.0},
        {"metodo": "ingenuo", "clase": "B", "WAPE_%": 60.0},   # el complejo mejora mucho: gana
    ])
    elegido = modelos.elegir_metodo_por_clase(met)
    assert elegido == {"A": "ingenuo", "B": "ml_poisson"}


def test_un_metodo_que_falla_en_lo_reciente_no_gana_solo_por_el_pasado():
    total = pd.DataFrame([{"metodo": "estacional", "clase": "A", "WAPE_%": 20.0}, {"metodo": "mezcla", "clase": "A", "WAPE_%": 40.0}])
    reciente = pd.DataFrame([{"metodo": "estacional", "clase": "A", "WAPE_%": 150.0}, {"metodo": "mezcla", "clase": "A", "WAPE_%": 50.0}])
    assert modelos.elegir_metodo_por_clase(total)["A"] == "estacional"
    assert modelos.elegir_metodo_por_clase(total, reciente)["A"] == "mezcla"


@pytest.mark.parametrize("wape,meses,esperado", [
    (20, 12, "Alta"), (20, 5, "Media"), (50, 12, "Media"), (90, 12, "Baja"), (None, 12, "Baja"), (10, 2, "Baja"),
])
def test_confiabilidad_en_lenguaje_de_negocio(wape, meses, esperado):
    assert confiabilidad(wape, meses) == esperado


# ---------------------------------------------------------------------------
# Regla de recomendaciones (a partir de la predicción y la clase ABC)
# ---------------------------------------------------------------------------
def _gen(clase, prevista, stock=None, mae=4.0, confiab="Media", reparto=None):
    d = pd.DataFrame({"producto": ["X"], "clase_abc": [clase], "demanda_prevista": [prevista],
                      "mae_validacion": [mae], "confiabilidad": [confiab]})
    inv = None if stock is None else pd.DataFrame({"producto": ["X"], "stock_actual": [stock]})
    return generar(d, inv, reparto).iloc[0]


def test_objetivos_de_stock_se_calculan_con_la_demanda_el_tiempo_de_espera_y_el_margen():
    r = _gen("A", 20, stock=None, mae=4.0)
    # espera = 20 x 15/30 = 10; margen = min(1.65 x 1.25 x 4 = 8.25, 75% de 20) -> 9; objetivo = 20 + 10 + 9
    assert (r["stock_seguridad"], r["punto_pedido"], r["stock_objetivo"]) == (9, 19, 39)


def test_el_margen_de_seguridad_tiene_tope_para_no_acumular_de_mas():
    assert _gen("A", 20, mae=100.0)["stock_seguridad"] == 15        # tope: 75% de la demanda en clase A
    assert _gen("C", 20, mae=100.0)["stock_seguridad"] == 7         # tope: 35% en clase C


def test_sin_error_medido_se_usa_un_porcentaje_fijo_segun_la_clase():
    assert _gen("A", 20, mae=None)["stock_seguridad"] == 10 and _gen("C", 20, mae=None)["stock_seguridad"] == 4


def test_los_productos_clase_a_reciben_mas_proteccion_que_los_c_con_el_mismo_error():
    a, b, c = (_gen(cl, 20, mae=3.0)["stock_seguridad"] for cl in "ABC")
    assert a > b > c


def test_sin_inventario_se_entrega_el_plan_y_nunca_se_inventa_un_stock():
    r = _gen("A", 20)
    assert r["nivel_urgencia"] == "plan" and r["stock_actual"] is None and not r["con_inventario"]
    assert r["cantidad_sugerida"] == r["stock_objetivo"] == 39
    assert "Prioridad alta" in r["motivo"] and "Conviene tener disponibles" in r["motivo"]


@pytest.mark.parametrize("stock,nivel,cantidad", [
    (5, "urgente", 34),      # 5 <= 10 (se acaba antes de que llegue el pedido): pedir 39 - 5
    (15, "media", 24),       # 10 < 15 <= 19 (punto de pedido): pedir 39 - 15
    (50, "sin_accion", 0),   # por encima del punto de pedido
])
def test_con_inventario_la_urgencia_depende_del_stock_frente_a_la_espera_y_al_punto_de_pedido(stock, nivel, cantidad):
    r = _gen("A", 20, stock=stock)
    assert (r["nivel_urgencia"], r["cantidad_sugerida"]) == (nivel, cantidad) and bool(r["con_inventario"])


def test_en_clase_c_lo_que_se_acabaria_antes_del_pedido_es_media_no_urgente():
    assert _gen("C", 20, stock=3)["nivel_urgencia"] == "media"


def test_sin_ventas_previstas_no_se_repone_ni_se_pide():
    for stock in (None, 5):
        r = _gen("A", 0.2, stock=stock)
        assert r["cantidad_sugerida"] == 0 and r["stock_objetivo"] == 0 and "no hace falta reponer" in r["motivo"]


def test_el_motivo_explica_con_palabras_y_avisa_si_el_pronostico_es_poco_confiable():
    r = _gen("A", 20, stock=5, confiab="Baja")
    assert "se acabaría antes de que llegue la reposición" in r["motivo"] and "15 días" in r["motivo"]
    assert "poco confiable" in r["motivo"]
    assert "poco confiable" not in _gen("A", 20, stock=5, confiab="Alta")["motivo"]


def test_el_reparto_por_presentacion_suma_exactamente_lo_sugerido():
    reparto = pd.DataFrame({"base": ["X", "X", "X"], "presentacion": ["1 L", "500 ML", "4 L (galón)"], "participacion_pct": [60.0, 30.0, 10.0]})
    texto = _gen("A", 20, stock=5, reparto=reparto)["reparto"]
    cantidades = [int(n) for n in re.findall(r"(\d+)(?:,|\.)", texto)]
    assert sum(cantidades) == 34 and texto.startswith("Reparto sugerido por presentación: 1 L")
    unica = pd.DataFrame({"base": ["X"], "presentacion": [limpieza.SIN_PRESENTACION], "participacion_pct": [100.0]})
    assert _gen("A", 20, stock=5, reparto=unica)["reparto"] == ""


def test_los_supuestos_estan_visibles_y_son_ajustables():
    assert PARAMETROS.tiempo_reposicion_dias == 15 and PARAMETROS.nivel_proteccion_pct("A") == 95
    assert PARAMETROS.nivel_proteccion_pct("B") == 90 and PARAMETROS.nivel_proteccion_pct("C") == 80


def test_el_motivo_cabe_en_la_columna():
    assert len(_gen("A", 123456, stock=0, mae=50000.0)["motivo"]) <= 400


# ---------------------------------------------------------------------------
# Catálogo, inventario y guardado en MySQL (base de datos propia de estas pruebas)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def app_a():
    assert BD_ANALITICA.endswith("_test")
    con = pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_USER, password=Config.DB_PASSWORD)
    with con.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{BD_ANALITICA}`")
        cur.execute(f"CREATE DATABASE `{BD_ANALITICA}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    con.commit()
    con.close()

    class Cfg(ConfigPruebas):
        SQLALCHEMY_DATABASE_URI = (f"mysql+pymysql://{Config.DB_USER}:{Config.DB_PASSWORD}@{Config.DB_HOST}:{Config.DB_PORT}/"
                                   f"{BD_ANALITICA}?charset=utf8mb4")

    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
        rol = Rol(nombre_rol="Administrador")
        db.session.add_all([rol, Rol(nombre_rol="Trabajador"), Sede(nombre_sede="Sede Pimentel"), Sede(nombre_sede="Sede Chacupe")])
        db.session.flush()
        u = Usuario(nombre="Ana", apellido="Admin", usuario_login="admin_a", id_rol=rol.id_rol, id_sede=1)
        u.set_password("AdminPrueba2026")
        db.session.add_all([u, Categoria(nombre_categoria="Prueba"), Producto(codigo_producto="T-1", nombre_producto="PRODUCTO DE PRUEBA", id_categoria=1, id_unidad=1)])
        from app.models import UnidadMedida
        db.session.add(UnidadMedida(nombre_unidad="Unidad", abreviatura="Un"))
        db.session.commit()
    return app


def _hojas_catalogo():
    productos = pd.DataFrame({
        "codigo_producto": ["QUI-001", "FOL-001"], "producto_base": ["AGRO UNO", "AGRO DOS"],
        "categoria_sistema": ["Químicos", "Foliares"], "n_presentaciones": [2, 1], "presentaciones": ["250 ML ; 1 L", "1 L"],
        "unidades_ult_12m": [120.0, 24.0], "soles_ult_12m": [3000.0, 500.0], "meses_con_venta_ult_12m": [12, 6],
        "precio_referencia": [25.0, None], "primer_mes": ["2025-01"] * 2, "ultimo_mes": ["2026-08"] * 2,
        "estado_sugerido": ["Activo"] * 2, "variantes_de_nombre": [2, 1],
    })
    presentaciones = pd.DataFrame({"producto_base": ["AGRO UNO", "AGRO UNO", "AGRO DOS"], "presentacion": ["250 ML", "1 L", "1 L"]})
    return {"productos": productos, "presentaciones": presentaciones}


def test_catalogo_en_simulacion_no_cambia_nada(app_a):
    with app_a.app_context():
        antes = Producto.query.count()
        r = srv.cargar_catalogo(_hojas_catalogo(), aplicar=False)
        assert r["aplicado"] is False and r["productos_ajenos_en_bd"] == 1 and Producto.query.count() == antes


def test_catalogo_se_niega_a_borrar_datos_de_prueba_sin_permiso_explicito(app_a):
    with app_a.app_context():
        with pytest.raises(srv.ErrorAnalitica, match="reemplazar-prueba"):
            srv.cargar_catalogo(_hojas_catalogo(), aplicar=True, reemplazar_prueba=False)
        assert Producto.query.filter_by(nombre_producto="PRODUCTO DE PRUEBA").count() == 1


def test_catalogo_reemplaza_la_prueba_con_respaldo_y_es_idempotente(app_a):
    llamado = []
    with app_a.app_context():
        r = srv.cargar_catalogo(_hojas_catalogo(), aplicar=True, reemplazar_prueba=True, respaldo_previo=lambda: llamado.append(1) or "respaldo.sql")
        assert llamado == [1] and r["creados"] == 2 and r["respaldo"] == "respaldo.sql"
        assert {p.nombre_producto for p in Producto.query.all()} == {"AGRO UNO", "AGRO DOS"}
        uno = Producto.query.filter_by(nombre_producto="AGRO UNO").one()
        assert float(uno.precio_unitario) == 25.0 and uno.stock_minimo == 10 and "250 ML" in uno.descripcion
        assert Categoria.query.filter_by(nombre_categoria="Prueba").count() == 0  # categoría de prueba sin uso: se retira
        # segunda carga: no duplica
        r2 = srv.cargar_catalogo(_hojas_catalogo(), aplicar=True, reemplazar_prueba=False)
        assert r2["creados"] == 0 and r2["actualizados"] == 2 and Producto.query.count() == 2


def test_inventario_plantilla_y_carga_con_validaciones(app_a, tmp_path):
    ruta = tmp_path / "inv.xlsx"
    with app_a.app_context():
        srv.generar_plantilla_inventario(_hojas_catalogo() | {"productos": _hojas_catalogo()["productos"]}, ruta)
        libro = pd.ExcelFile(ruta)
        assert {"Instrucciones", "Datos del conteo", "Inventario"} <= set(libro.sheet_names)

        inv = libro.parse("Inventario")
        inv["stock_actual"] = [10, 5, 8]
        with pd.ExcelWriter(ruta) as w:
            libro.parse("Datos del conteo").to_excel(w, sheet_name="Datos del conteo", index=False)
            inv.to_excel(w, sheet_name="Inventario", index=False)
        assert srv.cargar_inventario(ruta, aplicar=False)["aplicado"] is False
        assert StockPorSede.query.count() == 0
        srv.cargar_inventario(ruta, aplicar=True)
        uno = Producto.query.filter_by(nombre_producto="AGRO UNO").one()
        assert StockPorSede.query.filter_by(id_producto=uno.id_producto).one().cantidad_actual == 15  # 10 + 5 (suma de presentaciones)

        inv["stock_actual"] = [10, -1, 8]
        with pd.ExcelWriter(ruta) as w:
            inv.to_excel(w, sheet_name="Inventario", index=False)
        with pytest.raises(srv.ErrorAnalitica, match="negativo"):
            srv.cargar_inventario(ruta, aplicar=True)


def _resultado():
    pred = pd.DataFrame({
        "producto": ["AGRO UNO", "AGRO DOS", "NO ESTA EN CATALOGO"], "periodo_objetivo": ["2026-09"] * 3,
        "pronostico": [30.0, 2.0, 5.0], "metodo": ["mezcla", "ingenuo", "ingenuo"], "venta_mes_anterior": [20.0, 0.0, 1.0],
        "clase_abc": ["A", "C", "C"], "categoria": ["QUIMICOS", "FOLIARES", "FOLIARES"], "meses_con_venta_12m": [12, 3, 1],
        "error_validacion_pct": [25.0, None, 90.0], "mae_validacion": [6.0, np.nan, np.nan], "confiabilidad": ["Alta", "Baja", "Baja"],
        "cantidad_sugerida": [30, 2, 5], "tendencia_pct": [50.0, np.nan, 400.0],
    })
    abc = pd.DataFrame({
        "base": ["AGRO UNO", "AGRO DOS", "NO ESTA EN CATALOGO"], "categoria": ["QUIMICOS", "FOLIARES", "FOLIARES"],
        "ventas_periodo": [3000.0, 500.0, 0.0], "pct_acumulado": [85.71, 100.0, 100.0], "clase_abc": ["B", "C", "C"],
        "periodo_inicio": ["2025-09-01"] * 3, "periodo_fin": ["2026-08-01"] * 3,
    })
    return ResultadoAnalitica(
        anio_desde=2025, informe_calidad={"periodo": "2025-01 a 2026-08", "productos_base": 3, "alertas": []}, prediccion=pred, abc=abc,
        metricas_metodo=pd.DataFrame(), metricas_clase=pd.DataFrame([{"metodo": "mezcla", "clase": "A", "WAPE_%": 40.0}]),
        metodo_por_clase={"A": "mezcla"}, validacion=pd.DataFrame(), reparto_presentaciones=pd.DataFrame(), segundos=1.5,
    )


def test_guardar_resultado_escribe_las_tres_tablas_y_registra_la_ejecucion(app_a):
    with app_a.app_context():
        r = srv.guardar_resultado(_resultado())
        assert r["predicciones"] == 2 and r["segmentaciones"] == 2 and r["productos_sin_catalogo"] == ["NO ESTA EN CATALOGO"]
        uno = Producto.query.filter_by(nombre_producto="AGRO UNO").one()
        p = PrediccionDemanda.query.filter_by(id_producto=uno.id_producto).one()
        assert float(p.demanda_estimada) == 30.0 and p.confiabilidad == "Alta" and p.metodo == "mezcla" and p.periodo_objetivo.month == 9
        assert SegmentacionProducto.query.count() == 2
        # el inventario cargado cubre AGRO UNO (15 unidades) y AGRO DOS (8); "NO ESTA EN CATALOGO" no tiene stock ni producto
        recs = {r.producto.nombre_producto: r for r in RecomendacionAbastecimiento.query.all()}
        assert set(recs) == {"AGRO UNO", "AGRO DOS"}
        assert recs["AGRO UNO"].nivel_urgencia == "urgente"       # se esperan 30, clase A: espera 15 y hay 15 -> se acaba antes del pedido
        assert recs["AGRO UNO"].con_inventario and recs["AGRO UNO"].stock_objetivo == 58 and recs["AGRO UNO"].cantidad_sugerida == 43
        assert recs["AGRO DOS"].nivel_urgencia == "sin_accion" and recs["AGRO DOS"].clase_abc == "C"
        assert EjecucionAnalitica.query.count() == 1


def test_una_segunda_ejecucion_reemplaza_abc_y_recomendaciones_y_agrega_historial(app_a):
    with app_a.app_context():
        srv.guardar_resultado(_resultado())
        assert SegmentacionProducto.query.count() == 2          # reemplazada, no duplicada
        assert RecomendacionAbastecimiento.query.count() == 2
        assert PrediccionDemanda.query.count() == 4             # historial: 2 por ejecución
        assert EjecucionAnalitica.query.count() == 2


def test_sin_inventario_el_guardado_entrega_el_plan_para_todos_los_productos(app_a):
    with app_a.app_context():
        db.session.query(StockPorSede).delete()
        db.session.commit()
        srv.guardar_resultado(_resultado())
        recs = RecomendacionAbastecimiento.query.all()
        assert len(recs) == 2 and {r.nivel_urgencia for r in recs} == {"plan"}
        assert all(r.stock_actual is None and not r.con_inventario for r in recs)


def test_inventario_provisional_se_marca_no_pisa_stock_real_y_se_reemplaza_con_el_real(app_a, tmp_path):
    with app_a.app_context():
        db.session.query(StockPorSede).delete()
        db.session.commit()
        hojas = _hojas_catalogo()
        simulado = srv.generar_inventario_provisional(hojas, aplicar=False)
        assert simulado["aplicado"] is False and StockPorSede.query.count() == 0
        # AGRO UNO: 120 unidades/12 meses = 10 al mes x 1.5 = 15; AGRO DOS: 24/12 x 1.5 = 3 -> mínimo 5
        r = srv.generar_inventario_provisional(hojas, aplicar=True)
        stock = {s.producto.nombre_producto: s for s in StockPorSede.query.all()}
        assert r["aplicado"] and stock["AGRO UNO"].cantidad_actual == 15 and stock["AGRO DOS"].cantidad_actual == 5
        assert {s.origen for s in stock.values()} == {"provisional"} and srv.inventario_provisional_en_uso() == 2

        # un stock REAL nunca se pisa con una estimación
        stock["AGRO UNO"].cantidad_actual, stock["AGRO UNO"].origen = 99, "real"
        db.session.commit()
        r = srv.generar_inventario_provisional(hojas, aplicar=True)
        assert r["conservados_reales"] == 1
        assert StockPorSede.query.join(Producto).filter(Producto.nombre_producto == "AGRO UNO").one().cantidad_actual == 99

        # cargar el conteo real reemplaza lo provisional
        ruta = tmp_path / "inv.xlsx"
        srv.generar_plantilla_inventario(hojas, ruta)
        libro = pd.ExcelFile(ruta)
        inv = libro.parse("Inventario")
        inv["stock_actual"] = [3, 4, 7]
        with pd.ExcelWriter(ruta) as w:
            libro.parse("Datos del conteo").to_excel(w, sheet_name="Datos del conteo", index=False)
            inv.to_excel(w, sheet_name="Inventario", index=False)
        srv.cargar_inventario(ruta, aplicar=True)
        assert srv.inventario_provisional_en_uso() == 0 and {s.origen for s in StockPorSede.query.all()} == {"real"}
        # se deja la base como estaba para las pruebas siguientes: sin inventario
        db.session.query(StockPorSede).delete()
        db.session.commit()


# ---------------------------------------------------------------------------
# Pantallas
# ---------------------------------------------------------------------------
def _admin(app):
    c = app.test_client()
    assert c.post("/login", data={"usuario": "admin_a", "contrasena": "AdminPrueba2026"}).status_code == 302
    return c


def test_las_pantallas_muestran_la_ejecucion_real_y_ocultan_la_vista_previa(app_a):
    c = _admin(app_a)
    html = c.get("/analitica/prediccion").get_data(as_text=True)
    assert "Son una guía para decidir" in html and "Calculado el" not in html and "tabla-prediccion-real" in html and "Vista previa con datos reales" not in html
    assert "AGRO UNO" in html and "Alta" in html
    html = c.get("/analitica/segmentacion").get_data(as_text=True)
    assert "AGRO UNO" in html and "Vista previa con datos reales" not in html
    html = c.get("/analitica").get_data(as_text=True)
    assert "AGRO UNO" in html and "plan de abastecimiento" in html and "¿Cómo se calculan estas recomendaciones?" in html
    assert "15 días" in html and "Prioridad alta" in html


def test_sin_ejecucion_las_pantallas_siguen_funcionando_como_antes(app_a):
    with app_a.app_context():
        db.session.query(EjecucionAnalitica).delete()
        db.session.commit()
    c = _admin(app_a)
    assert c.get("/analitica/prediccion").status_code == 200
    assert "Calculado el" not in c.get("/analitica/prediccion").get_data(as_text=True)
    assert c.get("/analitica/segmentacion").status_code == 200


# ---------------------------------------------------------------------------
# Integración con los Excel reales (se omite si no están disponibles en este equipo)
# ---------------------------------------------------------------------------
def test_el_pipeline_completo_cumple_sus_invariantes_con_los_excel_reales():
    from analitica_job.pipeline.ejecutar import ejecutar
    from analitica_job.pimentel.cargar_pimentel import CARPETA

    if not list(CARPETA.glob("ventas_*.xlsx")):
        pytest.skip("No están los Excel de Pimentel en este equipo")
    res = ejecutar()
    p, abc = res.prediccion, res.abc
    assert (p["pronostico"] >= 0).all() and p["producto"].is_unique
    assert set(abc["clase_abc"]) <= {"A", "B", "C"} and abc["base"].is_unique
    assert set(p["producto"]) == set(abc["base"])                         # 100% de productos clasificados
    assert set(p["confiabilidad"]) <= {"Alta", "Media", "Baja"}
    assert re.fullmatch(r"\d{4}-\d{2}", p["periodo_objetivo"].iloc[0])
    assert res.informe_calidad["unidades_descartadas_comprobante_mes_total"] > 0  # los comprobantes de julio 2026 se descartan


# ---------------------------------------------------------------------------
# Plan de campaña
# ---------------------------------------------------------------------------
from analitica_job.pipeline import campanas  # noqa: E402


# ventas sintéticas: P1 vende `a` unidades cada mes de 2025 y `b` cada mes de 2026 (hasta el mes `hasta`); P2 solo en campaña chica
def _ventas_campana(a=10, b=5, hasta=8):
    filas = []
    for anio, por_mes in ((2024, a), (2025, a), (2026, b)):
        for mes in range(1, 13):
            if anio == 2026 and mes > hasta:
                continue
            filas.append({"anio": anio, "mes": mes, "base": "P1", "unidades": float(por_mes)})
            if mes >= 7:
                filas.append({"anio": anio, "mes": mes, "base": "P2", "unidades": 4.0})
    return pd.DataFrame(filas)


def _plan(hasta=8):
    df = _ventas_campana(hasta=hasta)
    return campanas.plan_campana(df, pd.Series({"P1": "A", "P2": "C"}), ["P1", "P2"], 2026, hasta)


def test_campana_en_curso_corrige_lo_del_anio_pasado_con_el_ritmo_de_este_anio():
    plan, meta = _plan(hasta=8)                                   # chica 2026 con 2 meses conocidos (jul, ago)
    p1 = plan[(plan.tipo == "en_curso") & (plan.producto == "P1")].iloc[0]
    assert p1["campana_id"] == "2026-C" and p1["meses_conocidos"] == 2
    assert p1["vendido"] == 10                                    # 5 + 5
    assert p1["referencia_anio_anterior"] == 60                   # 6 meses x 10 el año pasado
    # ritmo global: vendido hasta hoy / lo del año pasado en jul-ago = (10 + 8) / (20 + 8) = 0.643 (P1 y P2 juntos)
    assert meta["en_curso"]["ritmo_global"] == pytest.approx(18 / 28)
    assert p1["esperado_resto"] == pytest.approx(40 * 18 / 28)    # sep-dic del año pasado (4 x 10) x ritmo
    assert p1["esperado_total"] == pytest.approx(p1["vendido"] + p1["esperado_resto"])


def test_la_proxima_campana_repite_la_misma_del_anio_anterior_sin_ajuste():
    plan, meta = _plan(hasta=8)
    p1 = plan[(plan.tipo == "proxima") & (plan.producto == "P1")].iloc[0]
    assert p1["campana_id"] == "2027-G" and p1["meses_conocidos"] == 0 and p1["vendido"] == 0
    assert p1["esperado_total"] == 30                              # grande 2026: 6 meses x 5
    assert meta["proxima"]["ritmo_global"] == 1.0


def test_si_la_campana_ya_termino_solo_hay_plan_de_la_siguiente():
    plan, meta = _plan(hasta=12)
    assert set(plan["tipo"]) == {"proxima"} and plan["campana_id"].iloc[0] == "2027-G" and "en_curso" not in meta


def test_el_ritmo_se_acota_para_que_un_mes_raro_no_dispare_el_pronostico():
    df = _ventas_campana(a=10, b=500, hasta=7)                      # un mes enorme frente al año pasado
    plan, meta = campanas.plan_campana(df, pd.Series({"P1": "A", "P2": "C"}), ["P1", "P2"], 2026, 7)
    assert meta["en_curso"]["ritmo_global"] == campanas.RITMO_MAX


def test_el_error_tipico_se_mide_con_la_campana_terminada_mas_reciente_y_baja_la_confianza_de_clase_c():
    plan, meta = _plan(hasta=8)
    assert meta["en_curso"]["campana_prueba"] == "2026-G"
    # 2026 vende la mitad que 2025 de forma constante: con 2 meses de avance el método lo detecta y acierta;
    # sin avance (próxima campaña) repite el año anterior y se equivoca 100% (60 previstas contra 30 reales)
    assert meta["en_curso"]["errores"]["A"] == 0
    assert meta["proxima"]["errores"]["A"] == 100.0
    assert campanas._confianza(10, 12) == "Alta" and campanas._confianza(20, 12) == "Media" and campanas._confianza(60, 12) == "Baja"
    assert campanas._confianza(10, 1) == "Media"                  # casi sin historia el año pasado: baja un nivel


def _resultado_con_plan():
    res = _resultado()
    plan, meta = campanas.plan_campana(
        pd.DataFrame([
            {"anio": a, "mes": m, "base": b, "unidades": 5.0} for a in (2025, 2026) for m in range(1, 9 if a == 2026 else 13) for b in ("AGRO UNO", "AGRO DOS")
        ]), pd.Series({"AGRO UNO": "A", "AGRO DOS": "C"}), ["AGRO UNO", "AGRO DOS"], 2026, 8)
    res.plan_campana, res.meta_campana = plan, meta
    return res


def test_el_plan_de_campana_se_guarda_calcula_lo_que_falta_cubrir_y_se_reemplaza(app_a):
    with app_a.app_context():
        db.session.query(StockPorSede).delete()
        db.session.commit()
        r = srv.guardar_resultado(_resultado_con_plan())
        assert r["plan_campana"] == 4                                # 2 productos x (en curso + próxima)
        from app.models import PlanCampana

        filas = {(f.producto.nombre_producto, f.tipo): f for f in PlanCampana.query.all()}
        assert filas[("AGRO UNO", "en_curso")].stock_actual is None and filas[("AGRO UNO", "en_curso")].por_cubrir is None
        # con stock registrado, "por cubrir" = lo que falta vender menos el stock (nunca negativo)
        uno = Producto.query.filter_by(nombre_producto="AGRO UNO").one()
        db.session.add(StockPorSede(id_producto=uno.id_producto, id_sede=1, cantidad_actual=3))
        db.session.commit()
        srv.guardar_resultado(_resultado_con_plan())
        f = [x for x in PlanCampana.query.all() if x.producto.nombre_producto == "AGRO UNO" and x.tipo == "en_curso"][0]
        assert f.stock_actual == 3 and f.por_cubrir == max(0, int(-(-float(f.esperado_resto) // 1)) - 3)
        assert PlanCampana.query.count() == 4                        # reemplazado, no duplicado
        db.session.query(StockPorSede).delete()
        db.session.commit()


def test_la_pantalla_del_plan_de_campana_explica_y_muestra_el_resumen(app_a):
    c = _admin(app_a)
    html = c.get("/analitica/campana").get_data(as_text=True)
    assert "Plan de campaña" in html and "¿Qué es el plan de campaña y cómo se calcula?" in html
    assert "Campaña chica (julio a diciembre) 2026" in html and "Ritmo frente al año pasado" in html and "AGRO UNO" in html
    assert "campaña grande 2026" in html                              # campaña con la que se midió el error
    prox = c.get("/analitica/campana?tipo=proxima").get_data(as_text=True)
    assert "Campaña grande (enero a junio) 2027" in prox and "Vendido hasta ahora" not in prox
    assert c.get("/analitica/campana?tipo=cualquiera&clase=Z").status_code == 200


def test_el_plan_de_campana_es_solo_para_el_administrador(app, trabajador_a, cliente):
    assert trabajador_a.get("/analitica/campana").status_code == 403
    assert cliente.get("/analitica/campana").status_code == 302


def test_con_inventario_de_solo_algunos_productos_los_demas_quedan_en_plan_sin_error(app_a):
    with app_a.app_context():
        db.session.query(StockPorSede).delete()
        uno = Producto.query.filter_by(nombre_producto="AGRO UNO").one()
        db.session.add(StockPorSede(id_producto=uno.id_producto, id_sede=1, cantidad_actual=40))
        db.session.commit()
        srv.guardar_resultado(_resultado())
        recs = {r.producto.nombre_producto: r for r in RecomendacionAbastecimiento.query.all()}
        assert recs["AGRO UNO"].con_inventario and recs["AGRO UNO"].stock_actual == 40
        assert recs["AGRO DOS"].nivel_urgencia == "plan" and recs["AGRO DOS"].stock_actual is None
        db.session.query(StockPorSede).delete()
        db.session.commit()
