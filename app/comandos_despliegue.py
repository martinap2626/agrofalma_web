"""
Comandos para llevar el sistema a un servidor (no dependen de dónde se aloje):

    flask preparar-bd           crea las tablas, columnas e índices que falten (seguro de repetir)
    flask verificar-despliegue  revisa la configuración y el estado de la base y dice qué falta antes de abrirlo a los usuarios

`verificar-despliegue` devuelve un código de salida distinto de cero si hay algún FALLO, para poder usarlo en un script de
actualización (si falla, no se reinicia el servicio).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import click

OK, AVISO, FALLO = "OK", "AVISO", "FALLO"
RAIZ = Path(__file__).resolve().parent.parent
DIAS_RESPALDO_MAXIMO = 7


def diagnosticar(app) -> list[tuple[str, str, str]]:
    """Devuelve una lista de (nivel, tema, mensaje). No modifica nada."""
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import SQLAlchemyError

    from app.extensions import db

    r: list[tuple[str, str, str]] = []
    cfg = app.config

    # ---- configuración
    clave = cfg.get("SECRET_KEY") or ""
    if len(clave) < 16:
        r.append((FALLO, "SECRET_KEY", "Es demasiado corta. Genera una de 64 caracteres: python -c \"import secrets; print(secrets.token_hex(32))\"."))
    elif len(clave) < 32:
        r.append((AVISO, "SECRET_KEY", "Tiene menos de 32 caracteres; conviene una más larga."))
    else:
        r.append((OK, "SECRET_KEY", "Definida y de longitud adecuada."))

    if os.environ.get("FLASK_DEBUG", "").strip().lower() in ("1", "true", "yes", "si"):
        r.append((FALLO, "Modo depuración", "FLASK_DEBUG está activo: muestra errores internos a los usuarios. Desactívalo."))
    else:
        r.append((OK, "Modo depuración", "Desactivado."))

    if cfg.get("SESSION_COOKIE_SECURE"):
        r.append((OK, "Cookie de sesión segura", "Solo viaja por HTTPS."))
        if not cfg.get("CONFIAR_EN_PROXY"):
            r.append((AVISO, "Proxy", "Si el sistema está detrás de nginx u otro proxy con HTTPS, activa CONFIAR_EN_PROXY=true."))
    else:
        r.append((AVISO, "Cookie de sesión segura", "SESSION_COOKIE_SECURE=false. En el servidor real, con HTTPS, ponlo en true."))

    if cfg.get("DB_USER") == "root":
        r.append((AVISO, "Usuario de la base", "Usa root. Crea un usuario con permisos solo sobre la base del sistema."))
    if not cfg.get("DB_PASSWORD"):
        r.append((FALLO, "Contraseña de la base", "DB_PASSWORD está vacía."))

    # ---- base de datos
    try:
        db.session.execute(text("SELECT 1"))
        r.append((OK, "Conexión a la base", f"Conectado a {db.engine.url.database}."))
    except SQLAlchemyError as e:
        r.append((FALLO, "Conexión a la base", f"No se pudo conectar: {str(e)[:120]}"))
        return r

    inspector = inspect(db.engine)
    faltan = [t.name for t in db.metadata.sorted_tables if not inspector.has_table(t.name)]
    if faltan:
        r.append((FALLO, "Tablas", f"Faltan {len(faltan)}: {', '.join(faltan[:6])}. Ejecuta: flask preparar-bd"))
    else:
        r.append((OK, "Tablas", f"Las {len(db.metadata.sorted_tables)} tablas existen."))
        sin_indice = 0
        for t in db.metadata.sorted_tables:
            existentes = {i["name"] for i in inspector.get_indexes(t.name)}
            sin_indice += sum(1 for i in t.indexes if i.name not in existentes)
        r.append((OK, "Índices", "Todos creados.") if not sin_indice else (AVISO, "Índices", f"Faltan {sin_indice}. Ejecuta: flask preparar-bd"))

    try:
        from app.models import EjecucionAnalitica, StockPorSede, Usuario, Venta

        if Usuario.query.filter_by(contrasena_hash="PENDIENTE_HASH_REAL").count():
            r.append((FALLO, "Contraseñas", "Hay usuarios con la contraseña de relleno PENDIENTE_HASH_REAL."))
        if not Usuario.query.join(Usuario.rol).filter(Usuario.estado.is_(True), Usuario.rol.has(nombre_rol="Administrador")).count():
            r.append((FALLO, "Administrador", "No hay ningún Administrador activo."))
        else:
            r.append((OK, "Administrador", "Hay al menos un Administrador activo."))
        inactivos = Usuario.query.filter_by(estado=False).count()
        if inactivos:
            r.append((AVISO, "Usuarios", f"{inactivos} usuario(s) desactivado(s). Revisa que los trabajadores reales estén activos y con contraseña propia."))

        ejecucion = EjecucionAnalitica.query.order_by(EjecucionAnalitica.fecha_ejecucion.desc()).first()
        if ejecucion is None:
            r.append((AVISO, "Analítica", "Todavía no se ha calculado. Ejecuta: flask actualizar-analitica"))
        else:
            r.append((OK, "Analítica", f"Calculada el {ejecucion.fecha_ejecucion:%d/%m/%Y} con datos hasta {ejecucion.datos_hasta}."))
        if StockPorSede.query.filter_by(origen="provisional").count():
            r.append((AVISO, "Inventario", "El stock es PROVISIONAL (estimado). Carga el inventario real: flask cargar-inventario."))
        if Venta.query.count() == 0:
            r.append((AVISO, "Ventas", "No hay ventas registradas: el dashboard y los reportes saldrán vacíos."))
    except SQLAlchemyError as e:
        r.append((AVISO, "Datos", f"No se pudieron revisar los datos: {str(e)[:100]}"))

    # ---- respaldo y registros
    carpeta = RAIZ / "respaldos"
    recientes = [p for p in carpeta.glob("*.sql") if time.time() - p.stat().st_mtime < DIAS_RESPALDO_MAXIMO * 86400] if carpeta.exists() else []
    if recientes:
        r.append((OK, "Respaldo", f"Hay un respaldo de los últimos {DIAS_RESPALDO_MAXIMO} días."))
    else:
        r.append((AVISO, "Respaldo", f"No hay respaldos de los últimos {DIAS_RESPALDO_MAXIMO} días. Programa: flask respaldar-bd (ver deploy/)."))

    log = cfg.get("LOG_ARCHIVO")
    if log:
        try:
            ruta = (RAIZ / log).parent
            ruta.mkdir(parents=True, exist_ok=True)
            probar = ruta / ".prueba"
            probar.write_text("ok")
            probar.unlink()
            r.append((OK, "Registro de errores", f"Se puede escribir en {ruta}."))
        except OSError:
            r.append((AVISO, "Registro de errores", "No se puede escribir en la carpeta de logs."))
    return r


def registrar_comandos_despliegue(app) -> None:
    @app.cli.command("preparar-bd")
    def preparar_bd():
        """Crea las tablas, columnas e índices que falten, sin tocar los datos (se puede repetir sin problema)."""
        from sqlalchemy import inspect

        from app.extensions import db
        from app.servicios_analitica import asegurar_esquema

        antes = set(inspect(db.engine).get_table_names())
        db.create_all()  # solo crea las tablas que no existen
        nuevas = sorted(set(inspect(db.engine).get_table_names()) - antes)
        for t in nuevas:
            click.echo(f"  tabla creada: {t}")
        for c in asegurar_esquema():
            click.echo(f"  {c}")
        inspector = inspect(db.engine)
        creados = 0
        for tabla in db.metadata.sorted_tables:
            existentes = {i["name"] for i in inspector.get_indexes(tabla.name)}
            for indice in tabla.indexes:
                if indice.name not in existentes:
                    indice.create(bind=db.engine)
                    creados += 1
        db.session.commit()
        click.echo(f"Listo: {len(nuevas)} tabla(s) nueva(s), {creados} índice(s) nuevo(s).")

    @app.cli.command("verificar-despliegue")
    def verificar_despliegue():
        """Revisa configuración y base de datos y dice qué falta antes de abrir el sistema a los usuarios."""
        resultados = diagnosticar(app)
        marca = {OK: "[ OK  ]", AVISO: "[AVISO]", FALLO: "[FALLO]"}
        for nivel, tema, mensaje in resultados:
            click.echo(f"{marca[nivel]} {tema}: {mensaje}")
        fallos = sum(1 for n, _, _ in resultados if n == FALLO)
        avisos = sum(1 for n, _, _ in resultados if n == AVISO)
        click.echo(f"\nResultado: {fallos} fallo(s), {avisos} aviso(s).")
        if fallos:
            raise SystemExit(1)
