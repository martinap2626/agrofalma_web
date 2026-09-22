"""
Módulo de Analítica comercial (Sprint 3) — HU-11: recomendaciones de
abastecimiento. Exclusivo de Administrador, igual que usuarios y dashboard
(RNF-03): la decisión de qué y cuánto reabastecer es responsabilidad
gerencial, no una tarea operativa de venta.

Aquí viven las tres pantallas del módulo: recomendaciones de
abastecimiento (HU-11), predicción de demanda (HU-09) y segmentación ABC
(HU-10). Ninguna de las tres calcula nada en la app -- todas leen tablas
que llena un job de analítica aparte (modelo de predicción de demanda +
segmentación ABC), que todavía no está implementado. Mientras tanto,
predicciones_demanda y segmentacion_productos contienen datos de
referencia (incluido el mape) solo para validar cómo se ve la interfaz --
no son el resultado real de evaluación del modelo.
"""

from flask import Blueprint, render_template, request, flash
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError

from app.decorators import rol_requerido
from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion
from app.extensions import db
from app.models import (
    RecomendacionAbastecimiento,
    PrediccionDemanda,
    SegmentacionProducto,
    PrediccionDemandaPreview,
    SegmentacionProductoPreview,
)

bp = Blueprint("analitica", __name__, url_prefix="/analitica")

# el mas urgente primero, para que lo que necesita atencion ya no se pierda
# entre filas de "sin_accion"
ORDEN_URGENCIA = {"urgente": 0, "media": 1, "sin_accion": 2}


# pantalla de recomendaciones de abastecimiento: cruza demanda prevista vs.
# stock actual y explica en texto plano por que se sugiere cada accion
@bp.route("")
@login_required
@rol_requerido("Administrador")
def recomendaciones():
    filtro_urgencia = request.args.get("urgencia", "").strip()

    try:
        consulta = RecomendacionAbastecimiento.query.options(
            db.joinedload(RecomendacionAbastecimiento.producto),
            db.joinedload(RecomendacionAbastecimiento.sede),
        )
        if filtro_urgencia in ("urgente", "media", "sin_accion"):
            consulta = consulta.filter(RecomendacionAbastecimiento.nivel_urgencia == filtro_urgencia)
        filas = consulta.all()
    except SQLAlchemyError as error:
        registrar_error_conexion("listar recomendaciones de abastecimiento", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template(
            "analitica/recomendaciones.html", recomendaciones=[], filtro_urgencia=filtro_urgencia
        ), 503

    # las mas urgentes arriba, y dentro de cada nivel, la que tiene mayor
    # demanda prevista primero (es la que mas impacto tiene si se agota)
    filas.sort(key=lambda r: (ORDEN_URGENCIA.get(r.nivel_urgencia, 99), -float(r.demanda_prevista)))

    return render_template(
        "analitica/recomendaciones.html", recomendaciones=filas, filtro_urgencia=filtro_urgencia
    )


# pantalla de prediccion de demanda: una fila por producto con su ultima
# prediccion generada (demanda estimada para el proximo periodo y tendencia
# respecto al periodo anterior)
@bp.route("/prediccion")
@login_required
@rol_requerido("Administrador")
def prediccion():
    try:
        # una fila por producto: la mas reciente (MAX(fecha_generacion))
        subconsulta = (
            db.session.query(
                PrediccionDemanda.id_producto,
                db.func.max(PrediccionDemanda.fecha_generacion).label("ultima_fecha"),
            )
            .group_by(PrediccionDemanda.id_producto)
            .subquery()
        )
        filas = (
            PrediccionDemanda.query.join(
                subconsulta,
                db.and_(
                    PrediccionDemanda.id_producto == subconsulta.c.id_producto,
                    PrediccionDemanda.fecha_generacion == subconsulta.c.ultima_fecha,
                ),
            )
            .options(db.joinedload(PrediccionDemanda.producto))
            .all()
        )
    except SQLAlchemyError as error:
        registrar_error_conexion("listar predicciones de demanda", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("analitica/prediccion.html", predicciones=[]), 503

    # mayor demanda estimada primero, es la que mas le importa al gerente
    filas.sort(key=lambda p: -float(p.demanda_estimada))

    # vista previa con datos reales del historico (Excel), identificados por
    # nombre de producto mientras el catalogo real no este en MySQL -- ver
    # app/models/prediccion_demanda_preview.py. No crítica: si falla, la
    # pantalla igual muestra la tabla principal.
    try:
        vista_previa = (
            PrediccionDemandaPreview.query.order_by(PrediccionDemandaPreview.cantidad_sugerida.desc()).all()
        )
    except SQLAlchemyError as error:
        registrar_error_conexion("listar vista previa de predicciones", error)
        vista_previa = []

    return render_template("analitica/prediccion.html", predicciones=filas, vista_previa=vista_previa)


# pantalla de segmentacion ABC: clasificacion de productos segun su
# participacion acumulada en las ventas del periodo (Pareto 80/15/5)
@bp.route("/segmentacion")
@login_required
@rol_requerido("Administrador")
def segmentacion():
    filtro_clase = request.args.get("clase", "").strip().upper()

    try:
        consulta = SegmentacionProducto.query.options(
            db.joinedload(SegmentacionProducto.producto)
        )
        if filtro_clase in ("A", "B", "C"):
            consulta = consulta.filter(SegmentacionProducto.clase_abc == filtro_clase)
        filas = consulta.all()
    except SQLAlchemyError as error:
        registrar_error_conexion("listar segmentacion de productos", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template(
            "analitica/segmentacion.html", segmentaciones=[], filtro_clase=filtro_clase
        ), 503

    # orden de Pareto: de mayor a menor participacion en las ventas
    filas.sort(key=lambda s: float(s.pct_acumulado))

    # vista previa con datos reales del historico (Excel) -- ver
    # app/models/segmentacion_producto_preview.py. No critica: si falla, la
    # pantalla igual muestra la tabla principal.
    try:
        consulta_preview = SegmentacionProductoPreview.query
        if filtro_clase in ("A", "B", "C"):
            consulta_preview = consulta_preview.filter(SegmentacionProductoPreview.clase_abc == filtro_clase)
        vista_previa = consulta_preview.order_by(SegmentacionProductoPreview.pct_acumulado).all()
    except SQLAlchemyError as error:
        registrar_error_conexion("listar vista previa de segmentacion", error)
        vista_previa = []

    return render_template(
        "analitica/segmentacion.html",
        segmentaciones=filas,
        filtro_clase=filtro_clase,
        vista_previa=vista_previa,
    )
