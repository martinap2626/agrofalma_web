"""
Cambios de esquema de la base de datos posteriores a la primera versión, para bases que ya existen.

Una base nueva se crea completa con `db.create_all()` (incluye todo lo de abajo). Una base ya en uso (la del negocio) no
cambia sola cuando el código agrega una columna: por eso `flask preparar-bd` llama a `asegurar_esquema_plataforma()`, que
agrega lo que falte SIN tocar los datos y se puede repetir sin problema. `flask verificar-despliegue` usa
`esquema_pendiente()` para avisar si falta algo.

Cuando se adopten migraciones formales (Flask-Migrate / Alembic), este módulo queda solo para bases antiguas.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from app.extensions import db

# (tabla, columna, definición). Las columnas nuevas siempre aceptan datos antiguos (NULL o valor por defecto).
COLUMNAS_NUEVAS = [
    ("usuarios", "version_sesion", "INT NOT NULL DEFAULT 0"),
    ("ventas", "token_envio", "VARCHAR(40) NULL"),
    ("ventas", "anulada", "TINYINT(1) NOT NULL DEFAULT 0"),
    ("ventas", "fecha_anulacion", "DATETIME NULL"),
    ("ventas", "id_usuario_anulacion", "INT NULL"),
    ("ventas", "motivo_anulacion", "VARCHAR(150) NULL"),
    ("recuperaciones", "token_envio", "VARCHAR(40) NULL"),
    ("bitacora_acciones", "hash_previo", "VARCHAR(64) NULL"),
    ("bitacora_acciones", "hash_fila", "VARCHAR(64) NULL"),
]

# (tabla, nombre del índice único, columna): impiden que el mismo formulario se registre dos veces
INDICES_UNICOS = [
    ("ventas", "token_envio", "token_envio"),
    ("recuperaciones", "token_envio", "token_envio"),
]

# (tabla, nombre, condición, filas que la incumplen se buscan con "NOT (condición)"): la base de datos misma impide los
# valores imposibles (stock negativo, saldo negativo...), aunque falle alguna validación de la aplicación
RESTRICCIONES_CHECK = [
    ("stock_por_sede", "ck_stock_no_negativo", "cantidad_actual >= 0"),
    ("creditos", "ck_creditos_saldo_no_negativo", "saldo_pendiente >= 0"),
    ("creditos", "ck_creditos_monto_no_negativo", "monto_credito >= 0"),
    ("recuperaciones", "ck_recuperaciones_monto_positivo", "monto_recuperado > 0"),
    ("detalle_ventas", "ck_detalle_cantidad_positiva", "cantidad > 0"),
    ("ventas", "ck_ventas_total_no_negativo", "total >= 0"),
]

ENUM_ESTADO_CREDITO = "ENUM('pendiente','pagado','anulado') NOT NULL DEFAULT 'pendiente'"


def _columnas(tabla: str) -> set[str]:
    return {c["name"] for c in inspect(db.engine).get_columns(tabla)}


def _checks_existentes() -> set[str]:
    filas = db.session.execute(text(
        "SELECT constraint_name FROM information_schema.table_constraints "
        "WHERE table_schema = DATABASE() AND constraint_type = 'CHECK'"
    )).all()
    return {f[0] for f in filas}


def _enum_credito_incompleto() -> bool:
    fila = db.session.execute(text(
        "SELECT column_type FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'creditos' AND column_name = 'estado'"
    )).first()
    return fila is not None and "anulado" not in str(fila[0])


def esquema_pendiente() -> list[str]:
    """Lista (sin modificar nada) lo que le falta a la base respecto al código actual."""
    if db.engine.dialect.name != "mysql":
        return []
    inspector = inspect(db.engine)
    pendientes = []
    for tabla in ("bitacora_cadena", "movimientos_inventario"):
        if not inspector.has_table(tabla):
            pendientes.append(f"tabla {tabla}")
    for tabla, columna, _ in COLUMNAS_NUEVAS:
        if inspector.has_table(tabla) and columna not in _columnas(tabla):
            pendientes.append(f"columna {tabla}.{columna}")
    if inspector.has_table("creditos") and _enum_credito_incompleto():
        pendientes.append("estado 'anulado' en creditos.estado")
    existentes = _checks_existentes()
    for tabla, nombre, _ in RESTRICCIONES_CHECK:
        if inspector.has_table(tabla) and nombre not in existentes:
            pendientes.append(f"restricción {nombre}")
    return pendientes


def asegurar_esquema_plataforma() -> list[str]:
    """Agrega lo que falte (tablas, columnas, restricciones). Devuelve la lista de cambios y avisos. Seguro de repetir."""
    cambios: list[str] = []
    if db.engine.dialect.name != "mysql":
        return cambios
    inspector = inspect(db.engine)

    from app.models import BitacoraCadena, MovimientoInventario

    for modelo in (BitacoraCadena, MovimientoInventario):
        if not inspector.has_table(modelo.__tablename__):
            modelo.__table__.create(bind=db.engine, checkfirst=True)
            cambios.append(f"tabla {modelo.__tablename__}")

    for tabla, columna, definicion in COLUMNAS_NUEVAS:
        if inspector.has_table(tabla) and columna not in _columnas(tabla):
            db.session.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}"))
            cambios.append(f"columna {tabla}.{columna}")

    for tabla, indice, columna in INDICES_UNICOS:
        existentes = {i["name"] for i in inspect(db.engine).get_indexes(tabla)}
        if indice not in existentes:
            db.session.execute(text(f"ALTER TABLE {tabla} ADD UNIQUE INDEX {indice} ({columna})"))
            cambios.append(f"índice único {tabla}.{indice}")

    # la clave foránea de quién anuló la venta (el modelo la declara; en una base antigua hay que crearla)
    fks = {fk["constrained_columns"][0] for fk in inspect(db.engine).get_foreign_keys("ventas") if fk["constrained_columns"]}
    if "id_usuario_anulacion" not in fks:
        db.session.execute(text(
            "ALTER TABLE ventas ADD CONSTRAINT fk_ventas_usuario_anulacion "
            "FOREIGN KEY (id_usuario_anulacion) REFERENCES usuarios (id_usuario)"
        ))
        cambios.append("clave foránea ventas.id_usuario_anulacion")

    if _enum_credito_incompleto():
        db.session.execute(text(f"ALTER TABLE creditos MODIFY estado {ENUM_ESTADO_CREDITO}"))
        cambios.append("estado 'anulado' en creditos.estado")

    existentes = _checks_existentes()
    for tabla, nombre, condicion in RESTRICCIONES_CHECK:
        if nombre in existentes:
            continue
        incumplen = db.session.execute(text(f"SELECT COUNT(*) FROM {tabla} WHERE NOT ({condicion})")).scalar()
        if incumplen:
            cambios.append(f"AVISO: no se agregó {nombre} porque {incumplen} fila(s) de {tabla} ya incumplen ({condicion}); corrígelas y repite")
            continue
        db.session.execute(text(f"ALTER TABLE {tabla} ADD CONSTRAINT {nombre} CHECK ({condicion})"))
        cambios.append(f"restricción {nombre}")

    db.session.commit()
    return cambios
