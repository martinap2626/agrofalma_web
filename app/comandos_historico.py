"""
Importa el histórico de ventas de la sede Pimentel (Excel 2023-2026, ya limpio por el pipeline de
analítica) a las tablas `ventas` y `detalle_ventas`, para que Ventas consolidadas, el dashboard y los
reportes tengan datos reales.

    flask importar-ventas-historicas            simula e informa (no escribe nada)
    flask importar-ventas-historicas --aplicar  escribe en la base de datos
    flask importar-ventas-historicas --aplicar --reemplazar   borra antes lo ya importado

Qué es y qué NO es lo importado: los Excel traen ventas MENSUALES por producto y vendedor, sin
cliente, sin fecha exacta ni tipo de pago. Por eso se genera UNA venta consolidada por vendedor y mes
(número de operación H<AAAAMM><J|A>), fechada el último día del mes, con el cliente genérico
«Cliente varios (histórico)», pago al contado y observación que lo identifica. No se toca el stock.
La analítica no se afecta: el pipeline sigue leyendo los Excel.
"""

from __future__ import annotations

import secrets
import calendar
from datetime import datetime

import click
from app.seguridad import auditado

PREFIJO = "H"
CLIENTE_GENERICO = "Cliente varios (histórico)"
VENDEDORES = {"J": ("Jorge", "(histórico)", "hist.jorge"), "A": ("Alcántara", "(histórico)", "hist.alcantara")}


def _vendedor(hoja: str) -> str:
    h = hoja.upper()
    return "J" if "JORGE" in h else "A"  # ALCANTARA o solo «ING» (Ing. Alcántara)


def construir_ventas(df):
    """Una venta por (vendedor, año, mes); líneas agrupadas por producto base. Devuelve (ventas, resumen)."""
    d = df.copy()
    d["v"] = d["hoja"].map(_vendedor)
    antes = len(d)
    d = d[d["soles"] > 0]
    descartadas = antes - len(d)
    g = d.groupby(["v", "anio", "mes", "base"], as_index=False).agg(unidades=("unidades", "sum"), soles=("soles", "sum"))
    ventas = {}
    for r in g.itertuples(index=False):
        ventas.setdefault((r.v, int(r.anio), int(r.mes)), []).append(r)
    return ventas, {"lineas_descartadas_soles_no_positivos": descartadas}


def registrar_comando_historico(app) -> None:
    @app.cli.command("importar-ventas-historicas")
    @click.option("--aplicar", is_flag=True, help="Escribe en la base de datos (sin esto solo simula).")
    @click.option("--reemplazar", is_flag=True, help="Borra antes las ventas históricas ya importadas.")
    @auditado("consola_importar_historico", "flask importar-ventas-historicas", solo_si="aplicar")
    def importar(aplicar, reemplazar):
        from werkzeug.security import generate_password_hash

        from analitica_job.pipeline import limpieza
        from app.extensions import db
        from app.models import Cliente, DetalleVenta, Producto, Rol, Sede, Usuario, Venta

        df, _ = limpieza.cargar_ventas_limpias(anio_desde=2023)
        ventas, resumen = construir_ventas(df)
        productos = {p.nombre_producto: p for p in Producto.query.all()}
        faltan = sorted({l.base for ls in ventas.values() for l in ls if l.base not in productos})
        if faltan:
            raise click.ClickException(f"Productos sin catálogo ({len(faltan)}): {', '.join(faltan[:10])}. Ejecuta cargar-catalogo-base.")
        sede = Sede.query.filter_by(nombre_sede="Sede Pimentel").first()
        if sede is None:
            raise click.ClickException("No existe la sede Pimentel.")
        previas = Venta.query.filter(Venta.numero_operacion.like(f"{PREFIJO}%")).count()
        total_lineas = sum(len(v) for v in ventas.values())
        total_soles = sum(float(l.soles) for ls in ventas.values() for l in ls)
        click.echo(f"A importar: {len(ventas)} ventas mensuales, {total_lineas} líneas, S/ {total_soles:,.2f} "
                   f"({resumen['lineas_descartadas_soles_no_positivos']} líneas con soles no positivos descartadas). Ya importadas: {previas}.")
        if not aplicar:
            click.echo("Simulación: no se escribió nada. Usa --aplicar.")
            return
        if previas and not reemplazar:
            raise click.ClickException("Ya hay ventas históricas importadas. Usa --reemplazar para rehacerlas.")
        if previas:
            ids = [v.id_venta for v in Venta.query.filter(Venta.numero_operacion.like(f"{PREFIJO}%")).all()]
            DetalleVenta.query.filter(DetalleVenta.id_venta.in_(ids)).delete(synchronize_session=False)
            Venta.query.filter(Venta.id_venta.in_(ids)).delete(synchronize_session=False)

        cliente = Cliente.query.filter_by(nombre_cliente=CLIENTE_GENERICO).first()
        if cliente is None:
            cliente = Cliente(nombre_cliente=CLIENTE_GENERICO, estado=False)
            db.session.add(cliente)
        rol = Rol.query.filter_by(nombre_rol="Trabajador").first()
        usuarios = {}
        for clave, (nombre, apellido, login) in VENDEDORES.items():
            u = Usuario.query.filter_by(usuario_login=login).first()
            if u is None:  # cuenta desactivada y con clave aleatoria: solo sirve para atribuir las ventas
                u = Usuario(nombre=nombre, apellido=apellido, usuario_login=login, id_rol=rol.id_rol, id_sede=sede.id_sede,
                            estado=False, contrasena_hash=generate_password_hash(secrets.token_urlsafe(24)))
                db.session.add(u)
            usuarios[clave] = u
        db.session.flush()

        n_lineas = 0
        for (v, anio, mes), lineas in sorted(ventas.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0])):
            fecha = datetime(anio, mes, calendar.monthrange(anio, mes)[1], 18, 0)
            total = round(sum(float(l.soles) for l in lineas), 2)
            venta = Venta(
                numero_operacion=f"{PREFIJO}{anio}{mes:02d}{v}", fecha_hora=fecha, id_cliente=cliente.id_cliente,
                id_usuario=usuarios[v].id_usuario, id_sede=sede.id_sede, tipo_venta="contado", estado_pago="pagado",
                total=total, observaciones=f"Histórico mensual importado de Excel ({anio}-{mes:02d}, {VENDEDORES[v][0]})"[:150],
            )
            db.session.add(venta)
            db.session.flush()
            suma = 0.0
            for l in lineas:
                cantidad = max(1, int(round(float(l.unidades))))
                subtotal = round(float(l.soles), 2)
                db.session.add(DetalleVenta(id_venta=venta.id_venta, id_producto=productos[l.base].id_producto,
                                            cantidad=cantidad, precio_unitario=round(subtotal / cantidad, 2), subtotal=subtotal))
                n_lineas += 1
            db.session.flush()
        db.session.commit()
        click.echo(f"Listo: {len(ventas)} ventas y {n_lineas} líneas importadas.")


EVENTOS_EJEMPLO = [
    # tipo, fecha inicio, fecha fin, posición del producto en el ranking de demanda (None = todo el negocio), descripción, impacto, cantidad no atendida
    ("quiebre_stock", (2026, 3, 12), (2026, 3, 19), 0, "Se agotó una semana en plena campaña grande", "alto", 40),
    ("clima_plaga", (2026, 4, 20), None, None, "Plaga de gusano cogollero: subió la compra de químicos", "alto", None),
    ("promocion", (2026, 7, 5), (2026, 7, 31), 2, "Descuento por volumen en foliares", "medio", None),
    ("produccion", (2026, 8, 10), None, 1, "Retraso del lote propio por falta de un insumo", "medio", None),
    ("quiebre_stock", (2026, 8, 24), None, 3, "Sin stock por 5 días; clientes compraron en otra tienda", "alto", 25),
    ("precio", (2026, 6, 1), None, 4, "Subió el precio del insumo y se ajustó el precio de venta", "bajo", None),
]


def registrar_comandos_eventos_ejemplo(app) -> None:
    @app.cli.command("cargar-eventos-ejemplo")
    @auditado("consola_eventos_ejemplo", "flask cargar-eventos-ejemplo")
    def cargar_eventos_ejemplo():
        """Carga eventos de EJEMPLO (marcados como tales) para ver la pantalla con datos; no afectan los cálculos."""
        from datetime import date

        from app.extensions import db
        from app.models import EventoNegocio, RecomendacionAbastecimiento, Sede

        EventoNegocio.query.filter_by(es_ejemplo=True).delete()
        ranking = (RecomendacionAbastecimiento.query.order_by(RecomendacionAbastecimiento.demanda_prevista.desc()).limit(8).all())
        sede = Sede.query.filter_by(nombre_sede="Sede Pimentel").first()
        n = 0
        for tipo, ini, fin, pos, descripcion, impacto, cantidad in EVENTOS_EJEMPLO:
            id_producto = ranking[pos].id_producto if pos is not None and pos < len(ranking) else None
            db.session.add(EventoNegocio(
                tipo=tipo, fecha_inicio=date(*ini), fecha_fin=date(*fin) if fin else None, id_producto=id_producto,
                id_sede=sede.id_sede if (sede and id_producto) else None, descripcion=descripcion, impacto=impacto,
                cantidad_no_atendida=cantidad if id_producto else None, es_ejemplo=True,
            ))
            n += 1
        db.session.commit()
        click.echo(f"{n} eventos de ejemplo cargados (marcados como ejemplo; no afectan los cálculos).")

    @app.cli.command("quitar-eventos-ejemplo")
    @auditado("consola_eventos_ejemplo", "flask quitar-eventos-ejemplo")
    def quitar_eventos_ejemplo():
        """Borra todos los eventos marcados como ejemplo."""
        from app.extensions import db
        from app.models import EventoNegocio

        n = EventoNegocio.query.filter_by(es_ejemplo=True).delete()
        db.session.commit()
        click.echo(f"{n} eventos de ejemplo eliminados.")
