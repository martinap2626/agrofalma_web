"""
Pruebas funcionales de punta a punta (equivalentes automatizados de los casos
CP-01 a CP-18 del proyecto): cada prueba usa el sistema como lo haría una
persona, a través de sus formularios y rutas reales, contra la base de datos
de pruebas.
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import Cliente, Credito, Producto, StockPorSede, Usuario, Venta
from conftest import CLAVE_ADMIN, CLAVE_TRABAJADOR, iniciar_sesion


def texto(respuesta):
    return respuesta.get_data(as_text=True)


# stock actual del producto en la sede de los trabajadores de prueba
def stock(app, id_producto):
    with app.app_context():
        return StockPorSede.query.filter_by(id_producto=id_producto).first().cantidad_actual


def nueva_venta(c, datos, tipo="contado", cantidad="2", precio="50", producto=None):
    return c.post("/ventas/nueva", data={
        "id_cliente": datos["cliente"], "tipo_venta": tipo,
        "id_producto[]": [producto or datos["p1"]], "cantidad[]": [cantidad], "precio_unitario[]": [precio],
    })


def id_de(respuesta):
    return int(respuesta.headers["Location"].rstrip("/").split("/")[-1])


# ---------------------------------------------------------------------------
# CP-01 Autenticación y acceso por rol
# ---------------------------------------------------------------------------
def test_cp01_cada_rol_aterriza_en_su_pantalla(cliente, app):
    r = iniciar_sesion(cliente, "admin_t", CLAVE_ADMIN)
    assert r.headers["Location"].endswith("/dashboard")
    otro = app.test_client()
    r = iniciar_sesion(otro, "trab_a", CLAVE_TRABAJADOR)
    assert r.headers["Location"].endswith("/ventas/mis-ventas")


# ---------------------------------------------------------------------------
# CP-02 Gestión de usuarios
# ---------------------------------------------------------------------------
def formulario_usuario(datos, **cambios):
    base = {
        "nombre": "Laura", "apellido": "Gomez", "usuario_login": "lgomez", "correo": "",
        "id_rol": datos["rol_trabajador"], "id_sede": datos["sede"],
        "contrasena": "ClaveSegura2026", "confirmar_contrasena": "ClaveSegura2026",
    }
    base.update(cambios)
    return base


def test_cp02_alta_edicion_y_desactivacion_de_usuario(app, admin, datos):
    assert admin.post("/usuarios/nuevo", data=formulario_usuario(datos)).status_code == 302
    with app.app_context():
        usuario = Usuario.query.filter_by(usuario_login="lgomez").first()
        assert usuario is not None and usuario.estado
        # la contraseña NO se guarda en texto plano
        assert usuario.contrasena_hash != "ClaveSegura2026" and usuario.check_password("ClaveSegura2026")
        id_usuario = usuario.id_usuario

    assert admin.post(f"/usuarios/{id_usuario}/editar", data=formulario_usuario(datos, nombre="Laura B")).status_code == 302
    assert admin.post(f"/usuarios/{id_usuario}/desactivar").status_code == 302
    with app.app_context():
        assert db.session.get(Usuario, id_usuario).estado is False
    # y una cuenta desactivada ya no puede entrar
    assert iniciar_sesion(app.test_client(), "lgomez", "ClaveSegura2026").status_code == 403
    assert admin.post(f"/usuarios/{id_usuario}/reactivar").status_code == 302
    assert iniciar_sesion(app.test_client(), "lgomez", "ClaveSegura2026").status_code == 302


@pytest.mark.parametrize("cambios,mensaje", [
    ({"usuario_login": "admin_t"}, "ya existe"),
    ({"contrasena": "corta1", "confirmar_contrasena": "corta1"}, "al menos 8"),
    ({"contrasena": "sololetrasxx", "confirmar_contrasena": "sololetrasxx"}, "letras y números"),
    ({"confirmar_contrasena": "OtraDistinta2026"}, "no coinciden"),
    ({"nombre": ""}, "nombre es obligatorio"),
    ({"nombre": "x" * 80}, "no puede superar"),
])
def test_cp02_validaciones_de_usuario(admin, datos, cambios, mensaje):
    r = admin.post("/usuarios/nuevo", data=formulario_usuario(datos, **{"usuario_login": "otro_u", **cambios}))
    assert r.status_code == 200 and mensaje in texto(r)


def test_cp02_el_administrador_no_puede_desactivarse_a_si_mismo(app, admin):
    with app.app_context():
        id_admin = Usuario.query.filter_by(usuario_login="admin_t").first().id_usuario
    admin.post(f"/usuarios/{id_admin}/desactivar")
    with app.app_context():
        assert db.session.get(Usuario, id_admin).estado is True


# ---------------------------------------------------------------------------
# CP-03 / CP-07 / CP-16 Productos
# ---------------------------------------------------------------------------
def formulario_producto(datos, **cambios):
    base = {
        "codigo_producto": "NUEVO-1", "nombre_producto": "Producto nuevo", "id_categoria": datos["categoria"],
        "id_unidad": datos["unidad"], "precio_unitario": "12.50", "stock_minimo": "5", "descripcion": "",
    }
    base.update(cambios)
    return base


def test_cp03_alta_y_edicion_de_producto(app, admin, datos):
    assert admin.post("/productos/nuevo", data=formulario_producto(datos)).status_code == 302
    with app.app_context():
        producto = Producto.query.filter_by(codigo_producto="NUEVO-1").first()
        assert producto.precio_unitario == Decimal("12.50")
        id_producto = producto.id_producto
    r = admin.post(f"/productos/{id_producto}/editar", data=formulario_producto(datos, precio_unitario="15"))
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(Producto, id_producto).precio_unitario == Decimal("15")


@pytest.mark.parametrize("cambios,mensaje", [
    ({"codigo_producto": "P-001"}, "ya está en uso"),
    ({"codigo_producto": ""}, "código de producto es obligatorio"),
    ({"nombre_producto": ""}, "nombre del producto es obligatorio"),
    ({"precio_unitario": "-5"}, "no puede ser negativo"),
    ({"precio_unitario": "abc"}, "no es válido"),
    ({"precio_unitario": "NaN"}, "no es válido"),
    ({"precio_unitario": "Infinity"}, "no es válido"),
    ({"precio_unitario": "999999999999"}, "no es válido"),
    ({"stock_minimo": "-1"}, "entre 0 y"),
    ({"stock_minimo": "99999999999"}, "entre 0 y"),
    ({"nombre_producto": "x" * 200}, "no puede superar"),
])
def test_cp16_validacion_de_campos_de_producto(admin, datos, cambios, mensaje):
    r = admin.post("/productos/nuevo", data=formulario_producto(datos, **cambios))
    assert r.status_code == 200 and mensaje in texto(r)


def test_cp07_el_trabajador_consulta_el_catalogo_pero_sin_acciones_de_edicion(trabajador_a):
    r = trabajador_a.get("/productos")
    assert r.status_code == 200 and "Foliar de prueba" in texto(r)
    assert "Nuevo producto" not in texto(r)


# ---------------------------------------------------------------------------
# CP-04 Registro de ventas
# ---------------------------------------------------------------------------
def test_cp04_venta_al_contado_descuenta_stock_y_calcula_total(app, trabajador_a, datos):
    antes = stock(app, datos["p1"])
    r = nueva_venta(trabajador_a, datos, cantidad="3", precio="40")
    assert r.status_code == 302
    with app.app_context():
        venta = db.session.get(Venta, id_de(r))
        assert venta.total == Decimal("120") and venta.estado_pago == "pagado"
        assert venta.numero_operacion.startswith("OP-")
        assert venta.credito is None
    assert stock(app, datos["p1"]) == antes - 3


@pytest.mark.parametrize("kwargs,mensaje", [
    ({"cantidad": "0"}, "mayor a 0"),
    ({"cantidad": "-3"}, "mayor a 0"),
    ({"cantidad": "abc"}, "mayor a 0"),
    ({"cantidad": "99999999"}, "mayor a 0"),
    ({"cantidad": "100000"}, "No hay stock suficiente"),
    ({"precio": "-1"}, "precio"),
    ({"precio": "NaN"}, "precio"),
    ({"precio": "Infinity"}, "precio"),
    ({"precio": "1e400"}, "precio"),
])
def test_cp04_ventas_invalidas_se_rechazan_sin_tocar_el_stock(app, trabajador_a, datos, kwargs, mensaje):
    antes = stock(app, datos["p1"])
    r = nueva_venta(trabajador_a, datos, **kwargs)
    assert r.status_code == 200 and mensaje in texto(r)
    assert stock(app, datos["p1"]) == antes


def test_cp04_venta_sin_productos_o_con_cliente_invalido_se_rechaza(trabajador_a, datos):
    r = trabajador_a.post("/ventas/nueva", data={"id_cliente": datos["cliente"], "tipo_venta": "contado"})
    assert r.status_code == 200 and "al menos un producto" in texto(r)
    r = trabajador_a.post("/ventas/nueva", data={
        "id_cliente": "999999", "tipo_venta": "contado", "id_producto[]": [datos["p1"]], "cantidad[]": ["1"],
    })
    assert "cliente válido" in texto(r)


def test_cp04_observaciones_demasiado_largas_se_rechazan(trabajador_a, datos):
    r = trabajador_a.post("/ventas/nueva", data={
        "id_cliente": datos["cliente"], "tipo_venta": "contado", "observaciones": "x" * 300,
        "id_producto[]": [datos["p1"]], "cantidad[]": ["1"], "precio_unitario[]": ["10"],
    })
    assert r.status_code == 200 and "no puede superar" in texto(r)


# ---------------------------------------------------------------------------
# CP-05 Ventas consolidadas / CP-12 Reportes
# ---------------------------------------------------------------------------
def test_cp05_el_administrador_ve_las_ventas_consolidadas_con_filtros(admin, trabajador_a, datos):
    nueva_venta(trabajador_a, datos, cantidad="1")
    assert admin.get("/ventas").status_code == 200
    assert admin.get("/ventas?desde=2020-01-01&hasta=2099-12-31&categoria=1&sede=1").status_code == 200
    assert admin.get("/ventas?desde=basura&hasta=basura").status_code == 200


def test_cp12_reportes_en_pantalla_y_csv(admin):
    assert admin.get("/reportes?tipo=ventas").status_code == 200
    assert admin.get("/reportes?tipo=productos").status_code == 200
    csv = admin.get("/reportes/exportar?tipo=productos&desde=2020-01-01&hasta=2099-12-31")
    assert csv.status_code == 200 and "attachment" in csv.headers["Content-Disposition"]
    assert admin.get("/reportes?desde=basura&hasta=basura&id_sede=xx").status_code == 200


# ---------------------------------------------------------------------------
# CP-06 Créditos y recuperaciones
# ---------------------------------------------------------------------------
def crear_credito(app, c, datos, cantidad="2", precio="50"):
    r = nueva_venta(c, datos, tipo="credito", cantidad=cantidad, precio=precio)
    assert r.status_code == 302
    with app.app_context():
        credito = Credito.query.filter_by(id_venta=id_de(r)).first()
        assert credito.saldo_pendiente == Decimal(cantidad) * Decimal(precio) and credito.estado == "pendiente"
        return credito.id_credito


def estado_credito(app, id_credito):
    with app.app_context():
        credito = db.session.get(Credito, id_credito)
        return credito.saldo_pendiente, credito.estado, credito.venta.estado_pago


def test_cp06_pagos_parciales_y_total_cierran_el_credito(app, trabajador_a, datos):
    id_credito = crear_credito(app, trabajador_a, datos, cantidad="2", precio="50")  # S/ 100
    assert trabajador_a.post(f"/ventas/creditos/{id_credito}/pagar", data={"monto_recuperado": "30"}).status_code == 302
    assert estado_credito(app, id_credito) == (Decimal("70"), "pendiente", "pendiente")
    trabajador_a.post(f"/ventas/creditos/{id_credito}/pagar", data={"monto_recuperado": "70"})
    assert estado_credito(app, id_credito) == (Decimal("0"), "pagado", "pagado")


@pytest.mark.parametrize("monto", ["0", "-5", "abc", "NaN", "Infinity", "1e400", "101", ""])
def test_cp06_pagos_invalidos_se_rechazan_sin_alterar_el_saldo(app, trabajador_a, datos, monto):
    id_credito = crear_credito(app, trabajador_a, datos)
    r = trabajador_a.post(f"/ventas/creditos/{id_credito}/pagar", data={"monto_recuperado": monto}, follow_redirects=True)
    assert r.status_code == 200
    assert estado_credito(app, id_credito)[0] == Decimal("100")


def test_cp06_un_credito_saldado_no_acepta_mas_pagos(app, trabajador_a, datos):
    id_credito = crear_credito(app, trabajador_a, datos, cantidad="1", precio="10")
    trabajador_a.post(f"/ventas/creditos/{id_credito}/pagar", data={"monto_recuperado": "10"})
    r = trabajador_a.post(f"/ventas/creditos/{id_credito}/pagar", data={"monto_recuperado": "1"}, follow_redirects=True)
    assert "ya está saldado" in texto(r)


# ---------------------------------------------------------------------------
# CP-08 / CP-09 / CP-10 / CP-11 Dashboard y analítica (solo Administrador)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ruta", [
    "/dashboard", "/dashboard?mes=2026-08", "/analitica", "/analitica?urgencia=urgente",
    "/analitica/prediccion", "/analitica/segmentacion", "/analitica/segmentacion?clase=A",
])
def test_cp08_a_11_pantallas_del_administrador_responden(admin, ruta):
    assert admin.get(ruta).status_code == 200


def test_dashboard_suma_exactamente_lo_vendido_en_el_mes(app, admin, trabajador_a, datos):
    from datetime import date

    from app.extensions import db
    from app.utilidades_fecha import rango_mes

    nueva_venta(trabajador_a, datos, tipo="contado", cantidad="3", precio="40")  # 120
    nueva_venta(trabajador_a, datos, tipo="credito", cantidad="1", precio="25")  # 25

    hoy = date.today()
    inicio, fin = rango_mes(hoy.year, hoy.month)
    with app.app_context():
        esperado = dict(
            db.session.query(Venta.tipo_venta, db.func.sum(Venta.total))
            .filter(Venta.fecha_hora >= inicio, Venta.fecha_hora < fin, Venta.anulada.is_(False)).group_by(Venta.tipo_venta).all()
        )
    html = texto(admin.get("/dashboard"))
    assert f"S/ {esperado['contado']:.2f}" in html
    assert f"S/ {esperado['credito']:.2f}" in html
    # un mes sin ventas no rompe nada
    assert admin.get("/dashboard?mes=1999-01").status_code == 200
    # meses imposibles o basura en el parámetro no rompen la pantalla (se usa el mes actual)
    for mes_malo in ("1999-13", "2026-00", "0-5", "abc", "2026-5-1", "99999-01", "2026-"):
        assert admin.get(f"/dashboard?mes={mes_malo}").status_code == 200


def test_el_reporte_de_ventas_resume_igual_que_el_detalle(app, admin, trabajador_a, datos):
    nueva_venta(trabajador_a, datos, cantidad="2", precio="10")
    html = texto(admin.get("/reportes?tipo=ventas&desde=2020-01-01&hasta=2099-12-31"))
    with app.app_context():
        # las ventas anuladas no cuentan en el reporte, igual que en la pantalla
        vigentes = Venta.query.filter_by(anulada=False)
        total = sum((v.total for v in vigentes.all()), Decimal("0"))
        cantidad = vigentes.count()
    assert f"S/ {total:.2f}" in html and f">{cantidad}<" in html.replace(" ", "").replace("\n", "")


# ---------------------------------------------------------------------------
# Ayuda en el sistema
# ---------------------------------------------------------------------------
def test_la_ayuda_muestra_secciones_segun_el_rol(admin, trabajador_a):
    html_admin = texto(admin.get("/ayuda"))
    html_trab = texto(trabajador_a.get("/ayuda"))
    assert 'id="analitica"' in html_admin and 'id="auditoria"' in html_admin
    assert 'id="vender"' in html_trab and 'id="auditoria"' not in html_trab and 'id="analitica"' not in html_trab
    assert "Glosario" in html_admin and "Glosario" in html_trab


def test_la_ayuda_exige_sesion(cliente):
    assert cliente.get("/ayuda").status_code == 302


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------
def test_clientes_alta_validaciones_y_baja_logica(app, trabajador_a):
    assert trabajador_a.post("/ventas/clientes/nuevo", data={"nombre_cliente": "Agro Sur", "documento": "20100130204"}).status_code == 302
    r = trabajador_a.post("/ventas/clientes/nuevo", data={"nombre_cliente": ""})
    assert "obligatorio" in texto(r)
    r = trabajador_a.post("/ventas/clientes/nuevo", data={"nombre_cliente": "x" * 150})
    assert "no puede superar" in texto(r)
    r = trabajador_a.post("/ventas/clientes/nuevo", data={"nombre_cliente": "Ok", "telefono": "9" * 40})
    assert "no puede superar" in texto(r)


# ---------------------------------------------------------------------------
# Fiabilidad: el sistema tolera fallos sin mostrar errores técnicos
# ---------------------------------------------------------------------------
def test_si_la_base_de_datos_cae_el_usuario_ve_un_mensaje_claro(trabajador_a, monkeypatch):
    class ProductoRoto:
        @property
        def query(self):
            raise OperationalError("SELECT", {}, Exception("Lost connection to MySQL server"))

    monkeypatch.setattr("app.productos.routes.Producto", ProductoRoto())
    r = trabajador_a.get("/productos")
    assert r.status_code == 503
    assert "No se pudo conectar" in texto(r)
    assert "Traceback" not in texto(r) and "OperationalError" not in texto(r)


def test_un_error_inesperado_muestra_pagina_amable_sin_detalles_tecnicos(app, trabajador_a, monkeypatch):
    def explota(*_a, **_k):
        raise RuntimeError("detalle interno secreto")

    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    monkeypatch.setattr("app.productos.routes.render_template", explota)
    r = trabajador_a.get("/productos")
    assert r.status_code == 500
    assert "problema inesperado" in texto(r)
    assert "detalle interno secreto" not in texto(r)


def test_despues_de_un_fallo_el_sistema_sigue_funcionando(app, trabajador_a, monkeypatch):
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    with monkeypatch.context() as m:
        m.setattr("app.productos.routes.render_template", lambda *a, **k: 1 / 0)
        assert trabajador_a.get("/productos").status_code == 500
    assert trabajador_a.get("/productos").status_code == 200


# ---------------------------------------------------------------------------
# Modo privacidad (ocultar montos) y auditoría paginada
# ---------------------------------------------------------------------------
def test_el_boton_de_privacidad_esta_en_todas_las_pantallas_con_sesion_y_no_en_el_login(admin, trabajador_a, cliente):
    for c, ruta in ((admin, "/dashboard"), (admin, "/productos"), (trabajador_a, "/ventas/mis-ventas"), (trabajador_a, "/ayuda")):
        html = texto(c.get(ruta))
        assert 'id="boton-privacidad"' in html and "js/privacidad.js" in html
    assert 'id="boton-privacidad"' not in texto(cliente.get("/login"))


@pytest.mark.parametrize("ruta,minimo", [
    ("/dashboard", 6), ("/productos", 1), ("/ventas", 0), ("/reportes?tipo=ventas", 4), ("/reportes?tipo=productos", 1),
    ("/ventas/mis-ventas", 4), ("/ventas/creditos", 0), ("/ventas/nueva", 3),
])
def test_los_montos_de_dinero_estan_marcados_para_poder_ocultarse(admin, trabajador_a, datos, ruta, minimo):
    nueva_venta(trabajador_a, datos, cantidad="1", precio="30")
    nueva_venta(trabajador_a, datos, tipo="credito", cantidad="1", precio="30")
    cliente_http = trabajador_a if ruta in ("/ventas/mis-ventas", "/ventas/creditos", "/ventas/nueva") else admin
    html = texto(cliente_http.get(ruta))
    assert html.count('class="monto') + html.count("monto\"") >= minimo
    # ninguna cifra "S/ 12.34" queda suelta, fuera de un elemento marcado como monto (salvo etiquetas de texto)
    import re
    sueltas = [m for m in re.finditer(r"S/ \d", html) if 'monto' not in html[max(0, m.start() - 80):m.start()]]
    assert not sueltas, html[sueltas[0].start() - 80:sueltas[0].start() + 40]


def test_la_auditoria_se_pagina_de_a_10_registros(app, admin):
    from app.seguridad import registrar_accion

    # las filas se agregan por registrar_accion (la única vía legítima), así quedan encadenadas en la bitácora
    with app.test_request_context():
        for i in range(25):
            registrar_accion("logout", usuario_login="paginacion_t", detalle=f"fila {i}")
    html = texto(admin.get("/auditoria?usuario=paginacion_t"))
    assert html.count("<tr>") - 1 == 10                     # 10 filas (menos la cabecera)
    assert "Página 1 de 3" in html
    assert "Página 3 de 3" in texto(admin.get("/auditoria?usuario=paginacion_t&pagina=3"))
    assert texto(admin.get("/auditoria?usuario=paginacion_t&pagina=3")).count("<tr>") - 1 == 5


def test_ya_no_se_muestran_avisos_de_stock_provisional(admin, trabajador_a):
    assert "Stock provisional" not in texto(admin.get("/analitica"))
    assert "Stock provisional" not in texto(trabajador_a.get("/productos"))


# ---------------------------------------------------------------------------
# Inyección de fórmulas en el CSV exportado
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("valor,esperado", [("=1+1", "'=1+1"), ("+cmd", "'+cmd"), ("-2", "'-2"), ("@SUMA(A1)", "'@SUMA(A1)"), ("Juan", "Juan")])
def test_el_csv_neutraliza_textos_que_excel_leeria_como_formula(valor, esperado):
    from app.reportes.routes import _texto_seguro
    assert _texto_seguro(valor) == esperado


def test_la_exportacion_no_deja_pasar_formulas_en_el_nombre_del_cliente(app, admin, trabajador_a, datos):
    assert nueva_venta(trabajador_a, datos).status_code == 302
    with app.app_context():
        cliente = db.session.get(Cliente, datos["cliente"])
        original = cliente.nombre_cliente
        cliente.nombre_cliente = "=HYPERLINK(\"http://malo\")"
        db.session.commit()
    try:
        csv = texto(admin.get("/reportes/exportar?tipo=ventas&desde=2020-01-01&hasta=2099-12-31"))
        assert "'=HYPERLINK" in csv and ";=HYPERLINK" not in csv
    finally:
        with app.app_context():
            db.session.get(Cliente, datos["cliente"]).nombre_cliente = original
            db.session.commit()
