"""
Pruebas del respaldo y la recuperación (RNF-06 y fiabilidad): el respaldo se
genera completo, la restauración reproduce los datos sin pérdida, la retención
borra solo lo antiguo y la contraseña de la base nunca viaja en la línea de
comandos.
"""

import subprocess

import pytest

from app.comandos_respaldo import (
    _eliminar_bd,
    _recrear_bd,
    aplicar_retencion,
    contar_filas,
    ejecutar_mysqldump,
    localizar_binario,
    restaurar_respaldo,
)
from conftest import NOMBRE_BD_PRUEBAS, ConfigPruebas

BD_DESTINO = "agrofalma_restauracion_test"


@pytest.fixture(scope="module")
def binarios():
    try:
        localizar_binario("mysqldump")
        localizar_binario("mysql")
    except FileNotFoundError:
        pytest.skip("mysqldump/mysql no están disponibles en este equipo")


def test_respaldo_y_restauracion_reproducen_todas_las_tablas(app, tmp_path, binarios):
    class Config(ConfigPruebas):
        DB_NAME = NOMBRE_BD_PRUEBAS

    ruta = tmp_path / "respaldo.sql"
    ejecutar_mysqldump(Config, ruta, NOMBRE_BD_PRUEBAS)
    assert ruta.stat().st_size > 0

    _recrear_bd(Config, BD_DESTINO)
    try:
        restaurar_respaldo(Config, ruta, BD_DESTINO)
        origen = contar_filas(Config, NOMBRE_BD_PRUEBAS)
        restaurada = contar_filas(Config, BD_DESTINO)
    finally:
        _eliminar_bd(Config, BD_DESTINO)

    assert origen == restaurada
    assert origen["usuarios"] >= 4 and origen["bitacora_acciones"] >= 0


def test_la_contrasena_no_aparece_en_los_argumentos_del_comando(tmp_path, monkeypatch, binarios):
    capturado = {}

    def falso_run(comando, **kwargs):
        capturado["comando"] = comando
        kwargs["stdout"].write("-- Dump completed on 2026-01-01\n")
        return subprocess.CompletedProcess(comando, 0, stderr="")

    class Config(ConfigPruebas):
        DB_PASSWORD = "SECRETO-NO-DEBE-VERSE"

    monkeypatch.setattr("app.comandos_respaldo.subprocess.run", falso_run)
    ejecutar_mysqldump(Config, tmp_path / "x.sql", NOMBRE_BD_PRUEBAS)

    assert not any("SECRETO-NO-DEBE-VERSE" in parte for parte in capturado["comando"])
    assert capturado["comando"][1].startswith("--defaults-extra-file=")


def test_un_respaldo_cortado_se_detecta_y_no_se_conserva(tmp_path, monkeypatch, binarios):
    def run_incompleto(comando, **kwargs):
        kwargs["stdout"].write("CREATE TABLE a (...")  # sin la marca final
        return subprocess.CompletedProcess(comando, 0, stderr="")

    monkeypatch.setattr("app.comandos_respaldo.subprocess.run", run_incompleto)
    ruta = tmp_path / "cortado.sql"
    with pytest.raises(RuntimeError, match="incompleto"):
        ejecutar_mysqldump(ConfigPruebas, ruta, NOMBRE_BD_PRUEBAS)
    assert not ruta.exists()


def test_si_mysqldump_falla_no_queda_archivo_a_medias(tmp_path, monkeypatch, binarios):
    monkeypatch.setattr(
        "app.comandos_respaldo.subprocess.run",
        lambda comando, **kw: subprocess.CompletedProcess(comando, 2, stderr="Access denied"),
    )
    ruta = tmp_path / "fallido.sql"
    with pytest.raises(RuntimeError, match="Access denied"):
        ejecutar_mysqldump(ConfigPruebas, ruta, NOMBRE_BD_PRUEBAS)
    assert not ruta.exists()


def test_la_retencion_borra_solo_los_mas_antiguos(tmp_path):
    for dia in range(1, 11):
        (tmp_path / f"agrofalma_bd_202601{dia:02d}_020000.sql").write_text("x")
    (tmp_path / "otro_archivo.sql").write_text("no es un respaldo")

    borrados = aplicar_retencion(tmp_path, 7)

    assert len(borrados) == 3
    restantes = sorted(p.name for p in tmp_path.glob("agrofalma_bd_*.sql"))
    assert restantes[0] == "agrofalma_bd_20260104_020000.sql"  # los 3 más viejos se fueron
    assert (tmp_path / "otro_archivo.sql").exists()  # lo que no es respaldo no se toca


def test_nombre_de_bd_peligroso_es_rechazado(app):
    import click

    with pytest.raises(click.ClickException):
        _recrear_bd(ConfigPruebas, "x`; DROP DATABASE agrofalma_bd; --")
