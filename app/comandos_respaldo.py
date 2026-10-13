"""
Respaldo y recuperación de la base de datos (RNF-06: "La información del
sistema cuenta con respaldo básico"; y fiabilidad: recuperación sin pérdida
de datos).

Comandos:
    flask respaldar-bd        Genera un volcado completo con mysqldump, lo comprime
                              y lo cifra (si hay RESPALDO_CLAVE), aplica retención
                              (borra los respaldos más antiguos) y deja una copia
                              verificada fuera del equipo (si hay RESPALDO_COPIA_DIR).
    flask restaurar-bd ARCHIVO --destino NOMBRE_BD
                              Restaura un respaldo (.sql, .sql.gz o .sql.gz.enc)
                              en la base indicada.
    flask verificar-respaldo  Prueba automática de recuperación: respalda,
                              restaura en una base temporal, compara fila por
                              fila el conteo de cada tabla y mide el tiempo.

Objetivos de recuperación (ver deploy/POLITICA_RESPALDO.md):
    RPO (cuántos datos se pueden perder):  24 horas, con un respaldo diario.
    RTO (cuánto tarda volver a funcionar): la restauración de la base toma segundos
    (se mide en cada `flask verificar-respaldo`); con la instalación del servicio, menos de 1 hora.

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

import base64
import gzip
import hashlib
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
from cryptography.fernet import Fernet, InvalidToken
from app.seguridad import auditado

CARPETA_RESPALDOS = Path(__file__).resolve().parent.parent / "respaldos"
RESPALDOS_A_CONSERVAR = 7  # retencion: se borran los mas antiguos que estos
RESPALDOS_EXTERNOS_A_CONSERVAR = 30  # la copia fuera del equipo guarda más historia (un mes de respaldos diarios)
BD_TEMPORAL_VERIFICACION = "agrofalma_verificacion_tmp"

# formatos de respaldo: .sql (volcado plano), .sql.gz (comprimido) y .sql.gz.enc (comprimido y cifrado)
EXTENSIONES_RESPALDO = (".sql.gz.enc", ".sql.gz", ".sql")
CABECERA_CIFRADO = b"AGFB1"  # marca y versión del formato cifrado
TAMANO_BLOQUE = 1024 * 1024

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


# respaldos de una carpeta (en cualquiera de los tres formatos), del más reciente al más antiguo
def listar_respaldos(carpeta: Path) -> list[Path]:
    if not carpeta.exists():
        return []
    encontrados = [p for p in carpeta.glob("agrofalma_bd_*") if p.name.endswith(EXTENSIONES_RESPALDO)]
    return sorted(encontrados, key=lambda p: p.name, reverse=True)


def aplicar_retencion(carpeta: Path, conservar: int) -> list[Path]:
    a_borrar = listar_respaldos(carpeta)[conservar:]
    for ruta in a_borrar:
        ruta.unlink()
    return a_borrar


# ---------------------------------------------------------------------------
# Compresión y cifrado del respaldo
# ---------------------------------------------------------------------------
# convierte la clave escrita por el usuario en una clave de cifrado; scrypt es costoso a propósito (frena adivinarla a fuerza bruta)
def _clave_de_cifrado(clave: str, sal: bytes) -> bytes:
    return base64.urlsafe_b64encode(hashlib.scrypt(clave.encode("utf-8"), salt=sal, n=2**14, r=8, p=1, dklen=32))


def empaquetar_respaldo(ruta_sql: Path, clave: str | None) -> Path:
    """
    Comprime el volcado .sql (ocupa una décima parte) y, si hay clave, lo cifra (AES-128 con firma, vía Fernet, por bloques de
    1 MB). Borra el .sql plano y devuelve la ruta final. Un respaldo cifrado no sirve de nada a quien se lleve el archivo
    sin la clave, y cualquier alteración del archivo se detecta al restaurarlo.
    """
    ruta_gz = ruta_sql.with_name(ruta_sql.name + ".gz")
    ruta_final = ruta_gz.with_name(ruta_gz.name + ".enc") if clave else ruta_gz
    try:
        with open(ruta_sql, "rb") as entrada, gzip.open(ruta_gz, "wb", compresslevel=6) as salida:
            shutil.copyfileobj(entrada, salida, TAMANO_BLOQUE)
        if clave:
            sal = os.urandom(16)
            fernet = Fernet(_clave_de_cifrado(clave, sal))
            with open(ruta_gz, "rb") as entrada, open(ruta_final, "wb") as salida:
                salida.write(CABECERA_CIFRADO + sal)
                while bloque := entrada.read(TAMANO_BLOQUE):
                    token = fernet.encrypt(bloque)
                    salida.write(len(token).to_bytes(4, "big"))
                    salida.write(token)
            ruta_gz.unlink()
    except Exception:
        ruta_gz.unlink(missing_ok=True)
        ruta_final.unlink(missing_ok=True)  # no dejar un respaldo a medias
        raise
    ruta_sql.unlink()
    return ruta_final


@contextmanager
def abrir_respaldo(ruta: Path, clave: str | None):
    """
    Entrega la ruta de un .sql plano listo para restaurar, sea cual sea el formato del respaldo (.sql, .sql.gz o
    .sql.gz.enc). Si hubo que descifrar o descomprimir, el archivo temporal se borra al terminar.
    """
    if ruta.name.endswith(".sql"):
        yield ruta
        return
    carpeta = Path(tempfile.mkdtemp(prefix="agrofalma_restauracion_"))
    try:
        origen_gz = ruta
        if ruta.name.endswith(".enc"):
            if not clave:
                raise RuntimeError("El respaldo está cifrado: define la clave en la variable de entorno RESPALDO_CLAVE.")
            origen_gz = carpeta / "descifrado.sql.gz"
            with open(ruta, "rb") as entrada, open(origen_gz, "wb") as salida:
                if entrada.read(len(CABECERA_CIFRADO)) != CABECERA_CIFRADO:
                    raise RuntimeError("El archivo no tiene el formato de respaldo cifrado de Agrofalma.")
                fernet = Fernet(_clave_de_cifrado(clave, entrada.read(16)))
                while longitud := entrada.read(4):
                    try:
                        salida.write(fernet.decrypt(entrada.read(int.from_bytes(longitud, "big"))))
                    except InvalidToken as error:
                        raise RuntimeError("No se pudo descifrar: la clave es incorrecta o el respaldo está dañado.") from error
        sql = carpeta / "respaldo.sql"
        with gzip.open(origen_gz, "rb") as entrada, open(sql, "wb") as salida:
            shutil.copyfileobj(entrada, salida, TAMANO_BLOQUE)
        yield sql
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


# huella SHA-256 del archivo, para anotarla y poder comprobar después que no cambió
def huella_archivo(ruta: Path) -> str:
    resumen = hashlib.sha256()
    with open(ruta, "rb") as archivo:
        while bloque := archivo.read(TAMANO_BLOQUE):
            resumen.update(bloque)
    return resumen.hexdigest()


def copiar_fuera_del_equipo(ruta: Path, carpeta_externa: Path, conservar: int) -> Path:
    """
    Copia el respaldo a una carpeta que NO está en este disco (disco externo, carpeta sincronizada con la nube, unidad de
    red) y comprueba que la copia quedó idéntica. Si el equipo se pierde, se quema o lo encripta un virus, esa copia
    sigue viva. Aplica su propia retención (por defecto, 30 respaldos).
    """
    carpeta_externa.mkdir(parents=True, exist_ok=True)
    destino = carpeta_externa / ruta.name
    shutil.copy2(ruta, destino)
    if huella_archivo(destino) != huella_archivo(ruta):
        destino.unlink(missing_ok=True)
        raise RuntimeError(f"La copia en {carpeta_externa} no quedó idéntica al original; se descartó.")
    aplicar_retencion(carpeta_externa, conservar)
    return destino


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
    @click.option(
        "--conservar-externo", default=RESPALDOS_EXTERNOS_A_CONSERVAR,
        help=f"Cuántos respaldos conservar en la copia fuera del equipo (default: {RESPALDOS_EXTERNOS_A_CONSERVAR}).",
    )
    @auditado("consola_respaldo", "flask respaldar-bd")
    def respaldar_bd(conservar, conservar_externo):
        """Genera un volcado completo de la base de datos con mysqldump, lo comprime y lo cifra (si hay clave
        RESPALDO_CLAVE), aplica retención y deja una copia fuera del equipo (si hay RESPALDO_COPIA_DIR)."""
        from config import Config

        CARPETA_RESPALDOS.mkdir(exist_ok=True)
        ruta_sql = CARPETA_RESPALDOS / generar_nombre_archivo()
        clave = Config.RESPALDO_CLAVE or None

        click.echo(f"Generando respaldo en {ruta_sql.with_suffix('')} ...")
        try:
            ejecutar_mysqldump(Config, ruta_sql)
            tamano_sql_mb = ruta_sql.stat().st_size / (1024 * 1024)
            ruta_destino = empaquetar_respaldo(ruta_sql, clave)
        except (RuntimeError, FileNotFoundError, OSError) as error:
            click.echo(f"ERROR: no se pudo generar el respaldo -- {error}", err=True)
            raise SystemExit(1)

        tamano_mb = ruta_destino.stat().st_size / (1024 * 1024)
        click.echo(f"Respaldo generado correctamente: {ruta_destino.name} ({tamano_mb:.2f} MB; sin comprimir {tamano_sql_mb:.2f} MB).")
        click.echo(f"Huella SHA-256: {huella_archivo(ruta_destino)}")
        if clave:
            click.echo("Cifrado: sí (AES). Sin la clave RESPALDO_CLAVE este archivo no se puede restaurar: guárdala en un lugar seguro.")
        else:
            click.echo("AVISO: el respaldo NO está cifrado. Define RESPALDO_CLAVE en el archivo .env para cifrarlo.", err=True)

        borrados = aplicar_retencion(CARPETA_RESPALDOS, conservar)
        if borrados:
            click.echo(f"Retención: se eliminaron {len(borrados)} respaldo(s) antiguos, "
                       f"se conservan los últimos {conservar}.")

        if Config.RESPALDO_COPIA_DIR:
            try:
                copia = copiar_fuera_del_equipo(ruta_destino, Path(Config.RESPALDO_COPIA_DIR), conservar_externo)
            except (RuntimeError, OSError) as error:
                click.echo(f"ERROR: el respaldo local quedó bien, pero NO se pudo copiar fuera del equipo -- {error}", err=True)
                raise SystemExit(2)
            click.echo(f"Copia fuera del equipo verificada: {copia}")
        else:
            click.echo("AVISO: no hay copia fuera del equipo. Define RESPALDO_COPIA_DIR (disco externo, carpeta de la nube o unidad de red).", err=True)

    @app.cli.command("restaurar-bd")
    @click.argument("archivo", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option("--destino", required=True, help="Base de datos donde restaurar (se crea si no existe).")
    @click.option("--sobrescribir", is_flag=True,
                  help="Obligatorio para restaurar encima de la base de datos en uso de la aplicación.")
    @auditado("consola_restauracion", "flask restaurar-bd")
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

        inicio = time.perf_counter()
        try:
            # acepta .sql, .sql.gz y .sql.gz.enc (este último necesita RESPALDO_CLAVE). Primero se abre el respaldo y solo
            # entonces se crea la base de destino: si la clave es incorrecta o el archivo está dañado, no queda una base vacía a medias
            with abrir_respaldo(archivo, Config.RESPALDO_CLAVE or None) as ruta_sql:
                conexion = _conectar(Config)
                try:
                    with conexion.cursor() as cursor:
                        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{destino}` CHARACTER SET utf8mb4")
                    conexion.commit()
                finally:
                    conexion.close()
                restaurar_respaldo(Config, ruta_sql, destino)
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
        clave = Config.RESPALDO_CLAVE or None
        paquete = None

        try:
            t0 = time.perf_counter()
            click.echo("1/4 Generando respaldo (comprimido" + (" y cifrado" if clave else ", SIN cifrar") + ") ...")
            ejecutar_mysqldump(Config, ruta_sql)
            tamano_sql_mb = ruta_sql.stat().st_size / (1024 * 1024)
            paquete = empaquetar_respaldo(ruta_sql, clave)
            t_respaldo = time.perf_counter() - t0
            tamano_mb = paquete.stat().st_size / (1024 * 1024)

            click.echo(f"2/4 Restaurando el respaldo empaquetado en la base temporal '{BD_TEMPORAL_VERIFICACION}' ...")
            _recrear_bd(Config, BD_TEMPORAL_VERIFICACION)
            t1 = time.perf_counter()
            with abrir_respaldo(paquete, clave) as ruta_restauracion:
                restaurar_respaldo(Config, ruta_restauracion, BD_TEMPORAL_VERIFICACION)
            t_restauracion = time.perf_counter() - t1

            click.echo("3/4 Comparando tabla por tabla ...")
            origen = contar_filas(Config, Config.DB_NAME)
            restaurada = contar_filas(Config, BD_TEMPORAL_VERIFICACION)
        except (RuntimeError, FileNotFoundError, OSError) as error:
            click.echo(f"ERROR: {error}", err=True)
            raise SystemExit(1)
        finally:
            click.echo("4/4 Limpiando archivos y base temporal ...")
            ruta_sql.unlink(missing_ok=True)
            if paquete is not None:
                paquete.unlink(missing_ok=True)
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
            f"\nTablas: {len(origen)} · Filas totales: {total} · Tamaño del respaldo: {tamano_mb:.2f} MB (sin comprimir {tamano_sql_mb:.2f} MB)\n"
            f"Tiempo de respaldo: {t_respaldo:.1f} s · Tiempo de restauración: {t_restauracion:.1f} s "
            "(el tiempo de restauración es el RTO medido de la base de datos)"
        )
        if diferencias:
            click.echo(f"RESULTADO: FALLÓ. Tablas con diferencias: {', '.join(diferencias)}", err=True)
            raise SystemExit(1)
        click.echo("RESULTADO: CORRECTO. La recuperación reproduce todas las tablas sin pérdida de datos.")
