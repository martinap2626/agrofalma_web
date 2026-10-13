"""
Control de versiones del esquema de la base de datos (Alembic, vía Flask-Migrate).

* `sincronizar_migraciones()` la usa `flask preparar-bd`: si la base no tiene control de versiones (es nueva, o es una base
  anterior a las migraciones que los pasos previos de `preparar-bd` ya dejaron con el esquema inicial), la marca como la
  revisión inicial; después aplica las migraciones posteriores, si las hay.
* `estado_migraciones()` la usa `flask verificar-despliegue` para avisar si a la base le faltan migraciones.
"""

from __future__ import annotations

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from flask_migrate import stamp, upgrade
from sqlalchemy import inspect

from app.extensions import db

REVISION_INICIAL = "0001"


def _carpeta() -> str:
    from app import CARPETA_MIGRACIONES

    return str(CARPETA_MIGRACIONES)


def revision_actual() -> str | None:
    """Revisión en la que está la base (None si todavía no tiene control de versiones)."""
    with db.engine.connect() as conexion:
        return MigrationContext.configure(conexion).get_current_revision()


def revision_mas_reciente() -> str | None:
    """Última revisión que existe en la carpeta migrations/."""
    return ScriptDirectory(_carpeta()).get_current_head()


def estado_migraciones() -> tuple[str, str]:
    """Devuelve ('al_dia' | 'sin_control' | 'atrasada', mensaje)."""
    actual, ultima = revision_actual(), revision_mas_reciente()
    if actual is None:
        return "sin_control", "La base todavía no tiene control de versiones del esquema. Ejecuta: flask preparar-bd"
    if actual != ultima:
        return "atrasada", f"La base está en la revisión {actual} y la última es {ultima}. Ejecuta: flask preparar-bd (o flask db upgrade)"
    return "al_dia", f"Esquema en la revisión {actual} (la última)."


def sincronizar_migraciones() -> list[str]:
    """Marca la base si no tiene control de versiones y aplica las migraciones pendientes. Devuelve lo que hizo."""
    hecho = []
    if "alembic_version" not in inspect(db.engine).get_table_names() or revision_actual() is None:
        stamp(directory=_carpeta(), revision=REVISION_INICIAL)
        hecho.append(f"control de versiones del esquema iniciado en la revisión {REVISION_INICIAL}")
    antes = revision_actual()
    upgrade(directory=_carpeta(), revision="head")
    despues = revision_actual()
    if antes != despues:
        hecho.append(f"migraciones aplicadas: {antes} -> {despues}")
    return hecho
