"""Pruebas de la auditoría con valor anterior y nuevo en las ediciones, y del registro de operaciones de consola."""

import uuid

import pytest

from app.extensions import db
from app.models import Bitacora, Categoria, Cliente, Usuario, UnidadMedida
from app.seguridad import auditado, verificar_bitacora
from conftest import crear_producto, crear_usuario


def test_editar_producto_anota_valor_anterior_y_nuevo(app, admin):
    codigo = "AUD-" + uuid.uuid4().hex[:8]
    id_producto = crear_producto(app, codigo, precio=50, stock_minimo=5)
    with app.app_context():
        categoria = Categoria.query.first().id_categoria
        unidad = UnidadMedida.query.first().id_unidad
    r = admin.post(f"/productos/{id_producto}/editar", data={
        "codigo_producto": codigo, "nombre_producto": f"Producto {codigo}", "id_categoria": categoria, "id_unidad": unidad,
        "precio_unitario": "55", "stock_minimo": "8", "descripcion": ""})
    assert r.status_code == 302
    with app.app_context():
        fila = Bitacora.query.filter_by(accion="producto_editado", id_entidad=id_producto).first()
    assert "precio de 50.00 a 55.00" in fila.detalle and "stock mínimo de 5 a 8" in fila.detalle
    assert "categoría" not in fila.detalle  # lo que no cambia no se menciona


def test_editar_usuario_no_guarda_la_contrasena_pero_si_que_cambio(app, admin):
    login = "aud_" + uuid.uuid4().hex[:6]
    id_usuario = crear_usuario(app, login, "ClaveInicial2026")
    with app.app_context():
        u = db.session.get(Usuario, id_usuario)
        datos = {"nombre": "Nuevo", "apellido": u.apellido, "correo": "", "id_rol": u.id_rol, "id_sede": u.id_sede,
                 "contrasena": "OtraClave2026Segura", "confirmar_contrasena": "OtraClave2026Segura"}
    assert admin.post(f"/usuarios/{id_usuario}/editar", data=datos).status_code == 302
    with app.app_context():
        fila = Bitacora.query.filter_by(accion="usuario_editado", id_entidad=id_usuario).first()
    assert "nombre de Prueba a Nuevo" in fila.detalle and "contraseña cambiada" in fila.detalle
    assert "OtraClave2026Segura" not in fila.detalle


def test_editar_cliente_anota_el_cambio(app, admin):
    with app.app_context():
        cli = Cliente(nombre_cliente="Cliente Aud", documento=None, telefono="111")
        db.session.add(cli)
        db.session.commit()
        id_cliente = cli.id_cliente
    assert admin.post(f"/ventas/clientes/{id_cliente}/editar", data={
        "nombre_cliente": "Cliente Aud", "documento": "", "telefono": "222"}).status_code == 302
    with app.app_context():
        fila = Bitacora.query.filter_by(accion="cliente_editado", id_entidad=id_cliente).first()
    assert "teléfono de 111 a 222" in fila.detalle


def test_comando_de_consola_queda_en_la_bitacora_con_su_usuario(app):
    @auditado("consola_respaldo", "comando de prueba")
    def bien():
        return 1

    with app.app_context():
        antes = Bitacora.query.filter_by(accion="consola_respaldo").count()
        assert bien() == 1
        fila = Bitacora.query.filter_by(accion="consola_respaldo").order_by(Bitacora.id_bitacora.desc()).first()
        assert Bitacora.query.filter_by(accion="consola_respaldo").count() == antes + 1
        assert fila.usuario_login.startswith("consola:") and fila.resultado == "exito"
        assert verificar_bitacora()["ok"]


def test_comando_que_falla_queda_como_fallo_y_la_simulacion_no_se_anota(app):
    @auditado("consola_restauracion", "prueba")
    def malo():
        raise ValueError("x")

    @auditado("consola_cargar_inventario", "prueba simulacion", solo_si="aplicar")
    def simulacion(aplicar=False):
        return "ok"

    with app.app_context():
        antes = Bitacora.query.filter_by(accion="consola_cargar_inventario").count()
        assert simulacion(aplicar=False) == "ok"
        assert Bitacora.query.filter_by(accion="consola_cargar_inventario").count() == antes
        simulacion(aplicar=True)
        assert Bitacora.query.filter_by(accion="consola_cargar_inventario").count() == antes + 1
        with pytest.raises(ValueError):
            malo()
        fila = Bitacora.query.filter_by(accion="consola_restauracion").order_by(Bitacora.id_bitacora.desc()).first()
        assert fila.resultado == "fallo" and "ValueError" in fila.detalle


def test_la_cadena_sigue_integra_tras_las_nuevas_anotaciones(app):
    with app.app_context():
        resultado = verificar_bitacora()
    assert resultado["ok"], resultado["problemas"]
