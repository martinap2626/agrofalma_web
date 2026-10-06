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

from datetime import date, timedelta

from flask import Blueprint, render_template, request, flash
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError

from app.decorators import rol_requerido
from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion
from app.extensions import db
import json

from analitica_job.pipeline.recomendaciones import PARAMETROS, simular
from app.servicios_analitica import ultima_ejecucion
from app.models import (
    EventoNegocio,
    Producto,
    PlanCampana,
    RecomendacionAbastecimiento,
    PrediccionDemanda,
    SegmentacionProducto,
    PrediccionDemandaPreview,
    SegmentacionProductoPreview,
)

bp = Blueprint("analitica", __name__, url_prefix="/analitica")

# la ultima corrida del pipeline (para el aviso "calculado el ... con datos hasta ..."); si falla, la
# pantalla igual se muestra, solo sin ese aviso
def _ejecucion_o_none():
    try:
        return ultima_ejecucion()
    except SQLAlchemyError as error:
        registrar_error_conexion("leer ultima ejecucion de analitica", error)
        return None


# nombres en palabras de los metodos de pronostico, para explicarselos al usuario
NOMBRE_METODO = {
    "mezcla": "mezcla del mismo mes del año anterior y el promedio de los últimos meses",
    "estacional": "lo vendido en el mismo mes del año anterior",
    "ingenuo": "lo vendido el mes pasado",
    "promedio_3": "promedio de los últimos 3 meses",
    "promedio_6": "promedio de los últimos 6 meses",
    "ml_poisson": "modelo de aprendizaje automático",
}


# "2026-G" -> "campaña grande 2026"
def _nombre_campana(campana_id):
    if not campana_id:
        return None
    anio, tipo = campana_id.split("-")
    return f"campaña {'grande' if tipo == 'G' else 'chica'} {anio}"


# id de la campaña con la que se midió el error del plan (guardado en el resumen de la ejecucion)
def _campana_de_prueba(ejecucion, tipo):
    try:
        return json.loads(ejecucion.resumen or "{}").get("campana", {}).get(tipo, {}).get("campana_prueba")
    except (ValueError, AttributeError):
        return None


# datos del modelo para la explicacion en pantalla: metodo elegido por clase y resumen de confiabilidad
def _info_modelo(ejecucion):
    if ejecucion is None:
        return {}
    try:
        metodos = json.loads(ejecucion.metodo_por_clase or "{}")
        resumen = json.loads(ejecucion.resumen or "{}")
    except ValueError:
        metodos, resumen = {}, {}
    return {"metodos": {c: NOMBRE_METODO.get(m, m) for c, m in metodos.items()}, "confiabilidad": resumen.get("confiabilidad", {})}


# el mas urgente primero, para que lo que necesita atencion ya no se pierda
# entre filas de "sin_accion"; el "plan" (sin inventario) va ordenado por clase ABC
ORDEN_URGENCIA = {"urgente": 0, "media": 1, "plan": 1, "sin_accion": 2}


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
        if filtro_urgencia in ("urgente", "media", "sin_accion", "plan"):
            consulta = consulta.filter(RecomendacionAbastecimiento.nivel_urgencia == filtro_urgencia)
        filas = consulta.all()
        hay_plan = RecomendacionAbastecimiento.query.filter_by(nivel_urgencia="plan").count() > 0
        hay_inventario = RecomendacionAbastecimiento.query.filter_by(con_inventario=True).count() > 0
    except SQLAlchemyError as error:
        registrar_error_conexion("listar recomendaciones de abastecimiento", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template(
            "analitica/recomendaciones.html", recomendaciones=[], filtro_urgencia=filtro_urgencia, ejecucion=None,
            parametros=PARAMETROS, hay_plan=False, hay_inventario=False,
        ), 503

    # las mas urgentes arriba, y dentro de cada nivel, la que tiene mayor
    # demanda prevista primero (es la que mas impacto tiene si se agota)
    filas.sort(key=lambda r: (
        ORDEN_URGENCIA.get(r.nivel_urgencia, 99), r.clase_abc or "Z", -float(r.demanda_prevista),
    ))

    return render_template(
        "analitica/recomendaciones.html", recomendaciones=filas, filtro_urgencia=filtro_urgencia,
        ejecucion=_ejecucion_o_none(), parametros=PARAMETROS, hay_plan=hay_plan, hay_inventario=hay_inventario,
    )


# pantalla del plan de campaña: cuanto se espera vender en la campaña grande/chica, cuanto va vendido y cuanto falta
@bp.route("/campana")
@login_required
@rol_requerido("Administrador")
def campana():
    tipo = request.args.get("tipo", "en_curso")
    if tipo not in ("en_curso", "proxima"):
        tipo = "en_curso"
    filtro_clase = request.args.get("clase", "").strip().upper()
    if filtro_clase not in ("A", "B", "C"):
        filtro_clase = ""

    try:
        hay_en_curso = PlanCampana.query.filter_by(tipo="en_curso").count() > 0
        if tipo == "en_curso" and not hay_en_curso:
            tipo = "proxima"  # la campaña recién terminó: solo hay plan de la siguiente
        base = PlanCampana.query.filter_by(tipo=tipo)
        todas = base.options(db.joinedload(PlanCampana.producto)).all()
        filas = [f for f in todas if not filtro_clase or f.clase_abc == filtro_clase]
    except SQLAlchemyError as error:
        registrar_error_conexion("listar plan de campana", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("analitica/campana.html", filas=[], resumen=None, ejecucion=None, tipo=tipo, filtro_clase="",
                               hay_en_curso=False), 503

    # totales por clase (sobre todas las filas, sin importar el filtro de clase) para las tarjetas de resumen
    def suma(campo, clase=None):
        return float(sum(float(getattr(f, campo)) for f in todas if clase is None or f.clase_abc == clase))

    ejecucion = _ejecucion_o_none()
    resumen = None
    if todas:
        primera = todas[0]
        esperado, referencia, vendido = suma("esperado_total"), suma("referencia_anio_anterior"), suma("vendido")
        resumen = {
            "nombre": primera.campana_nombre, "campana_id": primera.campana_id, "meses_conocidos": primera.meses_conocidos,
            "vendido": vendido, "esperado_total": esperado, "referencia": referencia,
            "faltan": suma("esperado_resto"),
            "avance_pct": (vendido / esperado * 100) if esperado else 0,
            "variacion_pct": ((esperado - referencia) / referencia * 100) if referencia else None,
            "ritmo_pct": (float(primera.ritmo_global) * 100) if primera.meses_conocidos and primera.ritmo_global is not None else None,
            "por_clase": {c: {"esperado": suma("esperado_total", c), "n": sum(1 for f in todas if f.clase_abc == c)} for c in ("A", "B", "C")},
            "errores": {c: next((float(f.error_tipico_pct) for f in todas if f.clase_abc == c and f.error_tipico_pct is not None), None)
                        for c in ("A", "B", "C")},
            "campana_prueba_nombre": _nombre_campana(_campana_de_prueba(ejecucion, primera.tipo)),
        }
    filas.sort(key=lambda f: -float(f.esperado_total))
    return render_template(
        "analitica/campana.html", filas=filas, resumen=resumen, ejecucion=ejecucion, tipo=tipo,
        filtro_clase=filtro_clase, hay_en_curso=hay_en_curso,
    )


# simulador de escenarios: recalcula la recomendacion de un producto con otra demanda, otro tiempo de reposicion o otro stock
# (no guarda nada; sirve para decidir "que pasaria si ...")
@bp.route("/simulador")
@login_required
@rol_requerido("Administrador")
def simulador():
    try:
        recs = (RecomendacionAbastecimiento.query.join(Producto).options(db.joinedload(RecomendacionAbastecimiento.producto))
                .order_by(Producto.nombre_producto).all())
    except SQLAlchemyError as error:
        registrar_error_conexion("abrir simulador de escenarios", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("analitica/simulador.html", recs=[], rec=None, base=None, escenario=None, ejecucion=None,
                               parametros=PARAMETROS, form={}), 503

    def numero(nombre, defecto, minimo, maximo):
        try:
            v = float(request.args.get(nombre, "").replace(",", "."))
            return v if minimo <= v <= maximo and v == v else defecto
        except ValueError:
            return defecto

    id_txt = request.args.get("id_producto", "").strip()
    rec = next((r for r in recs if str(r.id_producto) == id_txt), None)
    base = escenario = None
    form = {}
    if rec is not None:
        modo = request.args.get("modo", "pct")
        modo = modo if modo in ("pct", "unid", "total") else "pct"
        limite = 500.0 if modo == "pct" else 1_000_000.0
        cambio = numero("cambio", 0.0, -90.0 if modo == "pct" else -1_000_000.0, limite)
        dias = int(numero("dias", float(PARAMETROS.tiempo_reposicion_dias), 1, 120))
        stock_txt = request.args.get("stock", "").strip()
        stock = numero("stock", None, 0, 1_000_000) if stock_txt else (rec.stock_actual if rec.con_inventario else None)
        form = {"id_producto": rec.id_producto, "cambio": cambio, "modo": modo, "dias": dias, "stock": "" if stock is None else int(stock)}
        D, margen = float(rec.demanda_prevista), float(rec.stock_seguridad or 0)
        # el cambio se puede expresar en porcentaje, en unidades a sumar o restar, o como la demanda total del mes
        nueva = {"pct": D * (1 + cambio / 100.0), "unid": D + cambio, "total": cambio}[modo]
        base = simular(D, margen, rec.clase_abc, 0, PARAMETROS.tiempo_reposicion_dias, stock)
        escenario = simular(D, margen, rec.clase_abc, 0, dias, stock, demanda_escenario=max(0.0, nueva))
        escenario["cambio_unidades"] = escenario["demanda"] - D
        escenario["cambio_pct"] = ((escenario["demanda"] - D) / D * 100) if D > 0 else None
    return render_template("analitica/simulador.html", recs=recs, rec=rec, base=base, escenario=escenario,
                           ejecucion=_ejecucion_o_none(), parametros=PARAMETROS, form=form)


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
        return render_template("analitica/prediccion.html", predicciones=[], ejecucion=None, clases={}, modelo={}), 503

    # mayor demanda estimada primero, es la que mas le importa al gerente
    filas.sort(key=lambda p: -float(p.demanda_estimada))

    # vista previa con datos reales del historico (Excel), identificados por
    # nombre de producto mientras el catalogo real no este en MySQL -- ver
    # app/models/prediccion_demanda_preview.py. No crítica: si falla, la
    # pantalla igual muestra la tabla principal.
    # Con una ejecucion real del pipeline, la tabla principal YA trae datos reales por producto del
    # catalogo: la "vista previa" (que existia por no tener catalogo real) deja de hacer falta.
    ejecucion = _ejecucion_o_none()
    vista_previa = []
    clases = {}
    if ejecucion is not None:
        try:
            clases = {s.id_producto: s.clase_abc for s in SegmentacionProducto.query.all()}
        except SQLAlchemyError as error:
            registrar_error_conexion("leer clases ABC para la prediccion", error)
    else:
        try:
            vista_previa = (
                PrediccionDemandaPreview.query.order_by(PrediccionDemandaPreview.cantidad_sugerida.desc()).all()
            )
        except SQLAlchemyError as error:
            registrar_error_conexion("listar vista previa de predicciones", error)

    # eventos del negocio de los últimos 12 meses por producto: se avisa en la fila para interpretar meses atípicos
    eventos_por_producto = {}
    try:
        for e in EventoNegocio.query.filter(EventoNegocio.id_producto.isnot(None),
                                            EventoNegocio.fecha_inicio >= date.today() - timedelta(days=365)).all():
            eventos_por_producto.setdefault(e.id_producto, []).append(f"{e.fecha_inicio.strftime('%m/%Y')}: {'[Ejemplo] ' if e.es_ejemplo else ''}{e.descripcion}")
    except SQLAlchemyError as error:
        registrar_error_conexion("leer eventos del negocio para la prediccion", error)

    return render_template(
        "analitica/prediccion.html", predicciones=filas, vista_previa=vista_previa, ejecucion=ejecucion, clases=clases,
        modelo=_info_modelo(ejecucion), eventos_por_producto=eventos_por_producto,
    )


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
            "analitica/segmentacion.html", segmentaciones=[], filtro_clase=filtro_clase, ejecucion=None
        ), 503

    # orden de Pareto: de mayor a menor participacion en las ventas
    filas.sort(key=lambda s: float(s.pct_acumulado))

    # vista previa con datos reales del historico (Excel) -- ver
    # app/models/segmentacion_producto_preview.py. No critica: si falla, la
    # pantalla igual muestra la tabla principal.
    ejecucion = _ejecucion_o_none()
    vista_previa = []
    if ejecucion is None:
        try:
            consulta_preview = SegmentacionProductoPreview.query
            if filtro_clase in ("A", "B", "C"):
                consulta_preview = consulta_preview.filter(SegmentacionProductoPreview.clase_abc == filtro_clase)
            vista_previa = consulta_preview.order_by(SegmentacionProductoPreview.pct_acumulado).all()
        except SQLAlchemyError as error:
            registrar_error_conexion("listar vista previa de segmentacion", error)

    return render_template(
        "analitica/segmentacion.html",
        segmentaciones=filas,
        filtro_clase=filtro_clase,
        vista_previa=vista_previa,
        ejecucion=ejecucion,
    )
