# Migraciones del esquema de la base de datos

Cada cambio de la estructura de la base (una tabla, una columna, un índice, una restricción) es un archivo en `versions/`, con
su subida (`upgrade`) y su bajada (`downgrade`). Así el cambio es repetible en cualquier equipo, queda en el historial de git
y se puede revisar antes de aplicarlo. Se usa Alembic a través de Flask-Migrate.

## Situación actual

* `0001_esquema_inicial`: el esquema completo al 13/10/2026 (23 tablas).
* Una base **nueva** se crea con `flask --app run preparar-bd`.
* Una base **anterior a las migraciones** (como la de operación, creada con `db.create_all()` y comandos a medida) se pone al
  día con el mismo `flask --app run preparar-bd`: agrega lo que falte (`app/esquema.py`), la marca como revisión `0001` y aplica
  las migraciones posteriores, si las hay. No toca los datos y se puede repetir.
* `flask --app run verificar-despliegue` avisa si a la base le faltan migraciones.

## Cómo hacer un cambio

1. Editar el modelo en `app/models/` (por ejemplo, agregar una columna).
2. Generar la migración: `flask --app run db migrate -m "agregar telefono a usuarios"`. Crea un archivo en `versions/`.
3. **Revisarlo a mano**: Alembic no detecta bien los cambios de nombre ni algunos tipos. Probar la bajada también.
4. Aplicarlo: `flask --app run preparar-bd` (o `flask --app run db upgrade`).
5. Correr `python -m pytest tests/test_migraciones.py`: falla si el modelo y las migraciones no coinciden.

Para volver atrás un paso: `flask --app run db downgrade -1` (hacer un respaldo antes: una bajada puede borrar datos).

## Reglas

* Nunca editar una migración que ya se aplicó en una base real: crear una nueva.
* Las columnas nuevas deben aceptar los datos que ya existen (NULL o valor por defecto).
* Hacer un respaldo (`flask --app run respaldar-bd`) antes de migrar una base de operación.
* Las migraciones necesitan un usuario de MySQL con permisos de estructura (no el usuario de la aplicación; ver `flask --app run permisos-bd`).
