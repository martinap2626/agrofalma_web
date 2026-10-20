"""
Pruebas de las migraciones del esquema (Alembic / Flask-Migrate):

* las migraciones crean una base completa desde cero y esa base coincide con los modelos del código (si alguien cambia un
  modelo y olvida su migración, esta prueba falla);
* se puede subir y bajar sin romper;
* `flask preparar-bd` pone al día una base anterior a las migraciones y la deja bajo control de versiones.
"""

import pymysql
import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect

from app import CARPETA_MIGRACIONES, create_app
from app.extensions import db
from app.migraciones import REVISION_INICIAL, estado_migraciones, revision_actual, revision_mas_reciente
from config import Config
from conftest import ConfigPruebas

BD = "agrofalma_migraciones_test"


def conexion_admin():
    return pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_ADMIN_USER, password=Config.DB_ADMIN_PASSWORD, autocommit=True)


def recrear_bd():
    with conexion_admin().cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS `{BD}`")
        c.execute(f"CREATE DATABASE `{BD}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")


@pytest.fixture()
def app_vacia():
    """Una aplicación conectada a una base de datos recién creada y vacía."""
    try:
        recrear_bd()
    except pymysql.err.Error:
        pytest.skip("la cuenta de pruebas no puede crear bases de datos")
    config = type("ConfigMigraciones", (ConfigPruebas,), {
        "SQLALCHEMY_DATABASE_URI": f"mysql+pymysql://{Config.DB_ADMIN_USER}:{Config.DB_ADMIN_PASSWORD}@{Config.DB_HOST}:{Config.DB_PORT}/{BD}?charset=utf8mb4",
    })
    aplicacion = create_app(config)
    yield aplicacion
    with aplicacion.app_context():
        db.session.remove()
        db.engine.dispose()
    with conexion_admin().cursor() as c:
        c.execute(f"DROP DATABASE IF EXISTS `{BD}`")


def diferencias_con_los_modelos():
    with db.engine.connect() as conexion:
        return compare_metadata(MigrationContext.configure(conexion), db.metadata)


def test_hay_una_sola_revision_inicial_y_la_carpeta_existe():
    assert CARPETA_MIGRACIONES.is_dir() and (CARPETA_MIGRACIONES / "env.py").exists()
    assert REVISION_INICIAL == "0001" and revision_mas_reciente() is not None


def test_las_migraciones_crean_la_base_completa_y_coincide_con_los_modelos(app_vacia):
    with app_vacia.app_context():
        assert inspect(db.engine).get_table_names() == []
        upgrade(directory=str(CARPETA_MIGRACIONES), revision="head")
        tablas = set(inspect(db.engine).get_table_names())
        assert {"usuarios", "ventas", "stock_por_sede", "bitacora_acciones", "bitacora_cadena", "movimientos_inventario", "alembic_version"} <= tablas
        assert len(tablas) == len(db.metadata.tables) + 1  # los modelos + la tabla de control de versiones
        assert revision_actual() == revision_mas_reciente()
        # ningún modelo cambió sin su migración: la base creada por migraciones es igual a la que describen los modelos
        assert diferencias_con_los_modelos() == [], diferencias_con_los_modelos()


def test_se_puede_bajar_y_volver_a_subir(app_vacia):
    with app_vacia.app_context():
        upgrade(directory=str(CARPETA_MIGRACIONES), revision="head")
        downgrade(directory=str(CARPETA_MIGRACIONES), revision="base")
        assert inspect(db.engine).get_table_names() == ["alembic_version"]
        upgrade(directory=str(CARPETA_MIGRACIONES), revision="head")
        assert diferencias_con_los_modelos() == []


def test_preparar_bd_deja_una_base_nueva_bajo_control_de_versiones_y_es_repetible(app_vacia):
    ejecutor = app_vacia.test_cli_runner()
    r = ejecutor.invoke(args=["preparar-bd"])
    assert r.exit_code == 0, r.output
    assert "control de versiones del esquema iniciado" in r.output
    with app_vacia.app_context():
        assert revision_actual() == revision_mas_reciente()
        assert estado_migraciones()[0] == "al_dia"
        assert diferencias_con_los_modelos() == []
    segunda = ejecutor.invoke(args=["preparar-bd"])
    assert segunda.exit_code == 0 and "control de versiones" not in segunda.output  # ya estaba marcada


def test_una_base_anterior_a_las_migraciones_se_pone_al_dia_y_se_marca(app_vacia):
    from sqlalchemy import text

    with app_vacia.app_context():
        db.create_all()  # una base con el esquema actual pero sin control de versiones
        # se simula una base ANTIGUA quitando lo que las migraciones y las funciones de esquema agregaron después
        for sentencia in (
            "ALTER TABLE ventas DROP COLUMN anulada", "ALTER TABLE ventas DROP COLUMN motivo_anulacion",
            "ALTER TABLE usuarios DROP COLUMN version_sesion",
        ):
            db.session.execute(text(sentencia))
        db.session.commit()
        assert estado_migraciones()[0] == "sin_control"
    r = app_vacia.test_cli_runner().invoke(args=["preparar-bd"])
    assert r.exit_code == 0, r.output
    assert "columna ventas.anulada" in r.output and "columna usuarios.version_sesion" in r.output
    with app_vacia.app_context():
        assert estado_migraciones()[0] == "al_dia"
        assert diferencias_con_los_modelos() == []


def test_verificar_despliegue_avisa_si_a_la_base_le_faltan_migraciones(app_vacia):
    from app.comandos_despliegue import AVISO, FALLO, OK, diagnosticar

    with app_vacia.app_context():
        db.create_all()
        niveles = {tema: nivel for nivel, tema, _ in diagnosticar(app_vacia)}
        assert niveles["Migraciones"] == AVISO  # sin control de versiones todavía
        from flask_migrate import stamp

        stamp(directory=str(CARPETA_MIGRACIONES), revision=revision_mas_reciente())
        assert {tema: nivel for nivel, tema, _ in diagnosticar(app_vacia)}["Migraciones"] == OK

        def marcar(revision):
            db.session.rollback()  # libera la transacción abierta por el diagnóstico antes de tocar otra tabla
            with db.engine.begin() as conexion:
                conexion.execute(db.text("UPDATE alembic_version SET version_num = :v"), {"v": revision})

        marcar("0000")  # una revisión desconocida o anterior a la última se considera atrasada
        try:
            niveles = {tema: nivel for nivel, tema, _ in diagnosticar(app_vacia)}
            assert niveles["Migraciones"] == FALLO
        finally:
            marcar(revision_mas_reciente())
