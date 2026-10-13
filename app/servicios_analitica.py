"""
Puente entre el pipeline de analítica (analitica_job/pipeline, que trabaja con
Excel y DataFrames, sin base de datos) y las tablas de MySQL que lee la
aplicación: carga del catálogo de productos base, inventario, y escritura de
predicciones, segmentación ABC y recomendaciones de abastecimiento.

Lo usan los comandos de app/comandos_analitica.py; no se llama desde las
pantallas (las pantallas solo leen).
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import inspect, text

from app.extensions import db
from app.models import (
    Categoria, Cliente, Credito, DetalleVenta, EjecucionAnalitica, EventoNegocio, PlanCampana, PrediccionDemanda, Producto,
    Recuperacion, RecomendacionAbastecimiento, Sede, SegmentacionProducto, StockPorSede, UnidadMedida, Venta,
)

HORIZONTE_SEMANAS = 4  # el pronóstico es del mes siguiente (≈ 4 semanas)
SEDE_PRINCIPAL = "Pimentel"

COLUMNAS_NUEVAS_RECOMENDACION = {
    "clase_abc": "VARCHAR(1) NULL",
    "stock_objetivo": "INT NULL",
    "stock_seguridad": "INT NULL",
    "punto_pedido": "INT NULL",
    "confiabilidad": "VARCHAR(10) NULL",
    "reparto": "VARCHAR(250) NULL",
    "con_inventario": "TINYINT(1) NOT NULL DEFAULT 0",
}

COLUMNA_NUEVA_STOCK = ("origen", "VARCHAR(15) NOT NULL DEFAULT 'real'")

COLUMNAS_NUEVAS_PREDICCION = {
    "periodo_objetivo": "DATE NULL",
    "venta_mes_anterior": "DECIMAL(10,2) NULL",
    "confiabilidad": "VARCHAR(10) NULL",
    "metodo": "VARCHAR(30) NULL",
    "error_validacion": "DECIMAL(6,2) NULL",
}


class ErrorAnalitica(Exception):
    """Problema esperable (no un fallo del programa): se muestra tal cual al usuario del comando."""


def asegurar_esquema() -> list[str]:
    """Crea la tabla de ejecuciones y agrega a predicciones_demanda las columnas del pipeline real (idempotente)."""
    cambios = []
    inspector = inspect(db.engine)
    if not inspector.has_table("ejecuciones_analitica"):
        EjecucionAnalitica.__table__.create(bind=db.engine, checkfirst=True)
        cambios.append("tabla ejecuciones_analitica")
    if not inspector.has_table("plan_campana"):
        PlanCampana.__table__.create(bind=db.engine, checkfirst=True)
        cambios.append("tabla plan_campana")
    if not inspector.has_table("eventos_negocio"):
        EventoNegocio.__table__.create(bind=db.engine, checkfirst=True)
        cambios.append("tabla eventos_negocio")
    elif "es_ejemplo" not in {c["name"] for c in inspect(db.engine).get_columns("eventos_negocio")}:
        db.session.execute(text("ALTER TABLE eventos_negocio ADD COLUMN es_ejemplo TINYINT(1) NOT NULL DEFAULT 0"))
        cambios.append("columna eventos_negocio.es_ejemplo")
    existentes = {c["name"] for c in inspector.get_columns("predicciones_demanda")}
    for nombre, ddl in COLUMNAS_NUEVAS_PREDICCION.items():
        if nombre not in existentes:
            db.session.execute(text(f"ALTER TABLE predicciones_demanda ADD COLUMN {nombre} {ddl}"))
            cambios.append(f"columna predicciones_demanda.{nombre}")
    if COLUMNA_NUEVA_STOCK[0] not in {c["name"] for c in inspector.get_columns("stock_por_sede")}:
        db.session.execute(text(f"ALTER TABLE stock_por_sede ADD COLUMN {COLUMNA_NUEVA_STOCK[0]} {COLUMNA_NUEVA_STOCK[1]}"))
        cambios.append("columna stock_por_sede.origen")

    # recomendaciones: ahora pueden existir sin inventario (modo "plan"), con un motivo más largo
    existentes_rec = {c["name"] for c in inspector.get_columns("recomendaciones_abastecimiento")}
    for nombre, ddl in COLUMNAS_NUEVAS_RECOMENDACION.items():
        if nombre not in existentes_rec:
            db.session.execute(text(f"ALTER TABLE recomendaciones_abastecimiento ADD COLUMN {nombre} {ddl}"))
            cambios.append(f"columna recomendaciones_abastecimiento.{nombre}")
    if db.engine.dialect.name == "mysql":
        # solo se altera lo que de verdad falta: así, con la estructura al día, esta función no ejecuta ningún cambio de
        # estructura y la actualización mensual de la analítica puede correr con el usuario de MySQL de permisos mínimos
        tipos = {
            f[0]: str(f[1]).lower() for f in db.session.execute(text(
                "SELECT column_name, column_type FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'recomendaciones_abastecimiento'"
            )).all()
        }
        columnas_rec = {c["name"]: c for c in inspect(db.engine).get_columns("recomendaciones_abastecimiento")}
        if not columnas_rec["stock_actual"]["nullable"]:
            db.session.execute(text("ALTER TABLE recomendaciones_abastecimiento MODIFY stock_actual INT NULL"))
            cambios.append("recomendaciones_abastecimiento.stock_actual acepta vacío")
        if "'plan'" not in tipos.get("nivel_urgencia", ""):
            db.session.execute(text(
                "ALTER TABLE recomendaciones_abastecimiento MODIFY nivel_urgencia "
                "ENUM('urgente','media','sin_accion','plan') NOT NULL"
            ))
            cambios.append("recomendaciones_abastecimiento.nivel_urgencia acepta 'plan'")
        if tipos.get("motivo") != "varchar(400)":
            db.session.execute(text("ALTER TABLE recomendaciones_abastecimiento MODIFY motivo VARCHAR(400) NULL"))
            cambios.append("recomendaciones_abastecimiento.motivo de 400 caracteres")
    db.session.commit()
    return cambios


def _sede_principal() -> Sede:
    sede = Sede.query.filter(Sede.nombre_sede.ilike(f"%{SEDE_PRINCIPAL}%")).first()
    if sede is None:
        raise ErrorAnalitica(f"No existe una sede que contenga '{SEDE_PRINCIPAL}' en su nombre.")
    return sede


# ---------------------------------------------------------------------------
# Catálogo de productos base
# ---------------------------------------------------------------------------
def conteo_datos_de_prueba() -> dict[str, int]:
    return {
        "productos": Producto.query.count(), "ventas": Venta.query.count(), "detalles_de_venta": DetalleVenta.query.count(),
        "creditos": Credito.query.count(), "recuperaciones": Recuperacion.query.count(),
        "stock_por_sede": StockPorSede.query.count(), "predicciones": PrediccionDemanda.query.count(),
        "recomendaciones": RecomendacionAbastecimiento.query.count(), "segmentaciones": SegmentacionProducto.query.count(),
    }


def _borrar_datos_de_prueba() -> None:
    """Orden seguro respecto de las llaves foráneas. NO borra clientes ni usuarios."""
    for modelo in (Recuperacion, Credito, DetalleVenta, Venta, StockPorSede, RecomendacionAbastecimiento,
                   PrediccionDemanda, SegmentacionProducto, Producto):
        db.session.query(modelo).delete()
    db.session.flush()


def cargar_catalogo(hojas: dict[str, pd.DataFrame], aplicar: bool = False, reemplazar_prueba: bool = False,
                    respaldo_previo=None) -> dict:
    """
    Carga los productos BASE del catálogo en `productos`. Sin `aplicar` solo informa qué haría.

    - Si la base ya tiene productos de otro origen (datos de prueba), exige `reemplazar_prueba`:
      hace antes un respaldo (`respaldo_previo()`), y borra productos, stock, ventas, créditos,
      cobros y resultados de analítica de prueba (no clientes ni usuarios).
    - La carga es idempotente: un producto cuyo nombre ya existe se actualiza, no se duplica.
    """
    prod = hojas["productos"]
    pres = hojas["presentaciones"]
    existentes = {p.nombre_producto for p in Producto.query.all()}
    nombres_catalogo = set(prod["producto_base"])
    ajenos = existentes - nombres_catalogo
    resumen = {
        "productos_catalogo": int(len(prod)), "ya_existentes": len(existentes & nombres_catalogo),
        "nuevos": len(nombres_catalogo - existentes), "productos_ajenos_en_bd": len(ajenos),
        "datos_actuales": conteo_datos_de_prueba(), "aplicado": False,
    }
    if not aplicar:
        return resumen

    if ajenos and not reemplazar_prueba:
        raise ErrorAnalitica(
            f"La base tiene {len(ajenos)} productos que no son del catálogo real (probablemente datos de prueba) y "
            f"{resumen['datos_actuales']['ventas']} ventas. Para reemplazarlos repite el comando con --reemplazar-prueba "
            "(se hace un respaldo antes)."
        )
    if reemplazar_prueba and ajenos:
        if respaldo_previo is not None:
            resumen["respaldo"] = str(respaldo_previo())
        _borrar_datos_de_prueba()

    unidad = UnidadMedida.query.filter_by(nombre_unidad="Unidad").first()
    if unidad is None:
        unidad = UnidadMedida(nombre_unidad="Unidad", abreviatura="Un")
        db.session.add(unidad)
        db.session.flush()

    categorias = {}
    for nombre in sorted(set(prod["categoria_sistema"])):
        c = Categoria.query.filter_by(nombre_categoria=nombre).first()
        if c is None:
            c = Categoria(nombre_categoria=nombre)
            db.session.add(c)
            db.session.flush()
        categorias[nombre] = c

    por_nombre = {p.nombre_producto: p for p in Producto.query.all()}
    creados = actualizados = 0
    for r in prod.itertuples(index=False):
        presentaciones = " ; ".join(pres.loc[pres["producto_base"] == r.producto_base, "presentacion"])
        datos = dict(
            codigo_producto=r.codigo_producto, nombre_producto=r.producto_base[:80],
            id_categoria=categorias[r.categoria_sistema].id_categoria, id_unidad=unidad.id_unidad,
            precio_unitario=round(float(r.precio_referencia), 2) if pd.notna(r.precio_referencia) else 0,
            stock_minimo=int(math.ceil(float(r.unidades_ult_12m) / 12)),
            descripcion=("Presentaciones: " + presentaciones)[:200], estado=True,
        )
        existente = por_nombre.get(r.producto_base)
        if existente is None:
            db.session.add(Producto(**datos))
            creados += 1
        else:
            for campo, valor in datos.items():
                if campo != "codigo_producto":
                    setattr(existente, campo, valor)
            actualizados += 1

    # categorías de prueba que quedaron sin productos
    for c in Categoria.query.all():
        if c.nombre_categoria not in categorias and not Producto.query.filter_by(id_categoria=c.id_categoria).count():
            db.session.delete(c)
    db.session.commit()
    resumen.update(aplicado=True, creados=creados, actualizados=actualizados)
    return resumen


# ---------------------------------------------------------------------------
# Inventario (stock actual) desde Excel
# ---------------------------------------------------------------------------
def generar_plantilla_inventario(hojas: dict[str, pd.DataFrame], ruta: Path) -> Path:
    """Excel para que el gerente anote el stock actual por producto y presentación."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    pres = hojas["presentaciones"][["producto_base", "presentacion"]].copy()
    prod = hojas["productos"][["producto_base", "categoria_sistema", "unidades_ult_12m"]]
    plantilla = pres.merge(prod, on="producto_base", how="left")
    plantilla["stock_actual"] = None
    plantilla["observaciones"] = None
    plantilla = plantilla.rename(columns={"categoria_sistema": "categoria", "unidades_ult_12m": "ventas_ult_12_meses (referencia)"})
    instrucciones = pd.DataFrame({"Cómo llenar": [
        "1. Anote en 'stock_actual' cuántas unidades hay HOY en la sede, por cada presentación (frascos, sacos, cajas: lo que se cuente al vender).",
        "2. Si un producto no se tiene, ponga 0. No deje en blanco los que sí maneje.",
        "3. No cambie los nombres de producto ni de presentación (el sistema los usa para reconocerlos).",
        "4. Indique la fecha del conteo en la hoja 'Datos del conteo'.",
        "5. Si hay más de una sede con inventario, copie este archivo y llénelo por separado indicando la sede.",
    ]})
    datos = pd.DataFrame({"campo": ["sede", "fecha_del_conteo"], "valor": [SEDE_PRINCIPAL, date.today().isoformat()]})
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(ruta, engine="openpyxl") as w:
        instrucciones.to_excel(w, sheet_name="Instrucciones", index=False)
        datos.to_excel(w, sheet_name="Datos del conteo", index=False)
        plantilla.to_excel(w, sheet_name="Inventario", index=False)
        for nombre, df in (("Instrucciones", instrucciones), ("Datos del conteo", datos), ("Inventario", plantilla)):
            ws = w.sheets[nombre]
            for celda in ws[1]:
                celda.font = Font(bold=True, color="FFFFFF")
                celda.fill = PatternFill("solid", fgColor="1F5C3B")
                celda.alignment = Alignment(wrap_text=True)
            for i, col in enumerate(df.columns, start=1):
                ws.column_dimensions[get_column_letter(i)].width = 100 if nombre == "Instrucciones" else min(max(len(str(col)) + 2, 14), 45)
            ws.freeze_panes = "A2"
    return ruta


def cargar_inventario(ruta: Path, aplicar: bool = False) -> dict:
    """Lee el Excel de inventario y deja el stock por producto base (suma de sus presentaciones) en `stock_por_sede`."""
    libro = pd.ExcelFile(ruta)
    inv = libro.parse("Inventario")
    datos = dict(libro.parse("Datos del conteo").set_index("campo")["valor"]) if "Datos del conteo" in libro.sheet_names else {}
    sede = Sede.query.filter(Sede.nombre_sede.ilike(f"%{datos.get('sede', SEDE_PRINCIPAL)}%")).first()
    if sede is None:
        raise ErrorAnalitica(f"No encontré la sede '{datos.get('sede')}' en la base de datos.")

    inv = inv.dropna(subset=["stock_actual"])
    inv["stock_actual"] = pd.to_numeric(inv["stock_actual"], errors="coerce")
    malos = inv[inv["stock_actual"].isna() | (inv["stock_actual"] < 0)]
    if len(malos):
        raise ErrorAnalitica(f"Hay {len(malos)} filas con stock vacío o negativo (ej. {malos.iloc[0]['producto_base']}).")
    por_base = inv.groupby("producto_base")["stock_actual"].sum()

    productos = {p.nombre_producto: p for p in Producto.query.all()}
    desconocidos = sorted(set(por_base.index) - set(productos))
    resumen = {"sede": sede.nombre_sede, "productos_con_stock": int(len(por_base)), "desconocidos": desconocidos, "aplicado": False}
    if not aplicar:
        return resumen

    for nombre, cantidad in por_base.items():
        producto = productos.get(nombre)
        if producto is None:
            continue
        fila = StockPorSede.query.filter_by(id_producto=producto.id_producto, id_sede=sede.id_sede).first()
        if fila is None:
            db.session.add(StockPorSede(id_producto=producto.id_producto, id_sede=sede.id_sede,
                                        cantidad_actual=int(round(cantidad)), origen="real"))
        else:
            fila.cantidad_actual = int(round(cantidad))
            fila.origen = "real"
    db.session.commit()
    resumen["aplicado"] = True
    return resumen


def inventario_provisional_en_uso() -> int:
    """Cuántos productos tienen hoy un stock PROVISIONAL (para avisarlo en pantalla)."""
    try:
        return StockPorSede.query.filter_by(origen="provisional").count()
    except Exception:  # la columna puede no existir todavía en una base sin migrar
        db.session.rollback()
        return 0


def generar_inventario_provisional(hojas: dict[str, pd.DataFrame], aplicar: bool = False, meses_cobertura: float = 1.5,
                                   minimo: int = 5) -> dict:
    """
    La empresa no lleva registro de inventario, así que mientras no exista un conteo real se estima un stock
    PROVISIONAL por producto: `meses_cobertura` meses de la venta promedio mensual de los últimos 12 meses
    (mínimo `minimo` unidades para que se pueda vender). Queda marcado origen="provisional"; nunca pisa un stock
    "real", y se reemplaza solo al cargar el conteo real.
    """
    sede = _sede_principal()
    prod = hojas["productos"][["producto_base", "unidades_ult_12m"]]
    productos = {p.nombre_producto: p for p in Producto.query.all()}
    filas = []
    for r in prod.itertuples(index=False):
        p = productos.get(r.producto_base)
        if p is None:
            continue
        filas.append((p, max(int(minimo), int(math.ceil(float(r.unidades_ult_12m) / 12 * meses_cobertura)))))
    resumen = {"sede": sede.nombre_sede, "productos": len(filas), "unidades_totales": sum(c for _, c in filas),
               "meses_cobertura": meses_cobertura, "minimo": minimo, "aplicado": False, "conservados_reales": 0}
    if not aplicar:
        return resumen
    for p, cantidad in filas:
        fila = StockPorSede.query.filter_by(id_producto=p.id_producto, id_sede=sede.id_sede).first()
        if fila is None:
            db.session.add(StockPorSede(id_producto=p.id_producto, id_sede=sede.id_sede, cantidad_actual=cantidad, origen="provisional"))
        elif fila.origen == "real":
            resumen["conservados_reales"] += 1  # un conteo real nunca se pisa con una estimación
        else:
            fila.cantidad_actual = cantidad
    db.session.commit()
    resumen["aplicado"] = True
    return resumen


# ---------------------------------------------------------------------------
# Resultados del pipeline -> tablas
# ---------------------------------------------------------------------------
def guardar_resultado(res) -> dict:
    """Escribe predicciones, segmentación ABC, recomendaciones y el registro de la ejecución."""
    from analitica_job.pipeline.recomendaciones import generar

    asegurar_esquema()
    productos = {p.nombre_producto: p for p in Producto.query.all()}
    pred, abc = res.prediccion, res.abc
    faltantes = sorted((set(pred["producto"]) | set(abc["base"])) - set(productos))

    ahora = datetime.now()
    mes_objetivo = datetime.strptime(pred["periodo_objetivo"].iloc[0], "%Y-%m").date().replace(day=1)
    for r in pred.itertuples(index=False):
        p = productos.get(r.producto)
        if p is None:
            continue
        db.session.add(PrediccionDemanda(
            id_producto=p.id_producto, fecha_generacion=ahora, horizonte_semanas=HORIZONTE_SEMANAS,
            demanda_estimada=round(float(r.pronostico), 2), mape=None,
            tendencia_pct=None if pd.isna(r.tendencia_pct) else float(r.tendencia_pct),
            periodo_objetivo=mes_objetivo, venta_mes_anterior=round(float(r.venta_mes_anterior), 2),
            confiabilidad=r.confiabilidad, metodo=r.metodo,
            error_validacion=None if pd.isna(r.error_validacion_pct) else min(round(float(r.error_validacion_pct), 2), 9999.99),
        ))

    db.session.query(SegmentacionProducto).delete()  # la pantalla muestra todas las filas: se reemplaza completa
    for r in abc.itertuples(index=False):
        p = productos.get(r.base)
        if p is None:
            continue
        db.session.add(SegmentacionProducto(
            id_producto=p.id_producto, periodo_inicio=date.fromisoformat(r.periodo_inicio), periodo_fin=date.fromisoformat(r.periodo_fin),
            ventas_periodo=round(float(r.ventas_periodo), 2), pct_acumulado=min(float(r.pct_acumulado), 999.99), clase_abc=r.clase_abc,
        ))
    db.session.flush()

    n_recomendaciones = _guardar_recomendaciones(res, productos, generar)
    n_plan = _guardar_plan_campana(res, productos)

    db.session.add(EjecucionAnalitica(
        datos_hasta=res.informe_calidad["periodo"].split(" a ")[-1], entrenado_desde=res.anio_desde, productos=int(len(pred)),
        metodo_por_clase=json.dumps(res.metodo_por_clase, ensure_ascii=False), duracion_segundos=res.segundos,
        resumen=json.dumps({
            "calidad": {k: v for k, v in res.informe_calidad.items() if k != "nombres_no_mapeados"},
            "metricas_clase": res.metricas_clase.round(2).to_dict(orient="records"),
            "confiabilidad": pred["confiabilidad"].value_counts().to_dict(),
            "campana": {k: {c: v for c, v in d.items() if c in ("campana_id", "nombre", "campana_prueba", "errores", "ritmo_global")}
                        for k, d in res.meta_campana.items() if isinstance(d, dict)},
        }, ensure_ascii=False, default=str),
    ))
    db.session.commit()
    return {
        "predicciones": int(pred["producto"].isin(productos).sum()), "segmentaciones": int(abc["base"].isin(productos).sum()),
        "recomendaciones": n_recomendaciones, "plan_campana": n_plan, "productos_sin_catalogo": faltantes,
    }


def ventas_sistema_mensual() -> pd.DataFrame:
    """
    Ventas registradas en la plataforma (no las importadas del histórico) en meses COMPLETOS posteriores a los Excel, por producto base
    y mes. Alimentan la analítica para que aprenda de lo que se vende después de los Excel.
    """
    from analitica_job.pipeline import limpieza

    inicio = date(limpieza.ULTIMO_ANIO_REAL + (limpieza.ULTIMO_MES_REAL // 12), limpieza.ULTIMO_MES_REAL % 12 + 1, 1)
    hoy = date.today()
    fin = date(hoy.year, hoy.month, 1)  # el mes en curso todavía no está completo
    columnas = ["base", "anio", "mes", "categoria", "unidades", "soles"]
    if inicio >= fin:
        return pd.DataFrame(columns=columnas)
    filas = (
        db.session.query(
            Producto.nombre_producto, db.extract("year", Venta.fecha_hora), db.extract("month", Venta.fecha_hora), Categoria.nombre_categoria,
            db.func.sum(DetalleVenta.cantidad), db.func.sum(DetalleVenta.subtotal),
        )
        .join(DetalleVenta, DetalleVenta.id_producto == Producto.id_producto)
        .join(Venta, Venta.id_venta == DetalleVenta.id_venta)
        .join(Categoria, Categoria.id_categoria == Producto.id_categoria)
        .filter(Venta.fecha_hora >= inicio, Venta.fecha_hora < fin, ~Venta.numero_operacion.like("H%"), Venta.anulada.is_(False))
        .group_by(Producto.nombre_producto, db.extract("year", Venta.fecha_hora), db.extract("month", Venta.fecha_hora), Categoria.nombre_categoria)
        .all()
    )
    import unicodedata

    def sin_tilde(t):
        return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn").upper()

    return pd.DataFrame([(b, int(a), int(m), sin_tilde(c), float(u), float(s)) for b, a, m, c, u, s in filas], columns=columnas)


def ajustes_por_eventos() -> pd.DataFrame:
    """Demanda no atendida (quiebres de stock con cantidad) registrada en Eventos del negocio, por producto base y mes."""
    filas = []
    eventos = EventoNegocio.query.filter(EventoNegocio.tipo == "quiebre_stock", EventoNegocio.cantidad_no_atendida.isnot(None),
                                         EventoNegocio.id_producto.isnot(None), EventoNegocio.es_ejemplo.is_(False)).all()
    for e in eventos:
        filas.append({"base": e.producto.nombre_producto, "anio": e.fecha_inicio.year, "mes": e.fecha_inicio.month,
                      "unidades": float(e.cantidad_no_atendida)})
    return pd.DataFrame(filas, columns=["base", "anio", "mes", "unidades"])


def _guardar_recomendaciones(res, productos, generar) -> int:
    """
    Una recomendación por producto de la sede principal: con inventario cargado se compara contra el
    stock; sin inventario se entrega el plan (cuánto tener disponible). Nunca se inventa un stock.
    """
    db.session.query(RecomendacionAbastecimiento).delete()
    sede = _sede_principal()
    pred = res.prediccion
    demanda = pred.rename(columns={"pronostico": "demanda_prevista", "error_validacion_pct": "error_pct"})[
        ["producto", "clase_abc", "demanda_prevista", "mae_validacion", "confiabilidad"]
    ]
    stock = {s.id_producto: s.cantidad_actual for s in StockPorSede.query.filter_by(id_sede=sede.id_sede).all()}
    inventario = pd.DataFrame(
        [{"producto": n, "stock_actual": stock[p.id_producto]} for n, p in productos.items() if p.id_producto in stock]
    )
    recs = generar(demanda, inventario, res.reparto_presentaciones)
    ajustes = res.extra.get("ajustes") if isinstance(res.extra, dict) else None
    con_quiebre = {} if ajustes is None or len(ajustes) == 0 else ajustes.groupby("base")["unidades"].sum().to_dict()
    n = 0
    for r in recs.itertuples(index=False):
        p = productos.get(r.producto)
        if p is None:
            continue
        db.session.add(RecomendacionAbastecimiento(
            id_producto=p.id_producto, id_sede=sede.id_sede, stock_actual=None if pd.isna(r.stock_actual) else int(r.stock_actual),
            demanda_prevista=float(r.demanda_prevista),
            cantidad_sugerida=int(r.cantidad_sugerida), nivel_urgencia=r.nivel_urgencia, clase_abc=r.clase_abc,
            motivo=(r.motivo + (f" Se sumaron {int(con_quiebre[r.producto])} unidades de demanda no atendida (quiebres registrados)."
                                if r.producto in con_quiebre else ""))[:400],
            stock_objetivo=int(r.stock_objetivo), stock_seguridad=int(r.stock_seguridad), punto_pedido=int(r.punto_pedido),
            confiabilidad=r.confiabilidad, reparto=r.reparto or None, con_inventario=bool(r.con_inventario),
        ))
        n += 1
    return n


def _guardar_plan_campana(res, productos) -> int:
    """Reemplaza el plan de campaña completo. Si hay stock registrado, calcula cuánto falta cubrir de lo que resta de campaña."""
    db.session.query(PlanCampana).delete()
    plan = res.plan_campana
    if plan is None or plan.empty:
        return 0
    stock = {s.id_producto: s.cantidad_actual for s in StockPorSede.query.all()}
    suma_stock = {}
    for id_producto, cantidad in stock.items():
        suma_stock[id_producto] = suma_stock.get(id_producto, 0) + cantidad
    n = 0
    for r in plan.itertuples(index=False):
        p = productos.get(r.producto)
        if p is None:
            continue
        en_stock = suma_stock.get(p.id_producto)
        por_cubrir = None if en_stock is None else max(0, int(math.ceil(float(r.esperado_resto) - en_stock)))
        db.session.add(PlanCampana(
            id_producto=p.id_producto, campana_id=r.campana_id, campana_nombre=r.campana_nombre, tipo=r.tipo,
            meses_conocidos=int(r.meses_conocidos), vendido=round(float(r.vendido), 2),
            referencia_anio_anterior=round(float(r.referencia_anio_anterior), 2), esperado_resto=round(float(r.esperado_resto), 2),
            esperado_total=round(float(r.esperado_total), 2), ritmo_global=round(float(r.ritmo_global), 3), ritmo_pct=None if pd.isna(r.ritmo_pct) else float(r.ritmo_pct),
            clase_abc=r.clase_abc, confianza=r.confianza, error_tipico_pct=None if pd.isna(r.error_tipico_pct) else float(r.error_tipico_pct),
            stock_actual=en_stock, por_cubrir=por_cubrir,
        ))
        n += 1
    return n


def ultima_ejecucion() -> EjecucionAnalitica | None:
    return EjecucionAnalitica.query.order_by(EjecucionAnalitica.fecha_ejecucion.desc()).first() if inspect(db.engine).has_table("ejecuciones_analitica") else None
