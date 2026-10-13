"""
Pruebas del empaquetado del respaldo: compresión, cifrado, restauración desde cualquier formato, copia fuera del equipo
y retención. No necesitan mysqldump: trabajan con archivos de texto.
"""

import gzip

import pytest

from app.comandos_respaldo import (
    abrir_respaldo,
    aplicar_retencion,
    copiar_fuera_del_equipo,
    empaquetar_respaldo,
    huella_archivo,
    listar_respaldos,
)

CONTENIDO = "CREATE TABLE a (x INT);\nINSERT INTO a VALUES (1);\n-- Dump completed on 2026-10-13\n" * 500


def volcado(tmp_path, nombre="agrofalma_bd_20261013_020000.sql"):
    ruta = tmp_path / nombre
    ruta.write_bytes(CONTENIDO.encode("utf-8"))  # bytes exactos, sin que Windows convierta los saltos de línea
    return ruta


def test_el_respaldo_comprimido_se_restaura_igual_y_ocupa_menos(tmp_path):
    sql = volcado(tmp_path)
    original = sql.stat().st_size
    paquete = empaquetar_respaldo(sql, None)
    assert paquete.name == "agrofalma_bd_20261013_020000.sql.gz"
    assert not sql.exists()  # el volcado plano no se queda en el disco
    assert paquete.stat().st_size < original / 5
    with abrir_respaldo(paquete, None) as ruta:
        assert ruta.read_text(encoding="utf-8") == CONTENIDO
    assert not ruta.exists()  # el archivo temporal se borró


def test_el_respaldo_cifrado_no_deja_texto_legible_y_se_restaura_con_la_clave(tmp_path):
    paquete = empaquetar_respaldo(volcado(tmp_path), "ClaveSecretaDePrueba2026")
    assert paquete.name.endswith(".sql.gz.enc")
    crudo = paquete.read_bytes()
    assert b"CREATE TABLE" not in crudo and b"INSERT INTO" not in crudo  # ni comprimido ni legible
    assert not crudo.startswith(b"\x1f\x8b")  # no es un gzip a la vista
    with abrir_respaldo(paquete, "ClaveSecretaDePrueba2026") as ruta:
        assert ruta.read_text(encoding="utf-8") == CONTENIDO


def test_dos_respaldos_del_mismo_contenido_no_son_iguales_porque_la_sal_es_aleatoria(tmp_path):
    a = empaquetar_respaldo(volcado(tmp_path, "agrofalma_bd_20261013_020000.sql"), "clave-1234567")
    b = empaquetar_respaldo(volcado(tmp_path, "agrofalma_bd_20261014_020000.sql"), "clave-1234567")
    assert a.read_bytes() != b.read_bytes()


def test_una_clave_equivocada_o_ausente_no_restaura_nada(tmp_path):
    paquete = empaquetar_respaldo(volcado(tmp_path), "ClaveCorrecta2026")
    with pytest.raises(RuntimeError, match="clave es incorrecta"):
        with abrir_respaldo(paquete, "ClaveEquivocada2026"):
            pass
    with pytest.raises(RuntimeError, match="RESPALDO_CLAVE"):
        with abrir_respaldo(paquete, None):
            pass


def test_un_respaldo_alterado_se_detecta_al_restaurar(tmp_path):
    paquete = empaquetar_respaldo(volcado(tmp_path), "ClaveCorrecta2026")
    datos = bytearray(paquete.read_bytes())
    datos[len(datos) // 2] ^= 0xFF  # se cambia un byte a la mitad del archivo
    paquete.write_bytes(bytes(datos))
    with pytest.raises(RuntimeError, match="dañado"):
        with abrir_respaldo(paquete, "ClaveCorrecta2026"):
            pass


def test_un_respaldo_cortado_se_detecta(tmp_path):
    paquete = empaquetar_respaldo(volcado(tmp_path), "ClaveCorrecta2026")
    paquete.write_bytes(paquete.read_bytes()[:-40])
    with pytest.raises(RuntimeError):
        with abrir_respaldo(paquete, "ClaveCorrecta2026"):
            pass


def test_un_archivo_que_no_es_un_respaldo_cifrado_se_rechaza(tmp_path):
    falso = tmp_path / "agrofalma_bd_20261013_020000.sql.gz.enc"
    falso.write_bytes(b"esto no es un respaldo")
    with pytest.raises(RuntimeError, match="formato"):
        with abrir_respaldo(falso, "cualquiera"):
            pass


def test_un_volcado_plano_se_entrega_tal_cual_sin_copiarlo(tmp_path):
    sql = volcado(tmp_path)
    with abrir_respaldo(sql, None) as ruta:
        assert ruta == sql
    assert sql.exists()  # abrir un respaldo plano nunca lo borra


def test_un_fallo_al_empaquetar_no_deja_archivos_a_medias(tmp_path, monkeypatch):
    sql = volcado(tmp_path)

    def explota(*_a, **_k):
        raise OSError("disco lleno")

    monkeypatch.setattr("app.comandos_respaldo.gzip.open", explota)
    with pytest.raises(OSError):
        empaquetar_respaldo(sql, "clave-1234567")
    assert sql.exists()  # el volcado original sigue ahí
    assert [p.name for p in tmp_path.iterdir()] == [sql.name]


def test_la_copia_fuera_del_equipo_queda_identica_y_tiene_su_propia_retencion(tmp_path):
    externa = tmp_path / "disco_externo"
    for dia in range(1, 6):
        p = empaquetar_respaldo(volcado(tmp_path, f"agrofalma_bd_202610{dia:02d}_020000.sql"), "clave-1234567")
        copia = copiar_fuera_del_equipo(p, externa, conservar=3)
        assert huella_archivo(copia) == huella_archivo(p)
    assert [p.name for p in listar_respaldos(externa)] == [
        "agrofalma_bd_20261005_020000.sql.gz.enc", "agrofalma_bd_20261004_020000.sql.gz.enc", "agrofalma_bd_20261003_020000.sql.gz.enc",
    ]


def test_la_copia_externa_que_no_queda_igual_se_descarta(tmp_path, monkeypatch):
    p = empaquetar_respaldo(volcado(tmp_path), None)
    import shutil

    def copia_mala(origen, destino):
        destino.write_bytes(b"corrupto")

    monkeypatch.setattr(shutil, "copy2", copia_mala)
    with pytest.raises(RuntimeError, match="no quedó idéntica"):
        copiar_fuera_del_equipo(p, tmp_path / "externo", conservar=3)
    assert not list((tmp_path / "externo").glob("agrofalma_bd_*"))


def test_la_retencion_cubre_los_tres_formatos_y_no_toca_otros_archivos(tmp_path):
    nombres = [
        "agrofalma_bd_20261001_020000.sql", "agrofalma_bd_20261002_020000.sql.gz", "agrofalma_bd_20261003_020000.sql.gz.enc",
        "agrofalma_bd_20261004_020000.sql.gz.enc", "agrofalma_bd_20261005_020000.sql.gz",
    ]
    for n in nombres:
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "notas.txt").write_text("no es un respaldo")
    (tmp_path / "verificacion_agrofalma_bd_20260101_000000.sql").write_text("temporal")
    borrados = aplicar_retencion(tmp_path, 2)
    assert sorted(p.name for p in borrados) == sorted(nombres[:3])
    assert (tmp_path / "notas.txt").exists() and (tmp_path / "verificacion_agrofalma_bd_20260101_000000.sql").exists()


def test_el_gzip_generado_es_un_gzip_estandar(tmp_path):
    paquete = empaquetar_respaldo(volcado(tmp_path), None)
    assert gzip.decompress(paquete.read_bytes()).decode("utf-8") == CONTENIDO  # se puede abrir con cualquier herramienta


# ---------------------------------------------------------------------------
# De punta a punta con la base de pruebas: respaldar-bd (cifrado + copia externa) y restaurar-bd
# ---------------------------------------------------------------------------
@pytest.fixture()
def entorno_de_respaldo(app, tmp_path, monkeypatch):
    from app.comandos_respaldo import localizar_binario
    from config import Config
    from conftest import NOMBRE_BD_PRUEBAS

    try:
        localizar_binario("mysqldump")
        localizar_binario("mysql")
    except FileNotFoundError:
        pytest.skip("mysqldump/mysql no están disponibles en este equipo")
    monkeypatch.setattr("app.comandos_respaldo.CARPETA_RESPALDOS", tmp_path / "respaldos")
    monkeypatch.setattr(Config, "DB_NAME", NOMBRE_BD_PRUEBAS)  # el comando respalda la base de PRUEBAS, nunca la real
    return Config, tmp_path


def test_respaldar_bd_cifra_copia_fuera_del_equipo_y_restaurar_bd_lo_recupera(app, entorno_de_respaldo, monkeypatch):
    from app.comandos_respaldo import BD_TEMPORAL_VERIFICACION, _eliminar_bd, contar_filas
    from conftest import NOMBRE_BD_PRUEBAS

    Config, tmp_path = entorno_de_respaldo
    monkeypatch.setattr(Config, "RESPALDO_CLAVE", "ClaveDeIntegracion2026")
    monkeypatch.setattr(Config, "RESPALDO_COPIA_DIR", str(tmp_path / "disco_externo"))
    ejecutor = app.test_cli_runner()

    r = ejecutor.invoke(args=["respaldar-bd"])
    assert r.exit_code == 0, r.output
    assert "Cifrado: sí" in r.output and "Copia fuera del equipo verificada" in r.output
    local = listar_respaldos(tmp_path / "respaldos")
    externo = listar_respaldos(tmp_path / "disco_externo")
    assert len(local) == len(externo) == 1 and local[0].name.endswith(".sql.gz.enc")
    assert huella_archivo(local[0]) == huella_archivo(externo[0])
    assert b"CREATE TABLE" not in local[0].read_bytes()  # nada legible dentro del archivo

    destino = "agrofalma_restauracion_test"
    try:
        r = ejecutor.invoke(args=["restaurar-bd", str(externo[0]), "--destino", destino])  # se restaura desde la COPIA externa
        assert r.exit_code == 0, r.output
        restaurada, actual = contar_filas(Config, destino), contar_filas(Config, NOMBRE_BD_PRUEBAS)
        # la copia se toma antes de anotar la operación: la base en uso solo tiene de más las 2 filas de consola
        # (respaldo y restauración), que quedan en la bitácora
        assert actual.pop("bitacora_acciones") - restaurada.pop("bitacora_acciones") == 2
        assert restaurada == actual
    finally:
        _eliminar_bd(Config, destino)
        _eliminar_bd(Config, BD_TEMPORAL_VERIFICACION)


def test_restaurar_un_respaldo_cifrado_sin_la_clave_falla_con_un_mensaje_claro(app, entorno_de_respaldo, monkeypatch):
    Config, tmp_path = entorno_de_respaldo
    monkeypatch.setattr(Config, "RESPALDO_CLAVE", "ClaveDeIntegracion2026")
    monkeypatch.setattr(Config, "RESPALDO_COPIA_DIR", "")
    ejecutor = app.test_cli_runner()
    assert ejecutor.invoke(args=["respaldar-bd"]).exit_code == 0
    archivo = listar_respaldos(tmp_path / "respaldos")[0]

    monkeypatch.setattr(Config, "RESPALDO_CLAVE", "")  # el operador olvidó la clave
    r = ejecutor.invoke(args=["restaurar-bd", str(archivo), "--destino", "agrofalma_restauracion_test"])
    assert r.exit_code == 1 and "RESPALDO_CLAVE" in r.output
    # y no quedó una base de datos vacía a medias
    import pymysql

    conexion = pymysql.connect(host=Config.DB_HOST, port=int(Config.DB_PORT), user=Config.DB_USER, password=Config.DB_PASSWORD)
    with conexion.cursor() as cursor:
        cursor.execute("SHOW DATABASES LIKE 'agrofalma_restauracion_test'")
        assert cursor.fetchone() is None
    conexion.close()


def test_si_la_copia_externa_falla_el_comando_avisa_con_codigo_distinto_de_cero(app, entorno_de_respaldo, monkeypatch):
    Config, tmp_path = entorno_de_respaldo
    bloqueo = tmp_path / "no_es_una_carpeta"
    bloqueo.write_text("este archivo impide crear la carpeta de destino")
    monkeypatch.setattr(Config, "RESPALDO_CLAVE", "")
    monkeypatch.setattr(Config, "RESPALDO_COPIA_DIR", str(bloqueo / "dentro"))
    r = app.test_cli_runner().invoke(args=["respaldar-bd"])
    assert r.exit_code == 2 and "NO se pudo copiar fuera del equipo" in r.output
    assert len(listar_respaldos(tmp_path / "respaldos")) == 1  # el respaldo local sí quedó


def test_el_respaldo_sin_clave_ni_copia_externa_avisa_pero_funciona(app, entorno_de_respaldo, monkeypatch):
    Config, tmp_path = entorno_de_respaldo
    monkeypatch.setattr(Config, "RESPALDO_CLAVE", "")
    monkeypatch.setattr(Config, "RESPALDO_COPIA_DIR", "")
    r = app.test_cli_runner().invoke(args=["respaldar-bd"])
    assert r.exit_code == 0 and "NO está cifrado" in r.output and "no hay copia fuera del equipo" in r.output
    assert listar_respaldos(tmp_path / "respaldos")[0].name.endswith(".sql.gz")
