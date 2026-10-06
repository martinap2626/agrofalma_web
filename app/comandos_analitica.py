"""
Comandos de la analítica comercial (se ejecutan desde la consola, no desde la web):

    flask preparar-analitica          prepara las tablas (columnas nuevas, registro de ejecuciones)
    flask cargar-catalogo-base        carga los productos BASE sacados de los Excel de Pimentel
    flask plantilla-inventario        genera el Excel para que el gerente anote el stock actual
    flask cargar-inventario ARCHIVO   carga ese Excel en stock_por_sede
    flask cargar-inventario-provisional  estima un stock PROVISIONAL (marcado) mientras no haya conteo real
    flask actualizar-analitica        corre el pipeline y guarda predicción, ABC y recomendaciones

Orden de uso la primera vez: preparar-analitica -> cargar-catalogo-base ->
(plantilla-inventario -> cargar-inventario) -> actualizar-analitica.
"""

from __future__ import annotations

from pathlib import Path

import click

CARPETA_PLANTILLAS = Path(__file__).resolve().parent.parent / "analitica_job" / "datos_procesados"


def registrar_comandos_analitica(app) -> None:
    from app.servicios_analitica import ErrorAnalitica

    # corre una función de servicio y convierte los errores esperables en un mensaje claro
    def _proteger(funcion):
        try:
            return funcion()
        except ErrorAnalitica as error:
            raise click.ClickException(str(error))

    @app.cli.command("preparar-analitica")
    def preparar_analitica():
        """Agrega columnas y tablas que necesita el pipeline real (es seguro repetirlo)."""
        from app.servicios_analitica import asegurar_esquema

        cambios = asegurar_esquema()
        click.echo("Cambios: " + ", ".join(cambios) if cambios else "Todo estaba listo, sin cambios.")

    @app.cli.command("cargar-catalogo-base")
    @click.option("--aplicar", is_flag=True, help="Sin esta opción solo muestra qué haría (simulación).")
    @click.option("--reemplazar-prueba", is_flag=True,
                  help="Borra productos, ventas y resultados de PRUEBA para dejar solo el catálogo real. Hace un respaldo antes.")
    def cargar_catalogo_base(aplicar, reemplazar_prueba):
        """Carga el catálogo de productos base construido con los Excel de Pimentel."""
        from analitica_job.pipeline.catalogo import construir_catalogo
        from analitica_job.pipeline.limpieza import cargar_ventas_limpias
        from app.comandos_respaldo import CARPETA_RESPALDOS, ejecutar_mysqldump, generar_nombre_archivo
        from app.servicios_analitica import cargar_catalogo
        from config import Config

        click.echo("Construyendo el catálogo desde los Excel de Pimentel ...")
        df, _ = cargar_ventas_limpias(anio_desde=2023)
        hojas = construir_catalogo(df)

        def respaldo():
            CARPETA_RESPALDOS.mkdir(exist_ok=True)
            ruta = CARPETA_RESPALDOS / ("antes_catalogo_" + generar_nombre_archivo())
            ejecutar_mysqldump(Config, ruta)
            return ruta

        r = _proteger(lambda: cargar_catalogo(hojas, aplicar=aplicar, reemplazar_prueba=reemplazar_prueba, respaldo_previo=respaldo))
        click.echo(f"Catálogo: {r['productos_catalogo']} productos base ({r['ya_existentes']} ya existían, {r['nuevos']} nuevos).")
        click.echo(f"En la base hoy: {r['datos_actuales']}")
        if r["productos_ajenos_en_bd"]:
            click.echo(f"Hay {r['productos_ajenos_en_bd']} productos que no son del catálogo (¿datos de prueba?).")
        if not aplicar:
            click.echo("SIMULACIÓN: no se cambió nada. Repite con --aplicar (y --reemplazar-prueba si corresponde).")
            return
        if "respaldo" in r:
            click.echo(f"Respaldo previo: {r['respaldo']}")
        click.echo(f"Listo: {r['creados']} creados, {r['actualizados']} actualizados.")

    @app.cli.command("plantilla-inventario")
    @click.option("--salida", type=click.Path(path_type=Path), default=CARPETA_PLANTILLAS / "plantilla_inventario_pimentel.xlsx")
    def plantilla_inventario(salida):
        """Genera el Excel en blanco para anotar el stock actual por producto y presentación."""
        from analitica_job.pipeline.catalogo import construir_catalogo
        from analitica_job.pipeline.limpieza import cargar_ventas_limpias
        from app.servicios_analitica import generar_plantilla_inventario

        df, _ = cargar_ventas_limpias(anio_desde=2023)
        ruta = generar_plantilla_inventario(construir_catalogo(df), salida)
        click.echo(f"Plantilla generada: {ruta}")

    @app.cli.command("cargar-inventario")
    @click.argument("archivo", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option("--aplicar", is_flag=True, help="Sin esta opción solo valida el archivo.")
    def cargar_inventario_cmd(archivo, aplicar):
        """Carga el stock actual desde el Excel llenado por el gerente."""
        from app.servicios_analitica import cargar_inventario

        r = _proteger(lambda: cargar_inventario(archivo, aplicar=aplicar))
        click.echo(f"Sede: {r['sede']} · productos con stock: {r['productos_con_stock']}")
        if r["desconocidos"]:
            click.echo(f"Productos del archivo que no están en el catálogo (se ignoran): {', '.join(r['desconocidos'][:10])}")
        click.echo("Stock cargado." if r["aplicado"] else "SIMULACIÓN: archivo válido. Repite con --aplicar para cargarlo.")

    @app.cli.command("cargar-inventario-provisional")
    @click.option("--aplicar", is_flag=True, help="Sin esta opción solo muestra qué haría.")
    @click.option("--meses-cobertura", default=1.5, show_default=True, help="Meses de venta promedio que se asumen en stock.")
    @click.option("--minimo", default=5, show_default=True, help="Stock mínimo asignado a cada producto para poder venderlo.")
    def cargar_inventario_provisional(aplicar, meses_cobertura, minimo):
        """Estima un stock PROVISIONAL (marcado como tal) a partir de las ventas, mientras no haya conteo real."""
        from analitica_job.pipeline.catalogo import construir_catalogo
        from analitica_job.pipeline.limpieza import cargar_ventas_limpias
        from app.servicios_analitica import generar_inventario_provisional

        df, _ = cargar_ventas_limpias(anio_desde=2023)
        r = _proteger(lambda: generar_inventario_provisional(construir_catalogo(df), aplicar, meses_cobertura, minimo))
        click.echo(f"Sede {r['sede']}: {r['productos']} productos, {r['unidades_totales']} unidades en total "
                   f"({r['meses_cobertura']} meses de venta promedio, mínimo {r['minimo']}).")
        if r["conservados_reales"]:
            click.echo(f"Se conservaron {r['conservados_reales']} productos que ya tenían stock REAL.")
        click.echo("Stock provisional cargado. Ahora corre: flask actualizar-analitica" if r["aplicado"]
                   else "SIMULACIÓN: no se cambió nada. Repite con --aplicar.")

    @app.cli.command("actualizar-analitica")
    @click.option("--anio-desde", default=2023, show_default=True, help="Primer año de datos con el que se entrena.")
    @click.option("--sin-guardar", is_flag=True, help="Solo calcula y exporta el Excel, sin escribir en la base de datos.")
    def actualizar_analitica(anio_desde, sin_guardar):
        """Corre el pipeline de analítica (Pimentel) y guarda predicción, segmentación ABC y recomendaciones."""
        from analitica_job.pipeline.ejecutar import ejecutar, exportar_excel
        from app.servicios_analitica import ajustes_por_eventos, guardar_resultado, ventas_sistema_mensual

        ajustes = None if sin_guardar else _proteger(ajustes_por_eventos)
        if ajustes is not None and len(ajustes):
            click.echo(f"Eventos del negocio: se suman {int(ajustes['unidades'].sum())} unidades de demanda no atendida ({len(ajustes)} quiebres).")
        ventas_sistema = None if sin_guardar else _proteger(ventas_sistema_mensual)
        res = ejecutar(anio_desde=anio_desde, verbose=True, ajustes=ajustes, ventas_sistema=ventas_sistema)
        if res.informe_calidad.get("meses_desde_sistema"):
            click.echo(f"Ventas de la plataforma: se incorporaron {res.informe_calidad['meses_desde_sistema']} mes(es) posteriores a los Excel.")
        elif ventas_sistema is not None and len(ventas_sistema):
            click.echo("AVISO: hay ventas registradas en la plataforma, pero no se usaron: los meses deben ser consecutivos desde el mes siguiente "
                       "al último de los Excel. Registra o importa los meses intermedios que falten.")
        ruta = exportar_excel(res)
        click.echo(f"Excel de resultados: {ruta}")
        if sin_guardar:
            click.echo("No se escribió en la base de datos (--sin-guardar).")
            return
        r = _proteger(lambda: guardar_resultado(res))
        click.echo(f"Guardado: {r['predicciones']} predicciones, {r['segmentaciones']} clasificaciones ABC, "
                   f"{r['recomendaciones']} recomendaciones, {r['plan_campana']} filas del plan de campaña.")
        if r["productos_sin_catalogo"]:
            click.echo(f"AVISO: {len(r['productos_sin_catalogo'])} productos del Excel no están en el catálogo de la base "
                       f"(¿falta correr cargar-catalogo-base?): {', '.join(r['productos_sin_catalogo'][:8])}")
        if not r["recomendaciones"]:
            click.echo("No hay recomendaciones: falta cargar el inventario (flask plantilla-inventario / cargar-inventario).")
