"""
Pruebas de la cuenta del usuario (cambio de contraseña y cierre de sesiones), cierre de sesión seguro, ruta de salud,
límite de descargas, validación de clientes y cadena de huellas de la bitácora.
"""

import re

import pytest
from sqlalchemy import text

from app.extensions import db
from app.models import Bitacora, BitacoraCadena, Cliente, Usuario
from app.seguridad import registrar_accion, verificar_bitacora
from conftest import CLAVE_ADMIN, crear_usuario, iniciar_sesion, sesion_de


def texto(respuesta):
    return respuesta.get_data(as_text=True)


def acciones(app, accion, login=None):
    with app.app_context():
        consulta = Bitacora.query.filter_by(accion=accion)
        if login:
            consulta = consulta.filter_by(usuario_login=login)
        return [b.detalle for b in consulta.all()]


def cambiar(c, actual, nueva, confirmar=None):
    return c.post("/cuenta/contrasena", data={
        "contrasena_actual": actual, "contrasena_nueva": nueva, "confirmar_contrasena": nueva if confirmar is None else confirmar,
    })


# ---------------------------------------------------------------------------
# Cierre de sesión
# ---------------------------------------------------------------------------
def test_cerrar_sesion_exige_post_y_un_enlace_no_cierra_nada(app, admin):
    r = admin.get("/logout")
    assert r.status_code == 302  # solo redirige: no cerró la sesión
    assert admin.get("/usuarios").status_code == 200
    assert admin.post("/logout").status_code == 302
    assert admin.get("/usuarios").status_code == 302  # ahora sí, de vuelta al login


def test_el_boton_salir_es_un_formulario_y_no_un_enlace(admin):
    html = texto(admin.get("/dashboard"))
    assert 'action="/logout"' in html and 'class="boton-salir"' in html and 'name="csrf_token"' in html
    assert 'href="/logout"' not in html


def test_cerrar_sesion_desde_otro_sitio_sin_token_se_rechaza():
    # con CSRF activo, un POST sin token se rechaza: el cierre de sesión no se puede disparar desde otro sitio
    from app import create_app
    from conftest import ConfigPruebasConCSRF

    c = create_app(ConfigPruebasConCSRF).test_client()
    # se obtiene el token del formulario de acceso para iniciar sesión legítimamente
    html = c.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    c.post("/login", data={"usuario": "admin_t", "contrasena": CLAVE_ADMIN, "csrf_token": token})
    assert c.post("/logout").status_code == 400  # sin token
    assert c.get("/usuarios").status_code == 200  # la sesión sigue abierta


# ---------------------------------------------------------------------------
# Ruta de salud
# ---------------------------------------------------------------------------
def test_la_ruta_de_salud_responde_sin_sesion_y_sin_revelar_datos_internos(cliente):
    r = cliente.get("/salud")
    assert r.status_code == 200 and r.get_json() == {"estado": "ok"}


def test_la_ruta_de_salud_avisa_cuando_la_base_no_responde(cliente, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def falla(*_a, **_k):
        raise OperationalError("SELECT 1", {}, Exception("Lost connection to MySQL server"))

    monkeypatch.setattr(db.session, "execute", falla)
    r = cliente.get("/salud")
    assert r.status_code == 503 and r.get_json() == {"estado": "no disponible"}
    assert "Lost connection" not in texto(r)


# ---------------------------------------------------------------------------
# Cambio de contraseña propio y sesiones
# ---------------------------------------------------------------------------
def test_cambiar_la_contrasena_cierra_las_otras_sesiones_pero_no_la_actual(app):
    crear_usuario(app, "cuenta_1", "ClaveInicial2026")
    en_el_taller = sesion_de(app, "cuenta_1", "ClaveInicial2026")
    en_la_oficina = sesion_de(app, "cuenta_1", "ClaveInicial2026")
    assert en_la_oficina.get("/productos").status_code == 200

    r = cambiar(en_el_taller, "ClaveInicial2026", "ClaveNueva2026")
    assert r.status_code == 302
    assert en_el_taller.get("/productos").status_code == 200   # la sesión donde se cambió sigue abierta
    assert en_la_oficina.get("/productos").status_code == 302  # la otra se cerró: pide iniciar sesión de nuevo
    assert iniciar_sesion(app.test_client(), "cuenta_1", "ClaveInicial2026").status_code == 401  # la vieja ya no sirve
    assert iniciar_sesion(app.test_client(), "cuenta_1", "ClaveNueva2026").status_code == 302
    assert acciones(app, "contrasena_cambiada", "cuenta_1")


@pytest.mark.parametrize("actual,nueva,confirmar,mensaje", [
    ("ClaveMala2026x", "ClaveNueva2026", None, "contraseña actual no es correcta"),
    ("ClaveInicial2026", "corta1", None, "al menos 8"),
    ("ClaveInicial2026", "sololetrasxx", None, "letras y números"),
    ("ClaveInicial2026", "ClaveInicial2026", None, "distinta de la actual"),
    ("ClaveInicial2026", "ClaveNueva2026", "OtraCosa2026", "no coincide"),
])
def test_cambio_de_contrasena_invalido_se_rechaza_y_la_clave_sigue_igual(app, actual, nueva, confirmar, mensaje):
    login = f"cuenta_v{abs(hash((actual, nueva, confirmar))) % 100000}"
    crear_usuario(app, login, "ClaveInicial2026")
    c = sesion_de(app, login, "ClaveInicial2026")
    r = cambiar(c, actual, nueva, confirmar)
    assert r.status_code == 400 and mensaje in texto(r)
    assert iniciar_sesion(app.test_client(), login, "ClaveInicial2026").status_code == 302


def test_los_intentos_con_la_contrasena_actual_equivocada_se_bloquean(app):
    crear_usuario(app, "cuenta_2", "ClaveInicial2026")
    c = sesion_de(app, "cuenta_2", "ClaveInicial2026")
    for _ in range(5):
        assert cambiar(c, "adivinando123", "ClaveNueva2026").status_code == 400
    # el sexto intento se bloquea, aunque ahora escriba la contraseña correcta
    r = cambiar(c, "ClaveInicial2026", "ClaveNueva2026")
    assert r.status_code == 429 and "Demasiados intentos" in texto(r)
    assert acciones(app, "contrasena_bloqueada", "cuenta_2")


def test_cuando_el_administrador_cambia_la_contrasena_de_otro_se_cierran_las_sesiones_de_ese_usuario(app, admin):
    id_usuario = crear_usuario(app, "cuenta_3", "ClaveInicial2026")
    sesion_vieja = sesion_de(app, "cuenta_3", "ClaveInicial2026")
    with app.app_context():
        usuario = db.session.get(Usuario, id_usuario)
        datos_form = {
            "nombre": usuario.nombre, "apellido": usuario.apellido, "correo": "", "id_rol": usuario.id_rol, "id_sede": usuario.id_sede,
            "contrasena": "ClaveDelAdmin2026", "confirmar_contrasena": "ClaveDelAdmin2026",
        }
    assert admin.post(f"/usuarios/{id_usuario}/editar", data=datos_form).status_code == 302
    assert sesion_vieja.get("/productos").status_code == 302


def test_si_el_administrador_cambia_su_propia_contrasena_no_se_cierra_su_sesion(app):
    crear_usuario(app, "admin_extra", "ClaveInicial2026", rol="Administrador")
    c = sesion_de(app, "admin_extra", "ClaveInicial2026")
    r = cambiar(c, "ClaveInicial2026", "ClaveNueva2026")
    assert r.status_code == 302
    assert c.get("/usuarios").status_code == 200


def test_la_pagina_de_cambio_de_contrasena_exige_sesion(cliente):
    assert cliente.get("/cuenta/contrasena").status_code == 302


# ---------------------------------------------------------------------------
# Límite de descargas
# ---------------------------------------------------------------------------
def test_las_descargas_de_reportes_tienen_un_tope_por_usuario(app, monkeypatch):
    crear_usuario(app, "exporta_1", "ClaveInicial2026", rol="Administrador")
    c = sesion_de(app, "exporta_1", "ClaveInicial2026")
    monkeypatch.setitem(app.config, "EXPORT_MAX_POR_VENTANA", 2)
    ruta = "/reportes/exportar?tipo=ventas&desde=2020-01-01&hasta=2099-12-31"
    assert c.get(ruta).status_code == 200
    assert c.get(ruta).status_code == 200
    bloqueada = c.get(ruta)
    assert bloqueada.status_code == 429 and "Demasiadas solicitudes" in texto(bloqueada)
    assert acciones(app, "exportacion_limitada", "exporta_1")


# ---------------------------------------------------------------------------
# Clientes: documento válido, sin duplicados, búsqueda y paginación en el servidor
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("documento,mensaje", [
    ("12345", "DNI (8 dígitos)"),
    ("20123456789", "RUC no es válido"),
    ("12-345-678", "solo puede tener letras y números"),
    ("ABC", "entre 9 y 12 caracteres"),
])
def test_un_documento_mal_escrito_no_se_acepta(trabajador_a, documento, mensaje):
    r = trabajador_a.post("/ventas/clientes/nuevo", data={"nombre_cliente": "Cliente Documento", "documento": documento})
    assert r.status_code == 200 and mensaje in texto(r)


@pytest.mark.parametrize("documento", ["45678912", "20131312955", "000123456", "AB1234567", ""])
def test_un_documento_valido_o_vacio_se_acepta(trabajador_a, documento):
    r = trabajador_a.post("/ventas/clientes/nuevo", data={"nombre_cliente": f"Cliente {documento or 'sin doc'}", "documento": documento})
    assert r.status_code == 302


def test_no_se_puede_registrar_dos_veces_el_mismo_documento(trabajador_a):
    assert trabajador_a.post("/ventas/clientes/nuevo", data={"nombre_cliente": "Original", "documento": "70123456"}).status_code == 302
    r = trabajador_a.post("/ventas/clientes/nuevo", data={"nombre_cliente": "Copia", "documento": "70123456"})
    assert r.status_code == 200 and "Ya existe un cliente con ese documento" in texto(r) and "Original" in texto(r)


def test_editar_otro_dato_de_un_cliente_antiguo_no_exige_corregir_su_documento(app, trabajador_a):
    with app.app_context():
        antiguo = Cliente(nombre_cliente="Cliente antiguo", documento="12-34")  # formato que ya no se aceptaría
        db.session.add(antiguo)
        db.session.commit()
        id_cliente = antiguo.id_cliente
    r = trabajador_a.post(f"/ventas/clientes/{id_cliente}/editar", data={"nombre_cliente": "Cliente antiguo S.A.", "documento": "12-34"})
    assert r.status_code == 302
    # pero si cambia el documento, el nuevo sí se valida
    r = trabajador_a.post(f"/ventas/clientes/{id_cliente}/editar", data={"nombre_cliente": "Cliente antiguo S.A.", "documento": "9"})
    assert r.status_code == 200 and "documento" in texto(r).lower()


def test_el_listado_de_clientes_busca_y_pagina_en_el_servidor(app, trabajador_a):
    with app.app_context():
        for i in range(60):
            db.session.add(Cliente(nombre_cliente=f"Paginado {i:03d}", documento=None))
        db.session.commit()
    primera = texto(trabajador_a.get("/ventas/clientes?q=Paginado"))
    assert "60 cliente(s)" in primera and "Página 1 de 3" in primera
    assert "Paginado 000" in primera and "Paginado 030" not in primera
    tercera = texto(trabajador_a.get("/ventas/clientes?q=Paginado&pagina=3"))
    assert "Paginado 059" in tercera and "Paginado 000" not in tercera
    # páginas fuera de rango o con basura no rompen nada
    assert trabajador_a.get("/ventas/clientes?pagina=9999").status_code == 200
    assert trabajador_a.get("/ventas/clientes?pagina=abc").status_code == 200
    assert "Paginado 017" in texto(trabajador_a.get("/ventas/clientes?q=Paginado 017"))


# ---------------------------------------------------------------------------
# Bitácora encadenada
# ---------------------------------------------------------------------------
def test_cada_fila_nueva_de_la_bitacora_lleva_su_huella_encadenada(app):
    with app.test_request_context():
        registrar_accion("logout", usuario_login="cadena_1", detalle="primera")
        registrar_accion("logout", usuario_login="cadena_1", detalle="segunda")
    with app.app_context():
        filas = Bitacora.query.filter_by(usuario_login="cadena_1").order_by(Bitacora.id_bitacora).all()
        assert all(f.hash_fila and len(f.hash_fila) == 64 for f in filas)
        assert filas[1].hash_previo == filas[0].hash_fila  # cada fila apunta a la anterior
        assert db.session.get(BitacoraCadena, 1).ultimo_hash == verificar_bitacora()["ultimo_hash"]
        assert verificar_bitacora()["ok"]


def test_editar_una_fila_directamente_en_la_base_rompe_la_cadena_y_se_detecta(app):
    with app.test_request_context():
        registrar_accion("logout", usuario_login="cadena_2", detalle="detalle original")
    with app.app_context():
        id_fila = Bitacora.query.filter_by(usuario_login="cadena_2").first().id_bitacora
        assert verificar_bitacora()["ok"]
        try:
            db.session.execute(text("UPDATE bitacora_acciones SET detalle = 'detalle falsificado' WHERE id_bitacora = :i"), {"i": id_fila})
            db.session.commit()
            resultado = verificar_bitacora()
            assert not resultado["ok"]
            assert any(str(id_fila) in p and "modificada" in p for p in resultado["problemas"])
        finally:
            db.session.execute(text("UPDATE bitacora_acciones SET detalle = 'detalle original' WHERE id_bitacora = :i"), {"i": id_fila})
            db.session.commit()
        assert verificar_bitacora()["ok"]


def test_borrar_una_fila_intermedia_o_la_ultima_tambien_se_detecta(app):
    with app.test_request_context():
        for i in range(3):
            registrar_accion("logout", usuario_login="cadena_3", detalle=f"fila {i}")
    with app.app_context():
        filas = Bitacora.query.filter_by(usuario_login="cadena_3").order_by(Bitacora.id_bitacora).all()
        copias = [(f.id_bitacora, f.fecha_hora, f.usuario_login, f.accion, f.detalle, f.resultado, f.hash_previo, f.hash_fila) for f in filas]
        intermedia, ultima = copias[1], copias[2]

        def reponer(copia):
            db.session.execute(text(
                "INSERT INTO bitacora_acciones (id_bitacora, fecha_hora, usuario_login, accion, detalle, resultado, hash_previo, hash_fila) "
                "VALUES (:i, :f, :u, :a, :d, :r, :hp, :hf)"
            ), dict(zip(("i", "f", "u", "a", "d", "r", "hp", "hf"), copia)))
            db.session.commit()

        # se borra la del medio
        db.session.execute(text("DELETE FROM bitacora_acciones WHERE id_bitacora = :i"), {"i": intermedia[0]})
        db.session.commit()
        assert not verificar_bitacora()["ok"]
        reponer(intermedia)
        assert verificar_bitacora()["ok"]

        # se borra la última fila de todas (la de la cadena queda recordando su huella)
        ultima_global = Bitacora.query.order_by(Bitacora.id_bitacora.desc()).first()
        copia_global = (ultima_global.id_bitacora, ultima_global.fecha_hora, ultima_global.usuario_login, ultima_global.accion,
                        ultima_global.detalle, ultima_global.resultado, ultima_global.hash_previo, ultima_global.hash_fila)
        db.session.execute(text("DELETE FROM bitacora_acciones WHERE id_bitacora = :i"), {"i": copia_global[0]})
        db.session.commit()
        assert not verificar_bitacora()["ok"]
        reponer(copia_global)
        assert verificar_bitacora()["ok"]
        assert ultima[0] > 0


def test_el_administrador_verifica_la_bitacora_desde_la_pantalla(admin):
    r = admin.post("/auditoria/verificar", follow_redirects=True)
    assert "La bitácora está íntegra" in texto(r)


def test_el_trabajador_no_puede_verificar_la_bitacora(trabajador_a):
    assert trabajador_a.post("/auditoria/verificar").status_code == 403


# ---------------------------------------------------------------------------
# Panel de control y seguridad del dashboard
# ---------------------------------------------------------------------------
def test_el_dashboard_alerta_cuando_hay_muchos_accesos_fallidos(app, admin):
    from app.dashboard.routes import _resumen_control

    with app.app_context():
        antes = _resumen_control()["accesos_fallidos"]
    # sin dirección IP (no cuentan para el bloqueo por IP): así esta prueba no bloquea los accesos de las demás pruebas
    with app.test_request_context():
        for _ in range(5):
            registrar_accion("login_fallido", usuario_login="sintetico_alerta", resultado="fallo")
    with app.app_context():
        despues = _resumen_control()
    assert despues["accesos_fallidos"] == antes + 5 and despues["alerta"] is True
    html = texto(admin.get("/dashboard"))
    assert "Control y seguridad" in html and "actividad sospechosa" in html and "Revisar" in html


def test_el_dashboard_cuenta_anulaciones_y_rebajas_de_la_semana(app, admin):
    from app.dashboard.routes import _resumen_control

    with app.app_context():
        antes = _resumen_control()
    with app.test_request_context():
        registrar_accion("venta_anulada", detalle="sintética")
        registrar_accion("venta_precio_rebajado", detalle="sintética")
        registrar_accion("venta_precio_rebajado", detalle="sintética")
        registrar_accion("inventario_conteo", detalle="sintético")
    with app.app_context():
        despues = _resumen_control()
    assert (despues["anulaciones"], despues["rebajas"], despues["conteos"]) == (
        antes["anulaciones"] + 1, antes["rebajas"] + 2, antes["conteos"] + 1)
    assert admin.get("/dashboard").status_code == 200


def test_el_trabajador_no_ve_el_panel_de_control(trabajador_a):
    assert trabajador_a.get("/dashboard").status_code == 403
