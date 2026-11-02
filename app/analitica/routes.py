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
from decimal import Decimal

from flask import Blueprint, redirect, render_template, request, flash, url_for
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError

from app.decorators import rol_requerido
from app.errores_bd import MENSAJE_ERROR_CONEXION, registrar_error_conexion
from app.extensions import db
import json

from app import aciertos, ficha_producto as ficha
from app.alertas_ventas import detectar_ventas_inusuales
from app.parametros import parametros_vigentes
from app.simulador import ESCENARIOS, LIMITES, calcular, comparar_escenarios, leer_entrada
from app.servicios_analitica import ultima_ejecucion
from app.models import (
    DetalleVenta,
    EventoNegocio,
    Venta,
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


# cifras de las tarjetas de resumen de Recomendaciones: cuántos hay en cada nivel y cuánto habría que reponer
def resumir_recomendaciones(todas):
    por_nivel = {n: [r for r in todas if r.nivel_urgencia == n] for n in ("urgente", "media", "sin_accion", "plan")}
    a_reponer = por_nivel["urgente"] + por_nivel["media"]
    return {
        "urgentes": len(por_nivel["urgente"]), "medias": len(por_nivel["media"]), "sin_accion": len(por_nivel["sin_accion"]),
        "plan": len(por_nivel["plan"]),
        "clase_a_urgentes": sum(1 for r in por_nivel["urgente"] if r.clase_abc == "A"),
        "clase_a_plan": sum(1 for r in por_nivel["plan"] if r.clase_abc == "A"),
        "unidades_reponer": sum(r.cantidad_sugerida or 0 for r in a_reponer),
        # valor de referencia: a PRECIO DE VENTA (el sistema no guarda el costo de compra o producción)
        "valor_referencia": float(sum(Decimal(r.cantidad_sugerida or 0) * (r.producto.precio_unitario or 0) for r in a_reponer)),
        "unidades_plan": sum(r.stock_objetivo or 0 for r in por_nivel["plan"]),
    }


def _alertas_de_ventas(hoy=None):
    """
    Productos con ventas inusuales en el mes en curso. Solo compara cuando la predicción es PARA este mes (si la predicción es de otro
    mes no hay con qué comparar y no se avisa nada). Si algo falla, la pantalla igual se muestra, sin alertas.
    """
    import calendar

    hoy = hoy or date.today()
    inicio = hoy.replace(day=1)
    try:
        esperado = {}
        for p in (PrediccionDemanda.query.filter(PrediccionDemanda.periodo_objetivo == inicio)
                  .order_by(PrediccionDemanda.fecha_generacion).all()):
            esperado[p.id_producto] = (p.producto.nombre_producto, float(p.demanda_estimada))  # la última generación pisa a las anteriores
        if not esperado:
            return []
        vendido = dict(
            db.session.query(DetalleVenta.id_producto, db.func.sum(DetalleVenta.cantidad))
            .join(Venta, Venta.id_venta == DetalleVenta.id_venta)
            .filter(Venta.anulada.is_(False), Venta.fecha_hora >= inicio, DetalleVenta.id_producto.in_(list(esperado)))
            .group_by(DetalleVenta.id_producto).all()
        )
    except SQLAlchemyError as error:
        registrar_error_conexion("buscar ventas inusuales del mes", error)
        db.session.rollback()
        return []
    productos = [{"id_producto": i, "nombre": n, "vendido": vendido.get(i, 0), "esperado": e} for i, (n, e) in esperado.items()]
    return detectar_ventas_inusuales(productos, hoy.day, calendar.monthrange(hoy.year, hoy.month)[1])


# pantalla de recomendaciones de abastecimiento: cruza demanda prevista vs.
# stock actual y explica en texto plano por que se sugiere cada accion
@bp.route("")
@login_required
@rol_requerido("Administrador")
def recomendaciones():
    filtro_urgencia = request.args.get("urgencia", "").strip()

    try:
        # se traen todas (son una por producto) para que las tarjetas de resumen cuenten el total sin importar el filtro elegido
        todas = RecomendacionAbastecimiento.query.options(
            db.joinedload(RecomendacionAbastecimiento.producto),
            db.joinedload(RecomendacionAbastecimiento.sede),
        ).all()
    except SQLAlchemyError as error:
        registrar_error_conexion("listar recomendaciones de abastecimiento", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template(
            "analitica/recomendaciones.html", recomendaciones=[], filtro_urgencia=filtro_urgencia, ejecucion=None,
            parametros=parametros_vigentes(), hay_plan=False, hay_inventario=False, resumen=None,
        ), 503

    hay_plan = any(r.nivel_urgencia == "plan" for r in todas)
    hay_inventario = any(r.con_inventario for r in todas)
    resumen = resumir_recomendaciones(todas)
    filas = [r for r in todas if filtro_urgencia not in ("urgente", "media", "sin_accion", "plan") or r.nivel_urgencia == filtro_urgencia]

    # las mas urgentes arriba, y dentro de cada nivel, la que tiene mayor
    # demanda prevista primero (es la que mas impacto tiene si se agota)
    filas.sort(key=lambda r: (
        ORDEN_URGENCIA.get(r.nivel_urgencia, 99), r.clase_abc or "Z", -float(r.demanda_prevista),
    ))

    return render_template(
        "analitica/recomendaciones.html", recomendaciones=filas, filtro_urgencia=filtro_urgencia,
        ejecucion=_ejecucion_o_none(), parametros=parametros_vigentes(), hay_plan=hay_plan, hay_inventario=hay_inventario,
        resumen=resumen, alertas_ventas=_alertas_de_ventas(),
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


# simulador de escenarios: el usuario elige un producto y un escenario listo y ve, en palabras y en un gráfico, cuánto se vendería,
# cuándo se agotaría el stock y cuánto conviene reponer (no guarda nada; sirve para decidir "qué pasaría si ...")
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
        return render_template("analitica/simulador.html", recs=[], rec=None, resultado=None, entrada=None, ejecucion=None,
                               parametros=parametros_vigentes(), escenarios=ESCENARIOS, limites=LIMITES, stock_registrado=None), 503

    id_txt = request.args.get("id_producto", "").strip()
    rec = next((r for r in recs if str(r.id_producto) == id_txt), None)
    resultado = entrada = stock_registrado = None
    if rec is not None:
        # el stock registrado es el valor de partida; el usuario puede escribir otro (por ejemplo, el que contó hoy)
        stock_registrado = rec.stock_actual if rec.con_inventario else None
        entrada = leer_entrada(request.args, stock_registrado)
        resultado = calcular(rec, entrada)
        resultado["comparacion"] = comparar_escenarios(rec, entrada) if entrada["stock"] is not None else None
    return render_template("analitica/simulador.html", recs=recs, rec=rec, resultado=resultado, entrada=entrada,
                           ejecucion=_ejecucion_o_none(), parametros=parametros_vigentes(), escenarios=ESCENARIOS, limites=LIMITES,
                           stock_registrado=stock_registrado)


# ficha de un producto: venta esperada con su rango probable, stock y riesgo de quedarse sin producto, ventas de los últimos
# 12 meses y eventos que explican meses atípicos (cálculos en app/ficha_producto.py)
@bp.route("/producto/<int:id_producto>")
@login_required
@rol_requerido("Administrador")
def ficha_de_producto(id_producto):
    try:
        producto = db.get_or_404(Producto, id_producto)
        rec = (RecomendacionAbastecimiento.query.filter_by(id_producto=id_producto)
               .order_by(RecomendacionAbastecimiento.fecha_generacion.desc()).first())
        pred = (PrediccionDemanda.query.filter_by(id_producto=id_producto)
                .order_by(PrediccionDemanda.fecha_generacion.desc()).first())
        # la ventana de 12 meses termina en el mes de la última venta registrada (no en hoy): si los datos llegan hasta agosto,
        # septiembre y octubre no se muestran como «cero ventas» cuando en realidad todavía no se cargaron
        ultima_venta = db.session.query(db.func.max(Venta.fecha_hora)).filter(Venta.anulada.is_(False)).scalar()
        hoy = ultima_venta.date() if ultima_venta is not None else date.today()
        desde = ficha.inicio_de_ventana(hoy)
        ventas = (db.session.query(Venta.fecha_hora, DetalleVenta.cantidad)
                  .join(DetalleVenta, DetalleVenta.id_venta == Venta.id_venta)
                  .filter(DetalleVenta.id_producto == id_producto, Venta.anulada.is_(False), Venta.fecha_hora >= desde).all())
        eventos = (EventoNegocio.query.filter(EventoNegocio.id_producto == id_producto,
                                              EventoNegocio.fecha_inicio >= desde)
                   .order_by(EventoNegocio.fecha_inicio.desc()).all())
    except SQLAlchemyError as error:
        registrar_error_conexion("abrir la ficha de un producto", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return redirect(url_for("analitica.recomendaciones"))

    parametros = parametros_vigentes()
    demanda = float(pred.demanda_estimada) if pred is not None else (float(rec.demanda_prevista) if rec is not None else None)
    confiabilidad = (pred.confiabilidad if pred is not None else None) or (rec.confiabilidad if rec is not None else None)
    rango = riesgo = None
    if demanda is not None:
        error = ficha.error_relativo(pred.error_validacion if pred is not None else None, confiabilidad)
        rango = {"error_pct": round(error * 100), "minimo_maximo": ficha.rango_probable(demanda, error)}
        stock = rec.stock_actual if rec is not None and rec.con_inventario else None
        riesgo = ficha.riesgo_de_quiebre(stock, demanda, error, parametros.tiempo_reposicion_dias, parametros.demanda_minima)
    meses = ficha.ventas_por_mes([(f.date(), c) for f, c in ventas], hoy)
    try:
        historial = _comparar_pronosticos_pasados(date.today(), id_producto)[:6]
    except SQLAlchemyError as error:
        registrar_error_conexion("leer los aciertos pasados de un producto", error)
        db.session.rollback()
        historial = []
    return render_template(
        "analitica/ficha_producto.html", producto=producto, rec=rec, pred=pred, demanda=demanda, confiabilidad=confiabilidad,
        rango=rango, riesgo=riesgo, parametros=parametros, meses=meses, patron=ficha.patron_de_demanda(meses),
        eventos=eventos, ejecucion=_ejecucion_o_none(), historial=historial,
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
        return render_template("analitica/prediccion.html", predicciones=[], ejecucion=None, clases={}, modelo={}), 503

    # mayor demanda estimada primero, es la que mas le importa al gerente
    filas.sort(key=lambda p: -float(p.demanda_estimada))

    # cifras de las tarjetas de resumen
    resumen = {
        "unidades": sum(float(p.demanda_estimada) for p in filas),
        "periodo": next((p.periodo_objetivo for p in filas if p.periodo_objetivo), None),
        "alta": sum(1 for p in filas if p.confiabilidad == "Alta"),
        "media": sum(1 for p in filas if p.confiabilidad == "Media"),
        "baja": sum(1 for p in filas if p.confiabilidad not in ("Alta", "Media")),
        "sube": sum(1 for p in filas if p.venta_mes_anterior and p.tendencia_pct and p.tendencia_pct > 0),
        "baja_tendencia": sum(1 for p in filas if p.venta_mes_anterior and p.tendencia_pct and p.tendencia_pct < 0),
    }

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
        modelo=_info_modelo(ejecucion), eventos_por_producto=eventos_por_producto, resumen=resumen,
    )


# predicciones de meses ya terminados contra lo que de verdad se vendió (cálculos en app/aciertos.py); con id_producto se
# limita a un solo producto. Devuelve filas ordenadas del mes más reciente al más antiguo.
def _comparar_pronosticos_pasados(hoy, id_producto=None):
    consulta = (PrediccionDemanda.query.options(db.joinedload(PrediccionDemanda.producto))
                .filter(PrediccionDemanda.periodo_objetivo.isnot(None),
                        PrediccionDemanda.periodo_objetivo < hoy.replace(day=1)))
    if id_producto is not None:
        consulta = consulta.filter(PrediccionDemanda.id_producto == id_producto)
    ultimas = {}
    for p in consulta.order_by(PrediccionDemanda.fecha_generacion).all():
        ultimas[(p.id_producto, p.periodo_objetivo)] = p  # si se recalculó el mismo mes, vale el último pronóstico
    if not ultimas:
        return []
    desde = min(periodo for _, periodo in ultimas)
    anio, mes = db.func.extract("year", Venta.fecha_hora), db.func.extract("month", Venta.fecha_hora)
    consulta_real = (db.session.query(DetalleVenta.id_producto, anio, mes, db.func.sum(DetalleVenta.cantidad))
                     .join(Venta, Venta.id_venta == DetalleVenta.id_venta)
                     .filter(Venta.anulada.is_(False), Venta.fecha_hora >= desde, Venta.fecha_hora < hoy.replace(day=1)))
    if id_producto is not None:
        consulta_real = consulta_real.filter(DetalleVenta.id_producto == id_producto)
    real = {(i, int(a), int(m)): c for i, a, m, c in consulta_real.group_by(DetalleVenta.id_producto, anio, mes).all()}
    # un mes sin ninguna venta registrada en toda la plataforma no se compara: vender 0 caería "dentro del rango" de los productos
    # de pronóstico pequeño y aparentaría un acierto que no existe
    meses_con_ventas = {(int(a), int(m)) for a, m in db.session.query(anio, mes)
                        .filter(Venta.anulada.is_(False), Venta.fecha_hora >= desde, Venta.fecha_hora < hoy.replace(day=1))
                        .group_by(anio, mes).all()}
    filas = []
    for (i, periodo), p in ultimas.items():
        if (periodo.year, periodo.month) not in meses_con_ventas:
            continue
        fila =aciertos.evaluar(p.demanda_estimada, real.get((i, periodo.year, periodo.month), 0), p.error_validacion, p.confiabilidad)
        fila.update(id_producto=i, nombre=p.producto.nombre_producto, periodo=periodo)
        filas.append(fila)
    filas.sort(key=lambda f: (f["periodo"], f["estimado"]), reverse=True)
    return filas


# historial de aciertos: por cada mes que ya terminó, cuánto se esperaba vender, cuánto se vendió y en qué productos falló más
@bp.route("/aciertos")
@login_required
@rol_requerido("Administrador")
def historial_aciertos():
    try:
        filas = _comparar_pronosticos_pasados(date.today())
    except SQLAlchemyError as error:
        registrar_error_conexion("calcular el historial de aciertos", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template("analitica/aciertos.html", meses=[], ejecucion=None), 503
    por_mes = {}
    for f in filas:
        por_mes.setdefault(f["periodo"], []).append(f)
    meses = []
    for periodo, productos in por_mes.items():
        resumen = aciertos.resumir_periodo(productos)
        # los que más se desviaron en unidades: es donde conviene mirar primero
        mayores = sorted(productos, key=lambda f: -abs(f["diferencia"]))[:5]
        meses.append({"periodo": periodo, "resumen": resumen, "texto": aciertos.texto_acierto(resumen), "mayores": mayores})
    return render_template("analitica/aciertos.html", meses=meses, ejecucion=_ejecucion_o_none())


# pantalla de segmentacion ABC: clasificacion de productos segun su
# participacion acumulada en las ventas del periodo (Pareto 80/15/5)
@bp.route("/segmentacion")
@login_required
@rol_requerido("Administrador")
def segmentacion():
    filtro_clase = request.args.get("clase", "").strip().upper()

    try:
        # se traen todos (uno por producto) para que las tarjetas de resumen cuenten el total sin importar el filtro de clase
        todas = SegmentacionProducto.query.options(db.joinedload(SegmentacionProducto.producto)).all()
    except SQLAlchemyError as error:
        registrar_error_conexion("listar segmentacion de productos", error)
        flash(MENSAJE_ERROR_CONEXION, "danger")
        return render_template(
            "analitica/segmentacion.html", segmentaciones=[], filtro_clase=filtro_clase, ejecucion=None, resumen=None
        ), 503

    filas = [s for s in todas if filtro_clase not in ("A", "B", "C") or s.clase_abc == filtro_clase]
    # orden de Pareto: de mayor a menor participacion en las ventas
    filas.sort(key=lambda s: float(s.pct_acumulado))

    # cifras de las tarjetas de resumen: cuántos productos hay en cada clase y qué parte de las ventas concentra cada una
    ventas_total = sum(float(s.ventas_periodo) for s in todas)
    por_clase = {c: [s for s in todas if s.clase_abc == c] for c in ("A", "B", "C")}
    resumen = {
        "total": len(todas), "ventas_total": ventas_total,
        "n": {c: len(v) for c, v in por_clase.items()},
        "ventas_pct": {c: (sum(float(s.ventas_periodo) for s in v) / ventas_total * 100 if ventas_total else 0) for c, v in por_clase.items()},
        "sin_ventas": sum(1 for s in todas if float(s.ventas_periodo) == 0),
    }

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
        resumen=resumen,
    )
