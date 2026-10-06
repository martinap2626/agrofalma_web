"""
Respaldo y recuperación de la base de datos (RNF-06: "La información del
sistema cuenta con respaldo básico"; y fiabilidad: recuperación sin pérdida
de datos).

Comandos:
    flask respaldar-bd        Genera un volcado completo con mysqldump y aplica
                              retención (borra los respaldos más antiguos).
    flask restaurar-bd ARCHIVO --destino NOMBRE_BD
                              Restaura un respaldo en la base indicada.
    flask verificar-respaldo  Prueba automática de recuperación: respalda,
                              restaura en una base temporal, compara fila por
                              fila el conteo de cada tabla y mide el tiempo.

No corre solo: hay que dispararlo, ya sea a mano o mediante un programador
externo -- cron en el servidor de producción (Linux), o el Programador de
tareas de Windows en desarrollo. Flask no programa nada por sí mismo, solo
expone el comando.

Ejemplo de entrada en crontab para respaldar todos los días a las 2 AM
(ejecutar desde el entorno virtual del proyecto, en el servidor):
    0 2 * * * cd /ruta/al/proyecto/agrofalma_web && .venv/bin/flask respaldar-bd >> /var/log/agrofalma_respaldo.log 2>&1

La contraseña de la base de datos NUNCA se pasa como argumento del comando
(se vería en la lista de procesos del sistema): se entrega a mysqldump / mysql
mediante un archivo temporal de credenciales que se borra al terminar.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import click
import pymysql

CARPETA_RESPALDOS = Path(__file__).resolve().parent.parent / "respaldos"
RESPALDOS_A_CONSERVAR = 7  # retencion: se borran los mas antiguos que estos
BD_TEMPORAL_VERIFICACION = "agrofalma_verificacion_tmp"

# sitios habituales donde MySQL deja sus herramientas en Windows
_CARPETAS_WINDOWS = [
    r"C:\Program Files\MySQL\MySQL Server 8.4\bin",
    r"C:\Program Files\MySQL\MySQL Server 8.0\bin",
    r"C:\Program Files\MySQL\MySQL Server 5.7\bin",
    r"C:\Program Files\MariaDB 11.4\bin",
    r"C:\xampp\mysql\bin",
]


def generar_nombre_archivo() -> str:
    marca_tiempo = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"agrofalma_bd_{marca_tiempo}.sql"


# busca mysqldump / mysql: primero la variable de entorno, luego el PATH, luego carpetas típicas de Windows
def localizar_binario(nombre: str) -> str:
    desde_entorno = os.environ.get(f"{nombre.upper()}_RUTA")
    if desde_entorno and Path(desde_entorno).is_file():
        return desde_entorno

    en_path = shutil.which(nombre)
    if en_path:
        return en_path

    for carpeta in _CARPETAS_WINDOWS:
        candidato = Path(carpeta) / f"{nombre}.exe"
        if candidato.is_file():
            return str(candidato)

    raise FileNotFoundError(
        f"No se encontró '{nombre}'. Instálalo, agrégalo al PATH o define la variable de entorno "
        f"{nombre.upper()}_RUTA con su ruta completa."
    )


# crea un archivo temporal de credenciales para que la contraseña no aparezca en los argumentos del proceso
@contextmanager
def archivo_credenciales(config):
    descriptor, ruta = tempfile.mkstemp(suffix=".cnf", prefix="agrofalma_")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as archivo:
            archivo.write("[client]\n")
            archivo.write(f"host={config.DB_HOST}\n")
            archivo.write(f"port={config.DB_PORT}\n")
            archivo.write(f"user={config.DB_USER}\n")
            clave = str(config.DB_PASSWORD).replace("\\", "\\\\").replace('"', '\\"')
            archivo.write(f'password="{clave}"\n')
        yield ruta
    finally:
        Path(ruta).unlink(missing_ok=True)


def ejecutar_mysqldump(config, ruta_destino: Path, nombre_bd: str | None = None) -> None:
    with archivo_credenciales(config) as credenciales:
        comando = [
            localizar_binario("mysqldump"),
            f"--defaults-extra-file={credenciales}",  # debe ir primero
            "--single-transaction",  # respaldo consistente sin bloquear tablas InnoDB
            "--routines",
            "--triggers",
            nombre_bd or config.DB_NAME,
        ]
        try:
            with open(ruta_destino, "w", encoding="utf-8") as archivo_salida:
                resultado = subprocess.run(comando, stdout=archivo_salida, stderr=subprocess.PIPE, text=True)
        except Exception:
            ruta_destino.unlink(missing_ok=True)  # no dejar un .sql vacio/incompleto
            raise

    if resultado.returncode != 0:
        ruta_destino.unlink(missing_ok=True)  # no dejar un .sql vacio/incompleto si fallo
        raise RuntimeError(f"mysqldump falló (código {resultado.returncode}): {resultado.stderr}")

    if not _respaldo_completo(ruta_destino):
        ruta_destino.unlink(missing_ok=True)
        raise RuntimeError("El respaldo quedó incompleto (no termina con la marca 'Dump completed').")


# mysqldump termina siempre con "-- Dump completed on ..."; si falta, el archivo se cortó a la mitad
def _respaldo_completo(ruta: Path) -> bool:
    with open(ruta, "rb") as archivo:
        archivo.seek(0, os.SEEK_END)
        tamano = archivo.tell()
        archivo.seek(max(tamano - 400, 0))
        return b"Dump completed" in archivo.read()


def aplicar_retencion(carpeta: Path, conservar: int) -> list[Path]:
    respaldos = sorted(carpeta.glob("agrofalma_bd_*.sql"), key=lambda p: p.name, reverse=True)
    a_borrar = respaldos[conservar:]
    for ruta in a_borrar:
        ruta.unlink()
    return a_borrar


def _validar_nombre_bd(nombre: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", nombre):
        raise click.ClickException("El nombre de base de datos solo puede tener letras, números y guion bajo.")
    return nombre


def _conectar(config, bd: str | None = None):
    return pymysql.connect(
        host=config.DB_HOST, port=int(config.DB_PORT), user=config.DB_USER,
        password=config.DB_PASSWORD, database=bd, charset="utf8mb4",
    )


# vuelve a crear la base de datos vacía (la borra si existía) -- solo para bases temporales de verificación
def _recrear_bd(config, nombre: str) -> None:
    nombre = _validar_nombre_bd(nombre)
    conexion = _conectar(config)
    try:
        with conexion.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{nombre}`")
            cursor.execute(f"CREATE DATABASE `{nombre}` CHARACTER SET utf8mb4")
        conexion.commit()
    finally:
        conexion.close()


def _eliminar_bd(config, nombre: str) -> None:
    nombre = _validar_nombre_bd(nombre)
    conexion = _conectar(config)
    try:
        with conexion.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{nombre}`")
        conexion.commit()
    finally:
        conexion.close()


def restaurar_respaldo(config, ruta_sql: Path, nombre_bd: str) -> None:
    """Carga un volcado .sql dentro de la base indicada (que debe existir)."""
    with archivo_credenciales(config) as credenciales:
        comando = [
            localizar_binario("mysql"),
            f"--defaults-extra-file={credenciales}",
            "--default-character-set=utf8mb4",
            _validar_nombre_bd(nombre_bd),
        ]
        with open(ruta_sql, "rb") as entrada:
            resultado = subprocess.run(comando, stdin=entrada, stderr=subprocess.PIPE, text=True)
    if resultado.returncode != 0:
        raise RuntimeError(f"La restauración falló (código {resultado.returncode}): {resultado.stderr}")


# {tabla: filas} de una base de datos
def contar_filas(config, nombre_bd: str) -> dict[str, int]:
    conexion = _conectar(config, nombre_bd)
    try:
        with conexion.cursor() as cursor:
            cursor.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
            tablas = [fila[0] for fila in cursor.fetchall()]
            conteos = {}
            for tabla in tablas:
                cursor.execute(f"SELECT COUNT(*) FROM `{tabla}`")
                conteos[tabla] = cursor.fetchone()[0]
            return conteos
    finally:
        conexion.close()


def registrar_comando_respaldo(app) -> None:
    @app.cli.command("respaldar-bd")
    @click.option(
        "--conservar", default=RESPALDOS_A_CONSERVAR,
        help=f"Cuántos respaldos recientes conservar (default: {RESPALDOS_A_CONSERVAR}).",
    )
    def respaldar_bd(conservar):
        """Genera un volcado completo de la base de datos con mysqldump y
        aplica retención (borra los respaldos más antiguos que el límite)."""
        from config import Config

        CARPETA_RESPALDOS.mkdir(exist_ok=True)
        ruta_destino = CARPETA_RESPALDOS / generar_nombre_archivo()

        click.echo(f"Generando respaldo en {ruta_destino} ...")
        try:
            ejecutar_mysqldump(Config, ruta_destino)
        except (RuntimeError, FileNotFoundError) as error:
            click.echo(f"ERROR: no se pudo generar el respaldo -- {error}", err=True)
            raise SystemExit(1)

        tamano_mb = ruta_destino.stat().st_size / (1024 * 1024)
        click.echo(f"Respaldo generado correctamente ({tamano_mb:.2f} MB).")

        borrados = aplicar_retencion(CARPETA_RESPALDOS, conservar)
        if borrados:
            click.echo(f"Retención: se eliminaron {len(borrados)} respaldo(s) antiguos, "
                       f"se conservan los últimos {conservar}.")

    @app.cli.command("restaurar-bd")
    @click.argument("archivo", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option("--destino", required=True, help="Base de datos donde restaurar (se crea si no existe).")
    @click.option("--sobrescribir", is_flag=True,
                  help="Obligatorio para restaurar encima de la base de datos en uso de la aplicación.")
    def restaurar_bd(archivo, destino, sobrescribir):
        """Restaura un respaldo .sql. Por seguridad, no toca la base en uso
        salvo que se pida explícitamente con --sobrescribir."""
        from config import Config

        _validar_nombre_bd(destino)
        if destino == Config.DB_NAME and not sobrescribir:
            raise click.ClickException(
                f"'{destino}' es la base de datos en uso. Restaura en otra (--destino otra_bd) "
                "o repite el comando con --sobrescribir si de verdad quieres reemplazar sus datos."
            )

        conexion = _conectar(Config)
        try:
            with conexion.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{destino}` CHARACTER SET utf8mb4")
            conexion.commit()
        finally:
            conexion.close()

        inicio = time.perf_counter()
        try:
            restaurar_respaldo(Config, archivo, destino)
        except (RuntimeError, FileNotFoundError) as error:
            click.echo(f"ERROR: {error}", err=True)
            raise SystemExit(1)
        click.echo(f"Respaldo restaurado en '{destino}' en {time.perf_counter() - inicio:.1f} s.")

    @app.cli.command("verificar-respaldo")
    def verificar_respaldo():
        """Prueba de recuperación de extremo a extremo: respalda la base en
        uso, restaura en una base temporal, compara el conteo de filas de cada
        tabla y mide el tiempo. No modifica la base de datos en uso."""
        from config import Config

        CARPETA_RESPALDOS.mkdir(exist_ok=True)
        ruta_sql = CARPETA_RESPALDOS / f"verificacion_{generar_nombre_archivo()}"

        try:
            t0 = time.perf_counter()
            click.echo("1/4 Generando respaldo ...")
            ejecutar_mysqldump(Config, ruta_sql)
            t_respaldo = time.perf_counter() - t0
            tamano_mb = ruta_sql.stat().st_size / (1024 * 1024)

            click.echo(f"2/4 Restaurando en la base temporal '{BD_TEMPORAL_VERIFICACION}' ...")
            _recrear_bd(Config, BD_TEMPORAL_VERIFICACION)
            t1 = time.perf_counter()
            restaurar_respaldo(Config, ruta_sql, BD_TEMPORAL_VERIFICACION)
            t_restauracion = time.perf_counter() - t1

            click.echo("3/4 Comparando tabla por tabla ...")
            origen = contar_filas(Config, Config.DB_NAME)
            restaurada = contar_filas(Config, BD_TEMPORAL_VERIFICACION)
        except (RuntimeError, FileNotFoundError) as error:
            click.echo(f"ERROR: {error}", err=True)
            raise SystemExit(1)
        finally:
            click.echo("4/4 Limpiando archivos y base temporal ...")
            ruta_sql.unlink(missing_ok=True)
            _eliminar_bd(Config, BD_TEMPORAL_VERIFICACION)

        diferencias = []
        click.echo(f"\n{'Tabla':<36}{'Origen':>10}{'Restaurada':>12}  Estado")
        for tabla in sorted(set(origen) | set(restaurada)):
            a, b = origen.get(tabla), restaurada.get(tabla)
            ok = a == b
            if not ok:
                diferencias.append(tabla)
            click.echo(f"{tabla:<36}{str(a):>10}{str(b):>12}  {'OK' if ok else 'DIFERENTE'}")

        total = sum(origen.values())
        click.echo(
            f"\nTablas: {len(origen)} · Filas totales: {total} · Tamaño del respaldo: {tamano_mb:.2f} MB\n"
            f"Tiempo de respaldo: {t_respaldo:.1f} s · Tiempo de restauración: {t_restauracion:.1f} s"
        )
        if diferencias:
            click.echo(f"RESULTADO: FALLÓ. Tablas con diferencias: {', '.join(diferencias)}", err=True)
            raise SystemExit(1)
        click.echo("RESULTADO: CORRECTO. La recuperación reproduce todas las tablas sin pérdida de datos.")
