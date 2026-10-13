"""parametros del sistema

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13

Tabla `parametros_sistema`: ajustes que el Administrador cambia desde la pantalla «Parámetros» (días de reposición, protección
por clase ABC y descuento máximo del Trabajador). Es segura de repetir: `flask preparar-bd` puede haber creado ya la tabla.
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    if 'parametros_sistema' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table('parametros_sistema',
    sa.Column('clave', sa.String(length=40), nullable=False),
    sa.Column('valor', sa.String(length=40), nullable=False),
    sa.Column('actualizado_por', sa.String(length=30), nullable=True),
    sa.Column('fecha_actualizacion', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('clave')
    )


def downgrade():
    op.drop_table('parametros_sistema')
