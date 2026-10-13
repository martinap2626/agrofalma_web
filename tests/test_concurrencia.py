"""
Pruebas de exactitud cuando varias personas usan el sistema a la vez (fiabilidad): ventas simultáneas del mismo producto,
pagos simultáneos de un mismo crédito, formularios enviados dos veces y bitácora escrita por varios procesos.

Cada prueba lanza varios hilos que usan sesiones distintas del sistema real (con su base de datos de pruebas) y comprueba
que el resultado final es el correcto: ni stock negativo, ni saldo mal calculado, ni registros duplicados.
"""

import threading
from decimal import Decimal

from app.extensions import db
from app.models import Credito, DetalleVenta, Recuperacion, StockPorSede, Venta
from app.seguridad import registrar_accion, verificar_bitacora
from conftest import CLAVE_TRABAJADOR, crear_producto, sesion_de


# ejecuta `accion(cliente, i)` en `n` hilos a la vez, cada uno con su propia sesión iniciada; devuelve las respuestas
def en_paralelo(app, n, accion, login="trab_a"):
    clientes = [sesion_de(app, login, CLAVE_TRABAJADOR) for _ in range(n)]
    arranque = threading.Barrier(n)
    resultados = [None] * n
    errores = []

    def correr(i):
        try:
            arranque.wait(timeout=15)
            resultados[i] = accion(clientes[i], i)
        except Exception as error:  # noqa: BLE001 - se re-lanza en el hilo principal
            errores.append(error)

    hilos = [threading.Thread(target=correr, args=(i,)) for i in range(n)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=60)
    assert not errores, errores
    return resultados


def stock_de(app, id_producto):
    with app.app_context():
        return db.session.query(StockPorSede.cantidad_actual).filter_by(id_producto=id_producto).scalar()


def formulario_venta(datos, id_producto, cantidad=1, precio="10", tipo="contado", token=None):
    datos_form = {
        "id_cliente": datos["cliente"], "tipo_venta": tipo,
        "id_producto[]": [id_producto], "cantidad[]": [cantidad], "precio_unitario[]": [precio],
    }
    if token:
        datos_form["token_envio"] = token
    return datos_form


# ---------------------------------------------------------------------------
# Ventas simultáneas
# ---------------------------------------------------------------------------
def test_ventas_simultaneas_nunca_venden_mas_de_lo_que_hay(app, datos):
    id_producto = crear_producto(app, "CONC-1", precio=10, stock=5)
    respuestas = en_paralelo(app, 12, lambda c, i: c.post("/ventas/nueva", data=formulario_venta(datos, id_producto)))

    # solo hay 5 unidades: exactamente 5 ventas se registran y las otras 7 se rechazan por falta de stock
    exitosas = [r for r in respuestas if r.status_code == 302]
    rechazadas = [r for r in respuestas if r.status_code == 200 and "No hay stock suficiente" in r.get_data(as_text=True)]
    assert len(exitosas) == 5, [r.status_code for r in respuestas]
    assert len(rechazadas) == 7
    assert stock_de(app, id_producto) == 0
    with app.app_context():
        assert DetalleVenta.query.filter_by(id_producto=id_producto).count() == 5


def test_ventas_simultaneas_de_varias_unidades_dejan_el_stock_exacto(app, datos):
    id_producto = crear_producto(app, "CONC-2", precio=10, stock=20)
    # 10 hilos piden 3 unidades cada uno (30 en total) con solo 20 disponibles: caben 6 ventas (18 unidades)
    respuestas = en_paralelo(app, 10, lambda c, i: c.post("/ventas/nueva", data=formulario_venta(datos, id_producto, cantidad=3)))
    exitosas = [r for r in respuestas if r.status_code == 302]
    assert len(exitosas) == 6
    assert stock_de(app, id_producto) == 2


# ---------------------------------------------------------------------------
# Pagos simultáneos de un mismo crédito
# ---------------------------------------------------------------------------
def test_pagos_simultaneos_no_dejan_el_saldo_mal_calculado(app, datos, trabajador_a):
    id_producto = crear_producto(app, "CONC-3", precio=10, stock=100)
    r = trabajador_a.post("/ventas/nueva", data=formulario_venta(datos, id_producto, cantidad=10, precio="10", tipo="credito"))
    assert r.status_code == 302
    id_venta = int(r.headers["Location"].rstrip("/").split("/")[-1])
    with app.app_context():
        id_credito = Credito.query.filter_by(id_venta=id_venta).first().id_credito  # saldo S/ 100

    # 10 pagos de S/ 30 a la vez: solo caben 3 (S/ 90); los demás superarían el saldo y se rechazan
    en_paralelo(app, 10, lambda c, i: c.post(f"/ventas/creditos/{id_credito}/pagar", data={"monto_recuperado": "30"}))
    with app.app_context():
        credito = db.session.get(Credito, id_credito)
        pagos = Recuperacion.query.filter_by(id_credito=id_credito).all()
        assert len(pagos) == 3
        assert credito.saldo_pendiente == Decimal("10")
        assert credito.estado == "pendiente"
        # el saldo siempre coincide con lo que dicen los pagos registrados
        assert credito.monto_credito - sum(p.monto_recuperado for p in pagos) == credito.saldo_pendiente


# ---------------------------------------------------------------------------
# Formularios enviados dos veces (doble clic, reintento del navegador)
# ---------------------------------------------------------------------------
def test_el_mismo_formulario_de_venta_enviado_dos_veces_registra_una_sola_venta(app, datos, trabajador_a):
    id_producto = crear_producto(app, "DOBLE-1", precio=10, stock=10)
    datos_form = formulario_venta(datos, id_producto, cantidad=2, token="token-de-prueba-doble-venta")

    primera = trabajador_a.post("/ventas/nueva", data=datos_form)
    segunda = trabajador_a.post("/ventas/nueva", data=datos_form, follow_redirects=True)

    assert primera.status_code == 302
    assert "ya estaba registrada" in segunda.get_data(as_text=True)
    with app.app_context():
        assert Venta.query.filter_by(token_envio="token-de-prueba-doble-venta").count() == 1
    assert stock_de(app, id_producto) == 8  # se descontó una sola vez


def test_doble_clic_simultaneo_en_la_venta_registra_una_sola(app, datos):
    id_producto = crear_producto(app, "DOBLE-2", precio=10, stock=10)
    datos_form = formulario_venta(datos, id_producto, cantidad=1, token="token-doble-clic-simultaneo")
    en_paralelo(app, 2, lambda c, i: c.post("/ventas/nueva", data=datos_form))
    with app.app_context():
        assert Venta.query.filter_by(token_envio="token-doble-clic-simultaneo").count() == 1
    assert stock_de(app, id_producto) == 9


def test_el_mismo_pago_enviado_dos_veces_se_registra_una_sola_vez(app, datos, trabajador_a):
    id_producto = crear_producto(app, "DOBLE-3", precio=10, stock=10)
    r = trabajador_a.post("/ventas/nueva", data=formulario_venta(datos, id_producto, cantidad=5, tipo="credito"))
    id_venta = int(r.headers["Location"].rstrip("/").split("/")[-1])
    with app.app_context():
        id_credito = Credito.query.filter_by(id_venta=id_venta).first().id_credito  # S/ 50

    trabajador_a.post(f"/ventas/creditos/{id_credito}/pagar", data={"monto_recuperado": "20", "token_envio": "pago-doble-1"})
    repetido = trabajador_a.post(
        f"/ventas/creditos/{id_credito}/pagar", data={"monto_recuperado": "20", "token_envio": "pago-doble-1"}, follow_redirects=True
    )
    assert "ya estaba registrado" in repetido.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Credito, id_credito).saldo_pendiente == Decimal("30")


# ---------------------------------------------------------------------------
# La base de datos misma impide los valores imposibles
# ---------------------------------------------------------------------------
def test_la_base_de_datos_rechaza_stock_y_saldos_negativos(app):
    from sqlalchemy.exc import SQLAlchemyError

    id_producto = crear_producto(app, "CHECK-1", precio=10, stock=3)
    with app.app_context():
        fila = StockPorSede.query.filter_by(id_producto=id_producto).first()
        fila.cantidad_actual = -1
        try:
            db.session.commit()
            mensaje = None
        except SQLAlchemyError as error:
            db.session.rollback()
            mensaje = str(error)
        assert mensaje and "ck_stock_no_negativo" in mensaje, "MySQL debió rechazar un stock negativo"
    assert stock_de(app, id_producto) == 3  # el stock quedó intacto


# ---------------------------------------------------------------------------
# Bitácora escrita por varios procesos a la vez
# ---------------------------------------------------------------------------
def test_la_bitacora_se_mantiene_encadenada_con_escrituras_simultaneas(app):
    arranque = threading.Barrier(15)
    errores = []

    def escribir(i):
        try:
            with app.test_request_context():
                arranque.wait(timeout=15)
                registrar_accion("logout", usuario_login=f"hilo_{i}", detalle=f"escritura simultánea {i}")
        except Exception as error:  # noqa: BLE001
            errores.append(error)

    hilos = [threading.Thread(target=escribir, args=(i,)) for i in range(15)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=60)
    assert not errores, errores
    with app.app_context():
        resultado = verificar_bitacora()
        assert resultado["ok"], resultado["problemas"]
