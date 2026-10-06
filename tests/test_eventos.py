"""
Pruebas de las funciones de apoyo a la decisión: eventos del negocio (con demanda no atendida), ajuste del pipeline
y simulador de escenarios.
"""

import pandas as pd
import pytest

from analitica_job.pipeline.ejecutar import _aplicar_ajustes
from analitica_job.pipeline.recomendaciones import simular
from app.models import Bitacora, EventoNegocio


def texto(r):
    return r.get_data(as_text=True)


def formulario(datos, **cambios):
    base = {"tipo": "quiebre_stock", "fecha_inicio": "2026-03-10", "fecha_fin": "", "id_producto": datos["p1"],
            "id_sede": "", "descripcion": "Se agotó una semana", "impacto": "alto", "cantidad_no_atendida": "40"}
    base.update(cambios)
    return base


@pytest.fixture(autouse=True)
def limpiar(app):
    yield
    with app.app_context():
        EventoNegocio.query.delete()
        from app.extensions import db
        db.session.commit()


# ---------------------------------------------------------------------------
# Eventos del negocio
# ---------------------------------------------------------------------------
def test_el_administrador_registra_edita_y_elimina_un_evento(app, admin, datos):
    assert admin.post("/eventos/nuevo", data=formulario(datos)).status_code == 302
    with app.app_context():
        e = EventoNegocio.query.one()
        assert (e.tipo, e.cantidad_no_atendida, e.campana) == ("quiebre_stock", 40, "grande 2026")
        id_evento = e.id_evento
        assert Bitacora.query.filter_by(accion="evento_creado").count() >= 1
    assert "Se agotó una semana" in texto(admin.get("/eventos"))
    assert admin.post(f"/eventos/{id_evento}/editar", data=formulario(datos, cantidad_no_atendida="55")).status_code == 302
    with app.app_context():
        assert EventoNegocio.query.get(id_evento).cantidad_no_atendida == 55
    assert admin.post(f"/eventos/{id_evento}/eliminar").status_code == 302
    with app.app_context():
        assert EventoNegocio.query.count() == 0


@pytest.mark.parametrize("cambios,mensaje", [
    ({"tipo": "inventado"}, "Elige el tipo de evento"),
    ({"fecha_inicio": ""}, "fecha de inicio"),
    ({"fecha_inicio": "2026-03-10", "fecha_fin": "2026-03-01"}, "no puede ser anterior"),
    ({"descripcion": ""}, "Describe brevemente"),
    ({"descripcion": "x" * 201}, "no puede superar los 200"),
    ({"cantidad_no_atendida": "-5"}, "entero positivo"),
    ({"cantidad_no_atendida": "NaN"}, "entero positivo"),
    ({"tipo": "promocion", "cantidad_no_atendida": "5"}, "solo se anota en un quiebre"),
    ({"id_producto": "", "cantidad_no_atendida": "5"}, "elige el producto que se agotó"),
    ({"id_producto": "99999"}, "producto elegido no existe"),
    ({"impacto": "enorme"}, "impacto"),
])
def test_validacion_del_formulario_de_eventos(app, admin, datos, cambios, mensaje):
    r = admin.post("/eventos/nuevo", data=formulario(datos, **cambios))
    assert r.status_code == 200 and mensaje in texto(r)
    with app.app_context():
        assert EventoNegocio.query.count() == 0


def test_el_trabajador_no_accede_a_eventos(trabajador_a, datos):
    for ruta in ("/eventos", "/eventos/nuevo"):
        assert trabajador_a.get(ruta).status_code == 403
    assert trabajador_a.post("/eventos/nuevo", data=formulario(datos)).status_code == 403


def test_los_filtros_de_eventos_por_campana(admin, datos):
    admin.post("/eventos/nuevo", data=formulario(datos, descripcion="Evento de enero", fecha_inicio="2026-01-15"))
    admin.post("/eventos/nuevo", data=formulario(datos, descripcion="Evento de agosto", fecha_inicio="2026-08-02", tipo="promocion", cantidad_no_atendida=""))
    grande, chica = texto(admin.get("/eventos?campana=grande")), texto(admin.get("/eventos?campana=chica"))
    assert "Evento de enero" in grande and "Evento de agosto" not in grande
    assert "Evento de agosto" in chica and "Evento de enero" not in chica
    assert "Evento de agosto" not in texto(admin.get("/eventos?anio=2025"))


# ---------------------------------------------------------------------------
# La demanda no atendida entra al panel de la analítica
# ---------------------------------------------------------------------------
def test_la_demanda_no_atendida_se_suma_solo_al_producto_y_mes_del_quiebre():
    periodos = pd.period_range("2026-01", "2026-03", freq="M")
    panel = pd.DataFrame([{"base": b, "periodo": p, "unidades": 10.0} for b in ("A", "B") for p in periodos])
    ajustes = pd.DataFrame([{"base": "A", "anio": 2026, "mes": 2, "unidades": 25.0}, {"base": "A", "anio": 2026, "mes": 2, "unidades": 5.0},
                            {"base": "NO EXISTE", "anio": 2026, "mes": 2, "unidades": 99.0}])
    r = _aplicar_ajustes(panel, ajustes)
    assert r.loc[(r.base == "A") & (r.periodo == periodos[1]), "unidades"].iat[0] == 40.0
    assert r[(r.base != "A") | (r.periodo != periodos[1])]["unidades"].eq(10.0).all()
    assert panel["unidades"].eq(10.0).all()  # no modifica el original
    assert _aplicar_ajustes(panel, None) is panel and _aplicar_ajustes(panel, ajustes.iloc[0:0]) is panel


# ---------------------------------------------------------------------------
# Simulador de escenarios
# ---------------------------------------------------------------------------
def test_simulador_sin_cambios_reproduce_la_recomendacion_normal():
    s = simular(30, 10, "A", 0, 15, None)
    assert (s["objetivo"], s["punto_reposicion"], s["nivel"], s["cantidad"]) == (55, 25, "plan", 55)


def test_simulador_mas_demanda_y_mas_dias_aumenta_lo_que_hay_que_tener():
    base, esc = simular(30, 10, "A", 0, 15, 40), simular(30, 10, "A", 50, 30, 40)
    assert esc["objetivo"] > base["objetivo"] and esc["dias_alcanza"] < base["dias_alcanza"]
    assert (base["nivel"], esc["nivel"]) == ("sin_accion", "urgente")
    assert esc["cantidad"] == esc["objetivo"] - 40


def test_simulador_el_margen_respeta_el_tope_por_clase():
    s = simular(10, 1000, "C", 0, 15, None)
    assert s["margen"] == 4  # tope clase C: 35 % de la demanda -> ceil(3.5)


def test_simulador_demanda_nula_no_pide_nada():
    s = simular(30, 10, "B", -90, 15, 0)
    assert s["demanda"] == pytest.approx(3.0)
    s = simular(0, 0, "B", 0, 15, 5)
    assert (s["nivel"], s["cantidad"]) == ("sin_accion", 0)


def test_la_pantalla_del_simulador_exige_rol_administrador(admin, trabajador_a):
    assert admin.get("/analitica/simulador").status_code == 200
    assert trabajador_a.get("/analitica/simulador").status_code == 403
    assert admin.get("/analitica/simulador?id_producto=abc&cambio=NaN&dias=-3").status_code == 200


# ---------------------------------------------------------------------------
# Eventos de ejemplo: se ven, pero no afectan los cálculos
# ---------------------------------------------------------------------------
def test_los_eventos_de_ejemplo_se_ven_pero_no_suman_demanda(app, admin, datos):
    from app.servicios_analitica import ajustes_por_eventos
    admin.post("/eventos/nuevo", data=formulario(datos, descripcion="Quiebre real"))
    admin.post("/eventos/nuevo", data=formulario(datos, descripcion="Quiebre de ejemplo", cantidad_no_atendida="500", es_ejemplo="on"))
    html = texto(admin.get("/eventos"))
    assert "Quiebre de ejemplo" in html and "Ejemplo" in html
    with app.app_context():
        ajustes = ajustes_por_eventos()
    assert list(ajustes["unidades"]) == [40.0]  # solo el evento real; el de ejemplo (500) no cuenta


def test_los_comandos_de_eventos_de_ejemplo_son_repetibles(app):
    ejecutor = app.test_cli_runner()
    with app.app_context():
        from app.extensions import db
        from app.models import PrediccionDemanda, Producto, RecomendacionAbastecimiento, Sede
        p = Producto.query.first()
        sede = Sede.query.first()
        db.session.add(RecomendacionAbastecimiento(id_producto=p.id_producto, id_sede=sede.id_sede, stock_actual=None, demanda_prevista=10,
                                                   cantidad_sugerida=10, nivel_urgencia="plan", motivo="x"))
        db.session.commit()
    try:
        for _ in range(2):
            assert "eventos de ejemplo cargados" in ejecutor.invoke(args=["cargar-eventos-ejemplo"]).output
        with app.app_context():
            assert EventoNegocio.query.filter_by(es_ejemplo=True).count() == 6
        assert "eliminados" in ejecutor.invoke(args=["quitar-eventos-ejemplo"]).output
        with app.app_context():
            assert EventoNegocio.query.count() == 0
    finally:
        with app.app_context():
            from app.extensions import db
            from app.models import RecomendacionAbastecimiento
            RecomendacionAbastecimiento.query.delete()
            db.session.commit()


# ---------------------------------------------------------------------------
# Ventas de la plataforma que amplían el historial de los Excel
# ---------------------------------------------------------------------------
def _historial():
    return pd.DataFrame([{"anio": 2026, "mes": 8, "producto_original": "A", "producto_norm": "A", "base": "A", "presentacion": "x", "categoria": "FOLIARES",
                          "unidades": 5.0, "precio": 2.0, "soles": 10.0, "bloque": 2, "hoja": "h", "por_revisar": False}])


def test_se_incorporan_solo_meses_consecutivos_posteriores_a_los_excel():
    from analitica_job.pipeline.ejecutar import incorporar_ventas_sistema
    ventas = pd.DataFrame([
        {"base": "A", "anio": 2026, "mes": 9, "categoria": "FOLIARES", "unidades": 7.0, "soles": 21.0},
        {"base": "A", "anio": 2026, "mes": 10, "categoria": "FOLIARES", "unidades": 3.0, "soles": 9.0},
        {"base": "A", "anio": 2026, "mes": 12, "categoria": "FOLIARES", "unidades": 99.0, "soles": 99.0},   # hay un hueco en noviembre
        {"base": "A", "anio": 2026, "mes": 7, "categoria": "FOLIARES", "unidades": 50.0, "soles": 50.0},    # ya cubierto por los Excel
    ])
    df, fin, n = incorporar_ventas_sistema(_historial(), ventas, (2026, 8))
    assert (fin, n) == ((2026, 10), 2)
    nuevas = df[df["hoja"] == "sistema"]
    assert sorted(nuevas["mes"]) == [9, 10] and nuevas["unidades"].sum() == 10.0
    assert nuevas.loc[nuevas["mes"] == 9, "precio"].iat[0] == 3.0


def test_sin_ventas_nuevas_el_historial_no_cambia():
    from analitica_job.pipeline.ejecutar import incorporar_ventas_sistema
    base = _historial()
    for ventas in (None, pd.DataFrame(columns=["base", "anio", "mes", "categoria", "unidades", "soles"])):
        df, fin, n = incorporar_ventas_sistema(base, ventas, (2026, 8))
        assert (fin, n) == ((2026, 8), 0) and df is base
    salto = pd.DataFrame([{"base": "A", "anio": 2026, "mes": 11, "categoria": "FOLIARES", "unidades": 1.0, "soles": 1.0}])
    assert incorporar_ventas_sistema(base, salto, (2026, 8))[2] == 0


def test_la_consulta_toma_ventas_del_sistema_y_no_las_importadas_del_historico(app, datos, monkeypatch):
    from datetime import date, datetime

    import app.servicios_analitica as sa
    from app.extensions import db
    from app.models import DetalleVenta, Usuario, Venta

    class FechaFija(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 6)

    monkeypatch.setattr(sa, "date", FechaFija)
    with app.app_context():
        uid = Usuario.query.first().id_usuario
        creadas = []
        for numero, fecha in (("OP-FEED-1", datetime(2026, 9, 15)), ("H202609J", datetime(2026, 9, 30)), ("OP-FEED-2", datetime(2026, 10, 2))):
            v = Venta(numero_operacion=numero, fecha_hora=fecha, id_cliente=datos["cliente"], id_usuario=uid, id_sede=datos["sede"], total=30)
            db.session.add(v)
            db.session.flush()
            db.session.add(DetalleVenta(id_venta=v.id_venta, id_producto=datos["p1"], cantidad=3, precio_unitario=10, subtotal=30))
            creadas.append(v)
        db.session.commit()
        try:
            r = sa.ventas_sistema_mensual()
            assert len(r) == 1 and (int(r["anio"].iat[0]), int(r["mes"].iat[0])) == (2026, 9)
            assert r["unidades"].iat[0] == 3.0 and r["soles"].iat[0] == 30.0  # no cuenta la importada (H...) ni octubre (mes en curso)
        finally:
            for v in creadas:
                DetalleVenta.query.filter_by(id_venta=v.id_venta).delete()
                db.session.delete(v)
            db.session.commit()


def test_el_simulador_acepta_el_cambio_en_porcentaje_unidades_o_demanda_total(app, admin):
    from app.extensions import db
    from app.models import Producto, RecomendacionAbastecimiento, Sede
    with app.app_context():
        p, sede = Producto.query.first(), Sede.query.first()
        db.session.add(RecomendacionAbastecimiento(id_producto=p.id_producto, id_sede=sede.id_sede, stock_actual=None, demanda_prevista=40, stock_seguridad=10,
                                                   cantidad_sugerida=60, nivel_urgencia="plan", motivo="x", clase_abc="A"))
        db.session.commit()
        id_producto = p.id_producto
    try:
        def demanda(modo, valor):
            html = texto(admin.get(f"/analitica/simulador?id_producto={id_producto}&modo={modo}&cambio={valor}"))
            fila = html[html.index("Demanda del mes"):]
            return int(fila.split("<strong>")[1].split("</strong>")[0])
        assert demanda("pct", 25) == 50       # 40 + 25 %
        assert demanda("unid", 10) == 50      # 40 + 10 unidades
        assert demanda("unid", -15) == 25     # 40 - 15 unidades
        assert demanda("total", 70) == 70     # demanda total del mes
        assert demanda("total", -5) == 0      # nunca negativa
    finally:
        with app.app_context():
            RecomendacionAbastecimiento.query.delete()
            db.session.commit()
