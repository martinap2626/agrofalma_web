"""
Prueba real de `flask permisos-bd`: crea en MySQL un usuario con los permisos mínimos que el comando genera (sobre una base
temporal) y comprueba que el sistema funciona con él pero que ese usuario NO puede editar ni borrar la bitácora ni el
kárdex, ni crear, modificar o borrar tablas. Se omite si la cuenta de pruebas no puede crear usuarios de MySQL.
"""

import pymysql
import pytest

from app import create_app
from app.extensions import db
from config import Config
from conftest import CLAVE_ADMIN, ConfigPruebas, _sembrar, iniciar_sesion

BD = "agrofalma_permisos_test"
USUARIO = "agrofalma_app_perm_t"
CLAVE = "ClaveTemporalDePermisos2026"


def conexion_admin():
    return pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_USER, password=Config.DB_PASSWORD, autocommit=True)


@pytest.fixture(scope="module")
def entorno():
    try:
        admin = conexion_admin()
        with admin.cursor() as c:
            c.execute(f"DROP DATABASE IF EXISTS `{BD}`")
            c.execute(f"CREATE DATABASE `{BD}` CHARACTER SET utf8mb4")
            c.execute(f"DROP USER IF EXISTS '{USUARIO}'@'localhost'")
    except pymysql.err.Error:
        pytest.skip("la cuenta de pruebas no puede crear bases ni usuarios de MySQL")

    def configuracion(usuario, clave):
        return type("ConfigTemporal", (ConfigPruebas,), {
            "DB_NAME": BD,
            "SQLALCHEMY_DATABASE_URI": f"mysql+pymysql://{usuario}:{clave}@{Config.DB_HOST}:{Config.DB_PORT}/{BD}?charset=utf8mb4",
        })

    app_admin = create_app(configuracion(Config.DB_USER, Config.DB_PASSWORD))
    with app_admin.app_context():
        db.create_all()
        _sembrar()
        sql = app_admin.test_cli_runner().invoke(args=["permisos-bd", "--usuario", USUARIO]).output
    sql = sql.replace("CAMBIAR_ESTA_CLAVE_POR_UNA_LARGA", CLAVE).replace("`agrofalma_bd`", f"`{BD}`")
    sentencias = "\n".join(l for l in sql.splitlines() if not l.startswith("--"))
    with admin.cursor() as c:
        for sentencia in [s.strip() for s in sentencias.split(";") if s.strip()]:
            c.execute(sentencia)
    yield configuracion(USUARIO, CLAVE)
    with admin.cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS `{BD}`")
        c.execute(f"DROP USER IF EXISTS '{USUARIO}'@'localhost'")
    admin.close()


@pytest.fixture()
def limitado(entorno):
    c = pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=USUARIO, password=CLAVE, database=BD, autocommit=True)
    yield c
    c.close()


def intentar(conexion, sentencia):
    try:
        with conexion.cursor() as cur:
            cur.execute(sentencia)
        return True
    except pymysql.err.Error:
        return False


@pytest.mark.parametrize("sentencia", [
    "UPDATE bitacora_acciones SET accion = 'falsa'",
    "DELETE FROM bitacora_acciones",
    "TRUNCATE TABLE bitacora_acciones",
    "UPDATE movimientos_inventario SET cantidad = 0",
    "DELETE FROM movimientos_inventario",
    "DELETE FROM bitacora_cadena",
    "DROP TABLE clientes",
    "CREATE TABLE intruso (id INT)",
    "ALTER TABLE clientes ADD COLUMN extra INT",
    "SELECT user FROM mysql.user",
    "CREATE USER 'otro'@'localhost'",
])
def test_el_usuario_de_la_aplicacion_no_puede_alterar_la_bitacora_ni_la_estructura(limitado, sentencia):
    assert not intentar(limitado, sentencia), f"MySQL no debió permitir: {sentencia}"


@pytest.mark.parametrize("sentencia", [
    "INSERT INTO bitacora_acciones (fecha_hora, accion, resultado) VALUES (NOW(), 'prueba', 'exito')",
    "SELECT COUNT(*) FROM bitacora_acciones",
    "UPDATE bitacora_cadena SET total = total",
    "INSERT INTO clientes (nombre_cliente, estado) VALUES ('X', 1)",
    "UPDATE clientes SET nombre_cliente = 'Y' WHERE nombre_cliente = 'X'",
    "DELETE FROM clientes WHERE nombre_cliente = 'Y'",
])
def test_el_usuario_de_la_aplicacion_si_puede_hacer_su_trabajo(limitado, sentencia):
    assert intentar(limitado, sentencia), f"MySQL debió permitir: {sentencia}"


def test_con_la_estructura_al_dia_la_analitica_no_necesita_permisos_de_administracion(entorno):
    # la actualización mensual de la analítica revisa la estructura antes de empezar: con la base al día no debe intentar
    # ningún cambio de estructura (que el usuario limitado no podría hacer)
    from app.esquema import asegurar_esquema_plataforma, esquema_pendiente
    from app.servicios_analitica import asegurar_esquema

    app = create_app(entorno)
    with app.app_context():
        assert asegurar_esquema() == []
        assert esquema_pendiente() == []
        assert asegurar_esquema_plataforma() == []


def test_el_sistema_completo_funciona_con_ese_usuario_limitado(entorno):
    app = create_app(entorno)
    c = app.test_client()
    assert iniciar_sesion(c, "admin_t", CLAVE_ADMIN).status_code == 302
    with app.app_context():
        from app.models import Cliente, Producto

        cliente, producto = Cliente.query.first().id_cliente, Producto.query.first().id_producto
    venta = c.post("/ventas/nueva", data={"id_cliente": cliente, "tipo_venta": "contado", "id_producto[]": [producto],
                                          "cantidad[]": ["1"], "precio_unitario[]": ["50"]})
    assert venta.status_code == 302
    id_venta = venta.headers["Location"].rstrip("/").split("/")[-1]
    assert "íntegra" in c.post("/auditoria/verificar", follow_redirects=True).get_data(as_text=True)
    anulada = c.post(f"/ventas/{id_venta}/anular", data={"motivo": "Prueba con usuario limitado"}, follow_redirects=True)
    assert "anulada" in anulada.get_data(as_text=True).lower()
