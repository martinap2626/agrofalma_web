"""
Configuración común de las pruebas automatizadas.

Las pruebas corren contra una base de datos MySQL SEPARADA (agrofalma_test),
que se crea y se vacía sola, para no tocar jamás los datos reales de
agrofalma_bd. Si el nombre de la base de pruebas no termina en "_test", la
suite se niega a correr (protección contra borrar datos reales por error).

Ejecutar:  python -m pytest tests -v
"""

import os

import pymysql
import pytest

from app import create_app
from app.extensions import db
from app.models import Categoria, Cliente, Producto, Rol, Sede, StockPorSede, UnidadMedida, Usuario
from config import Config

# las bases de prueba las crea y respalda la cuenta de administración (la de respaldo del .env solo ve agrofalma_bd)
Config.DB_RESPALDO_USER, Config.DB_RESPALDO_PASSWORD = Config.DB_ADMIN_USER, Config.DB_ADMIN_PASSWORD

NOMBRE_BD_PRUEBAS = os.environ.get("DB_NAME_TEST", "agrofalma_test")
CLAVE_ADMIN = "AdminPrueba2026"
CLAVE_TRABAJADOR = "TrabajoPrueba2026"


class ConfigPruebas(Config):
    TESTING = True
    SECRET_KEY = "clave-de-pruebas-larga-y-aleatoria-0123456789"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{Config.DB_ADMIN_USER}:{Config.DB_ADMIN_PASSWORD}@{Config.DB_HOST}:{Config.DB_PORT}/"
        f"{NOMBRE_BD_PRUEBAS}?charset=utf8mb4"
    )
    LOGIN_MAX_INTENTOS = 5
    LOGIN_VENTANA_MIN = 15
    # sin tope de descuento ni de descargas en la base de las pruebas generales (muchas prueban ventas a precios bajos o
    # descargan varios reportes); las pruebas del control de precios y del límite de descargas fijan sus propios valores
    DESCUENTO_MAX_TRABAJADOR_PCT = 100.0
    EXPORT_MAX_POR_VENTANA = 1000


class ConfigPruebasConCSRF(ConfigPruebas):
    WTF_CSRF_ENABLED = True


# crea la base de datos de pruebas si todavía no existe
def _crear_bd_pruebas():
    assert NOMBRE_BD_PRUEBAS.endswith("_test"), "La base de pruebas debe terminar en _test"
    conexion = pymysql.connect(
        host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_ADMIN_USER, password=Config.DB_ADMIN_PASSWORD
    )
    try:
        with conexion.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{NOMBRE_BD_PRUEBAS}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conexion.commit()
    finally:
        conexion.close()


# siembra lo mínimo para probar: roles, sedes, usuarios, catálogo, stock y un cliente
def _sembrar():
    admin_rol = Rol(nombre_rol="Administrador")
    trab_rol = Rol(nombre_rol="Trabajador")
    sede1 = Sede(nombre_sede="Chiclayo")
    sede2 = Sede(nombre_sede="Pimentel")
    db.session.add_all([admin_rol, trab_rol, sede1, sede2])
    db.session.flush()

    def usuario(login, nombre, rol, sede, clave, estado=True):
        u = Usuario(nombre=nombre, apellido="Prueba", usuario_login=login, id_rol=rol.id_rol,
                    id_sede=sede.id_sede, estado=estado)
        u.set_password(clave)
        db.session.add(u)
        return u

    usuario("admin_t", "Ana", admin_rol, sede1, CLAVE_ADMIN)
    usuario("trab_a", "Beto", trab_rol, sede1, CLAVE_TRABAJADOR)
    usuario("trab_b", "Carla", trab_rol, sede1, CLAVE_TRABAJADOR)
    usuario("inactivo_t", "Dino", trab_rol, sede1, CLAVE_TRABAJADOR, estado=False)
    # cuentas exclusivas de las pruebas de bloqueo, para no dejar bloqueadas a las demás
    usuario("bloqueo_t", "Eli", trab_rol, sede1, CLAVE_TRABAJADOR)
    usuario("reinicio_t", "Fer", trab_rol, sede1, CLAVE_TRABAJADOR)

    categoria = Categoria(nombre_categoria="Foliares")
    unidad = UnidadMedida(nombre_unidad="Litro", abreviatura="L")
    db.session.add_all([categoria, unidad])
    db.session.flush()

    p1 = Producto(codigo_producto="P-001", nombre_producto="Foliar de prueba", id_categoria=categoria.id_categoria,
                  id_unidad=unidad.id_unidad, precio_unitario=50)
    p2 = Producto(codigo_producto="P-002", nombre_producto="Fertilizante de prueba", id_categoria=categoria.id_categoria,
                  id_unidad=unidad.id_unidad, precio_unitario=20)
    db.session.add_all([p1, p2, Cliente(nombre_cliente="Cliente de prueba")])
    db.session.flush()

    db.session.add_all([
        StockPorSede(id_producto=p1.id_producto, id_sede=sede1.id_sede, cantidad_actual=100),
        StockPorSede(id_producto=p2.id_producto, id_sede=sede1.id_sede, cantidad_actual=100),
    ])
    db.session.commit()


@pytest.fixture(scope="session")
def app():
    _crear_bd_pruebas()
    aplicacion = create_app(ConfigPruebas)
    with aplicacion.app_context():
        db.drop_all()
        db.create_all()
        _sembrar()
    yield aplicacion
    with aplicacion.app_context():
        db.session.remove()


@pytest.fixture()
def cliente(app):
    return app.test_client()


@pytest.fixture()
def contexto(app):
    with app.app_context():
        yield


# inicia sesión en el cliente de prueba y devuelve la respuesta
def iniciar_sesion(cliente, usuario, clave, **extra):
    return cliente.post("/login", data={"usuario": usuario, "contrasena": clave}, **extra)


@pytest.fixture()
def admin(app):
    c = app.test_client()
    respuesta = iniciar_sesion(c, "admin_t", CLAVE_ADMIN)
    assert respuesta.status_code == 302
    return c


@pytest.fixture()
def trabajador_a(app):
    c = app.test_client()
    assert iniciar_sesion(c, "trab_a", CLAVE_TRABAJADOR).status_code == 302
    return c


@pytest.fixture()
def trabajador_b(app):
    c = app.test_client()
    assert iniciar_sesion(c, "trab_b", CLAVE_TRABAJADOR).status_code == 302
    return c


# crea una cuenta propia para una prueba (así no se alteran las cuentas compartidas de las demás pruebas)
def crear_usuario(app, login, clave, rol="Trabajador", estado=True):
    with app.app_context():
        rol_fila = Rol.query.filter_by(nombre_rol=rol).first()
        sede = Sede.query.filter_by(nombre_sede="Chiclayo").first()
        usuario = Usuario(nombre="Prueba", apellido=login, usuario_login=login, id_rol=rol_fila.id_rol,
                          id_sede=sede.id_sede, estado=estado)
        usuario.set_password(clave)
        db.session.add(usuario)
        db.session.commit()
        return usuario.id_usuario


# crea un producto propio con su stock en la sede de los trabajadores de prueba; devuelve su id
def crear_producto(app, codigo, precio=50, stock=100, stock_minimo=0):
    with app.app_context():
        categoria = Categoria.query.first()
        unidad = UnidadMedida.query.first()
        sede = Sede.query.filter_by(nombre_sede="Chiclayo").first()
        producto = Producto(codigo_producto=codigo, nombre_producto=f"Producto {codigo}", id_categoria=categoria.id_categoria,
                            id_unidad=unidad.id_unidad, precio_unitario=precio, stock_minimo=stock_minimo)
        db.session.add(producto)
        db.session.flush()
        db.session.add(StockPorSede(id_producto=producto.id_producto, id_sede=sede.id_sede, cantidad_actual=stock))
        db.session.commit()
        return producto.id_producto


# cliente HTTP con la sesión ya iniciada
def sesion_de(app, login, clave):
    c = app.test_client()
    assert iniciar_sesion(c, login, clave).status_code == 302
    return c


# ids de los datos sembrados, para armar formularios de venta
@pytest.fixture()
def datos(app):
    with app.app_context():
        return {
            "cliente": Cliente.query.first().id_cliente,
            "p1": Producto.query.filter_by(codigo_producto="P-001").first().id_producto,
            "p2": Producto.query.filter_by(codigo_producto="P-002").first().id_producto,
            "categoria": Categoria.query.first().id_categoria,
            "unidad": UnidadMedida.query.first().id_unidad,
            "sede": Sede.query.first().id_sede,
            "rol_trabajador": Rol.query.filter_by(nombre_rol="Trabajador").first().id_rol,
        }
