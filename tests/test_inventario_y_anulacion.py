"""
Pruebas del inventario (ingresos, conteos, kárdex) y de la anulación de ventas, y del control de precios.
"""

import pytest

from app.extensions import db
from app.models import Bitacora, Credito, MovimientoInventario, Sede, StockPorSede, Venta
from conftest import crear_producto


def texto(respuesta):
    return respuesta.get_data(as_text=True)


def stock_y_origen(app, id_producto):
    with app.app_context():
        fila = StockPorSede.query.filter_by(id_producto=id_producto).first()
        return fila.cantidad_actual, fila.origen


def movimientos(app, id_producto):
    with app.app_context():
        filas = MovimientoInventario.query.filter_by(id_producto=id_producto).order_by(MovimientoInventario.id_movimiento).all()
        return [(m.tipo, m.cantidad, m.stock_resultante) for m in filas]


def id_sede(app, nombre="Chiclayo"):
    with app.app_context():
        return Sede.query.filter_by(nombre_sede=nombre).first().id_sede


def vender(c, datos, id_producto, cantidad=1, precio=None, tipo="contado"):
    # el navegador siempre envía el campo de precio; vacío significa "el precio de lista"
    form = {"id_cliente": datos["cliente"], "tipo_venta": tipo, "id_producto[]": [id_producto], "cantidad[]": [cantidad],
            "precio_unitario[]": ["" if precio is None else precio]}
    return c.post("/ventas/nueva", data=form)


def id_venta_de(respuesta):
    return int(respuesta.headers["Location"].rstrip("/").split("/")[-1])


def acciones(app, accion):
    with app.app_context():
        return [b.detalle for b in Bitacora.query.filter_by(accion=accion).all()]


# ---------------------------------------------------------------------------
# Inventario: ingresos y conteos
# ---------------------------------------------------------------------------
def test_el_administrador_ve_el_inventario_y_el_detalle_de_un_producto(app, admin):
    id_producto = crear_producto(app, "INV-0", stock=7)
    assert admin.get("/inventario").status_code == 200
    assert "Producto INV-0" in texto(admin.get("/inventario?q=INV-0"))
    detalle = texto(admin.get(f"/inventario/{id_producto}"))
    assert "Registrar un ingreso" in detalle and "Registrar un conteo físico" in detalle
    assert admin.get("/inventario?alerta=1&id_sede=999&pagina=abc").status_code == 200


def test_el_trabajador_no_puede_entrar_al_inventario_ni_cambiar_stock(app, trabajador_a):
    id_producto = crear_producto(app, "INV-1", stock=10)
    assert trabajador_a.get("/inventario").status_code == 403
    assert trabajador_a.get(f"/inventario/{id_producto}").status_code == 403
    r = trabajador_a.post(f"/inventario/{id_producto}/ingreso", data={"id_sede": id_sede(app), "cantidad": "50", "motivo": "compra"})
    assert r.status_code == 403
    assert trabajador_a.post(f"/inventario/{id_producto}/conteo", data={"id_sede": id_sede(app), "conteo": "0", "detalle": "x" * 5}).status_code == 403
    assert stock_y_origen(app, id_producto)[0] == 10


def test_un_ingreso_suma_al_stock_y_deja_el_movimiento_y_la_bitacora(app, admin):
    id_producto = crear_producto(app, "INV-2", stock=10)
    r = admin.post(f"/inventario/{id_producto}/ingreso", data={
        "id_sede": id_sede(app), "cantidad": "25", "motivo": "compra", "detalle": "Factura F001-77",
    }, follow_redirects=True)
    assert "Ingreso registrado" in texto(r)
    assert stock_y_origen(app, id_producto)[0] == 35
    assert movimientos(app, id_producto) == [("ingreso", 25, 35)]
    assert any("INV-2" in d and "+25" in d for d in acciones(app, "inventario_ingreso"))
    assert "Factura F001-77" in texto(admin.get(f"/inventario/{id_producto}"))


@pytest.mark.parametrize("cambios,mensaje", [
    ({"cantidad": "0"}, "entre 1 y 1 000 000"),
    ({"cantidad": "-5"}, "entre 1 y 1 000 000"),
    ({"cantidad": "abc"}, "entre 1 y 1 000 000"),
    ({"cantidad": "99999999"}, "entre 1 y 1 000 000"),
    ({"id_sede": "9999"}, "sede válida"),
    ({"motivo": "robo"}, "motivo del ingreso"),
    ({"detalle": "x" * 150}, "no puede superar"),
])
def test_los_ingresos_invalidos_se_rechazan_sin_tocar_el_stock(app, admin, cambios, mensaje):
    id_producto = crear_producto(app, f"INV-X{abs(hash(mensaje + str(cambios))) % 100000}", stock=10)
    form = {"id_sede": id_sede(app), "cantidad": "5", "motivo": "compra", "detalle": ""}
    form.update(cambios)
    r = admin.post(f"/inventario/{id_producto}/ingreso", data=form, follow_redirects=True)
    assert mensaje in texto(r)
    assert stock_y_origen(app, id_producto)[0] == 10
    assert movimientos(app, id_producto) == []


def test_un_conteo_fija_el_stock_registra_la_diferencia_y_lo_marca_como_real(app, admin):
    id_producto = crear_producto(app, "INV-3", stock=10)
    with app.app_context():
        StockPorSede.query.filter_by(id_producto=id_producto).first().origen = "provisional"
        db.session.commit()

    r = admin.post(f"/inventario/{id_producto}/conteo", data={"id_sede": id_sede(app), "conteo": "4", "detalle": "Conteo del almacén"},
                   follow_redirects=True)
    assert "quedó en 4" in texto(r) and "-6" in texto(r)
    assert stock_y_origen(app, id_producto) == (4, "real")  # dejó de ser un estimado
    assert movimientos(app, id_producto) == [("ajuste", -6, 4)]

    # un conteo sin diferencia también se registra (queda constancia de que se contó)
    admin.post(f"/inventario/{id_producto}/conteo", data={"id_sede": id_sede(app), "conteo": "4", "detalle": "Reconteo"})
    assert movimientos(app, id_producto)[-1] == ("ajuste", 0, 4)


def test_un_conteo_exige_una_nota_y_un_numero_valido(app, admin):
    id_producto = crear_producto(app, "INV-4", stock=10)
    for form, mensaje in (
        ({"conteo": "5", "detalle": ""}, "breve nota"),
        ({"conteo": "-1", "detalle": "nota"}, "entre 0 y 1 000 000"),
        ({"conteo": "x", "detalle": "nota"}, "entre 0 y 1 000 000"),
    ):
        r = admin.post(f"/inventario/{id_producto}/conteo", data={"id_sede": id_sede(app), **form}, follow_redirects=True)
        assert mensaje in texto(r)
    assert stock_y_origen(app, id_producto)[0] == 10


def test_el_mismo_ingreso_enviado_dos_veces_se_suma_una_sola_vez(app, admin):
    id_producto = crear_producto(app, "INV-5", stock=10)
    form = {"id_sede": id_sede(app), "cantidad": "5", "motivo": "produccion", "token_envio": "ingreso-doble-1"}
    admin.post(f"/inventario/{id_producto}/ingreso", data=form)
    repetido = admin.post(f"/inventario/{id_producto}/ingreso", data=form, follow_redirects=True)
    assert "ya estaba registrado" in texto(repetido)
    assert stock_y_origen(app, id_producto)[0] == 15


def test_ingresar_en_una_sede_sin_registro_crea_el_stock_de_esa_sede(app, admin):
    id_producto = crear_producto(app, "INV-6", stock=10)
    pimentel = id_sede(app, "Pimentel")
    admin.post(f"/inventario/{id_producto}/ingreso", data={"id_sede": pimentel, "cantidad": "8", "motivo": "compra"})
    with app.app_context():
        assert StockPorSede.query.filter_by(id_producto=id_producto, id_sede=pimentel).first().cantidad_actual == 8


# ---------------------------------------------------------------------------
# Kárdex: las ventas también dejan movimiento
# ---------------------------------------------------------------------------
def test_cada_venta_deja_su_movimiento_en_el_karddex(app, datos, trabajador_a):
    id_producto = crear_producto(app, "KAR-1", precio=10, stock=10)
    r = vender(trabajador_a, datos, id_producto, cantidad=3)
    assert r.status_code == 302
    assert movimientos(app, id_producto) == [("venta", -3, 7)]


# ---------------------------------------------------------------------------
# Anulación de ventas
# ---------------------------------------------------------------------------
def test_anular_una_venta_devuelve_el_stock_la_marca_y_la_saca_de_los_totales(app, datos, admin, trabajador_a):
    id_producto = crear_producto(app, "ANU-1", precio=10, stock=10)
    r = vender(trabajador_a, datos, id_producto, cantidad=4, precio="10")
    id_venta = id_venta_de(r)
    assert stock_y_origen(app, id_producto)[0] == 6

    anulada = admin.post(f"/ventas/{id_venta}/anular", data={"motivo": "Se digitó el cliente equivocado"}, follow_redirects=True)
    assert "anulada" in texto(anulada).lower() and "volvió al almacén" in texto(anulada)
    assert stock_y_origen(app, id_producto)[0] == 10
    assert movimientos(app, id_producto) == [("venta", -4, 6), ("anulacion", 4, 10)]
    with app.app_context():
        venta = db.session.get(Venta, id_venta)
        assert venta.anulada and venta.motivo_anulacion == "Se digitó el cliente equivocado"
        assert venta.id_usuario_anulacion is not None and venta.fecha_anulacion is not None
    assert any("S/ 40" in d and "equivocado" in d for d in acciones(app, "venta_anulada"))
    # el detalle muestra el aviso y ya no ofrece anular otra vez
    detalle = texto(admin.get(f"/ventas/{id_venta}"))
    assert "Venta anulada" in detalle and "Anular esta venta" not in detalle


def test_la_venta_anulada_no_cuenta_en_reportes_ni_en_el_dashboard(app, datos, admin, trabajador_a):
    id_producto = crear_producto(app, "ANU-2", precio=10, stock=10)
    r = vender(trabajador_a, datos, id_producto, cantidad=2, precio="10")
    id_venta = id_venta_de(r)

    def ventas_del_reporte():
        html = texto(admin.get("/reportes?tipo=productos&desde=2020-01-01&hasta=2099-12-31"))
        return "Producto ANU-2" in html

    assert ventas_del_reporte()
    admin.post(f"/ventas/{id_venta}/anular", data={"motivo": "Venta de prueba duplicada"})
    assert not ventas_del_reporte()
    csv = texto(admin.get("/reportes/exportar?tipo=ventas&desde=2020-01-01&hasta=2099-12-31"))
    with app.app_context():
        numero = db.session.get(Venta, id_venta).numero_operacion
    assert numero not in csv


def test_anular_una_venta_al_credito_anula_tambien_su_credito(app, datos, admin, trabajador_a):
    id_producto = crear_producto(app, "ANU-3", precio=10, stock=10)
    r = vender(trabajador_a, datos, id_producto, cantidad=5, precio="10", tipo="credito")
    id_venta = id_venta_de(r)
    admin.post(f"/ventas/{id_venta}/anular", data={"motivo": "Cliente canceló el pedido"})
    with app.app_context():
        credito = Credito.query.filter_by(id_venta=id_venta).first()
        assert credito.estado == "anulado" and credito.estado_visual == "anulado"
        id_credito = credito.id_credito
    # un crédito anulado ya no acepta pagos
    r = trabajador_a.post(f"/ventas/creditos/{id_credito}/pagar", data={"monto_recuperado": "10"}, follow_redirects=True)
    assert "venta anulada" in texto(r)
    assert "Anulado" in texto(admin.get("/ventas/creditos"))


def test_no_se_puede_anular_una_venta_al_credito_que_ya_tiene_pagos(app, datos, admin, trabajador_a):
    id_producto = crear_producto(app, "ANU-4", precio=10, stock=10)
    r = vender(trabajador_a, datos, id_producto, cantidad=5, precio="10", tipo="credito")
    id_venta = id_venta_de(r)
    with app.app_context():
        id_credito = Credito.query.filter_by(id_venta=id_venta).first().id_credito
    trabajador_a.post(f"/ventas/creditos/{id_credito}/pagar", data={"monto_recuperado": "10"})
    r = admin.post(f"/ventas/{id_venta}/anular", data={"motivo": "Intento de anular con pagos"}, follow_redirects=True)
    assert "ya tiene pagos" in texto(r)
    with app.app_context():
        assert db.session.get(Venta, id_venta).anulada is False
    assert stock_y_origen(app, id_producto)[0] == 5


def test_solo_el_administrador_puede_anular_y_se_exige_un_motivo(app, datos, admin, trabajador_a):
    id_producto = crear_producto(app, "ANU-5", precio=10, stock=10)
    id_venta = id_venta_de(vender(trabajador_a, datos, id_producto, cantidad=1, precio="10"))
    assert trabajador_a.post(f"/ventas/{id_venta}/anular", data={"motivo": "Quiero anularla yo"}).status_code == 403
    for motivo in ("", "no", "x" * 200):
        r = admin.post(f"/ventas/{id_venta}/anular", data={"motivo": motivo}, follow_redirects=True)
        assert "motivo de la anulación" in texto(r)
    with app.app_context():
        assert db.session.get(Venta, id_venta).anulada is False
    assert stock_y_origen(app, id_producto)[0] == 9


def test_anular_dos_veces_no_devuelve_el_stock_dos_veces(app, datos, admin, trabajador_a):
    id_producto = crear_producto(app, "ANU-6", precio=10, stock=10)
    id_venta = id_venta_de(vender(trabajador_a, datos, id_producto, cantidad=2, precio="10"))
    admin.post(f"/ventas/{id_venta}/anular", data={"motivo": "Primera anulación"})
    r = admin.post(f"/ventas/{id_venta}/anular", data={"motivo": "Segunda anulación"}, follow_redirects=True)
    assert "ya estaba anulada" in texto(r)
    assert stock_y_origen(app, id_producto)[0] == 10


def test_las_ventas_historicas_importadas_no_se_pueden_anular(app, datos, admin, trabajador_a):
    id_producto = crear_producto(app, "ANU-7", precio=10, stock=10)
    id_venta = id_venta_de(vender(trabajador_a, datos, id_producto, cantidad=1, precio="10"))
    with app.app_context():
        db.session.get(Venta, id_venta).numero_operacion = f"H{id_venta:05d}"
        db.session.commit()
    r = admin.post(f"/ventas/{id_venta}/anular", data={"motivo": "Intento sobre una histórica"}, follow_redirects=True)
    assert "históricas importadas" in texto(r)
    assert "Anular esta venta" not in texto(admin.get(f"/ventas/{id_venta}"))


def test_la_analitica_no_cuenta_las_ventas_anuladas(app, datos):
    from datetime import date, datetime

    from analitica_job.pipeline import limpieza
    from app.models import DetalleVenta, Usuario
    from app.servicios_analitica import ventas_sistema_mensual

    # la analítica aprende de los meses COMPLETOS posteriores a los Excel: se usa el primero de ellos
    anio, mes = limpieza.ULTIMO_ANIO_REAL + limpieza.ULTIMO_MES_REAL // 12, limpieza.ULTIMO_MES_REAL % 12 + 1
    if date(anio, mes, 1) >= date.today().replace(day=1):
        pytest.skip("todavía no hay un mes completo posterior a los Excel")
    id_producto = crear_producto(app, "ANA-1", precio=10, stock=10)
    with app.app_context():
        usuario = Usuario.query.filter_by(usuario_login="trab_a").first()
        for numero, anulada in (("TV-ANA-1", False), ("TV-ANA-2", True)):
            venta = Venta(numero_operacion=numero, fecha_hora=datetime(anio, mes, 15, 10, 0), id_cliente=datos["cliente"],
                          id_usuario=usuario.id_usuario, id_sede=usuario.id_sede, tipo_venta="contado", estado_pago="pagado",
                          total=30, anulada=anulada)
            db.session.add(venta)
            db.session.flush()
            db.session.add(DetalleVenta(id_venta=venta.id_venta, id_producto=id_producto, cantidad=3, precio_unitario=10, subtotal=30))
        db.session.commit()
        filas = ventas_sistema_mensual()
        propias = filas[filas["base"] == "Producto ANA-1"]
        assert propias["unidades"].sum() == 3 and propias["soles"].sum() == 30  # la anulada no suma


def test_la_venta_anulada_se_ve_marcada_en_los_listados(app, datos, admin, trabajador_a):
    id_producto = crear_producto(app, "ANU-8", precio=10, stock=10)
    id_venta = id_venta_de(vender(trabajador_a, datos, id_producto, cantidad=1, precio="10"))
    admin.post(f"/ventas/{id_venta}/anular", data={"motivo": "Registrada por error"})
    assert "Anulada" in texto(admin.get("/ventas"))
    assert "Anulada" in texto(trabajador_a.get("/ventas/mis-ventas"))


def test_la_ayuda_explica_las_funciones_nuevas_segun_el_rol(admin, trabajador_a):
    ayuda_admin, ayuda_trabajador = texto(admin.get("/ayuda")), texto(trabajador_a.get("/ayuda"))
    assert 'id="inventario"' in ayuda_admin and "Kárdex" in ayuda_admin and "Verificar integridad" in ayuda_admin
    assert 'id="inventario"' not in ayuda_trabajador          # el Trabajador no ve la sección de inventario
    assert "anularla" in ayuda_trabajador and "Cambiar contraseña" in ayuda_trabajador


# ---------------------------------------------------------------------------
# Control de precios
# ---------------------------------------------------------------------------
def test_el_trabajador_no_puede_rebajar_mas_del_tope_sin_autorizacion(app, datos, trabajador_a, monkeypatch):
    monkeypatch.setitem(app.config, "DESCUENTO_MAX_TRABAJADOR_PCT", 10.0)
    id_producto = crear_producto(app, "PRE-1", precio=100, stock=10)
    r = vender(trabajador_a, datos, id_producto, cantidad=1, precio="80")  # 20 % menos
    assert r.status_code == 200 and "20 % por debajo" in texto(r) and "Pide autorización" in texto(r)
    assert stock_y_origen(app, id_producto)[0] == 10


def test_una_rebaja_dentro_del_tope_se_acepta_y_queda_en_la_bitacora(app, datos, trabajador_a, monkeypatch):
    monkeypatch.setitem(app.config, "DESCUENTO_MAX_TRABAJADOR_PCT", 10.0)
    id_producto = crear_producto(app, "PRE-2", precio=100, stock=10)
    r = vender(trabajador_a, datos, id_producto, cantidad=1, precio="95")  # 5 % menos
    assert r.status_code == 302
    detalles = acciones(app, "venta_precio_rebajado")
    assert any("Producto PRE-2" in d and "S/ 95" in d and "5 % menos" in d for d in detalles)


def test_el_administrador_puede_rebajar_mas_pero_queda_registrado(app, datos, admin, monkeypatch):
    monkeypatch.setitem(app.config, "DESCUENTO_MAX_TRABAJADOR_PCT", 10.0)
    id_producto = crear_producto(app, "PRE-3", precio=100, stock=10)
    r = vender(admin, datos, id_producto, cantidad=1, precio="60")  # 40 % menos
    assert r.status_code == 302
    assert any("Producto PRE-3" in d and "40 % menos" in d for d in acciones(app, "venta_precio_rebajado"))


def test_vender_al_precio_de_lista_o_mas_caro_no_deja_aviso(app, datos, trabajador_a, monkeypatch):
    monkeypatch.setitem(app.config, "DESCUENTO_MAX_TRABAJADOR_PCT", 10.0)
    id_producto = crear_producto(app, "PRE-4", precio=100, stock=10)
    antes = len(acciones(app, "venta_precio_rebajado"))
    assert vender(trabajador_a, datos, id_producto, cantidad=1, precio="100").status_code == 302
    assert vender(trabajador_a, datos, id_producto, cantidad=1, precio="120").status_code == 302
    assert len(acciones(app, "venta_precio_rebajado")) == antes
