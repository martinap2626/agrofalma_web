"""
Genera un catálogo de productos BORRADOR a partir del historial real de
ventas (2025-2026), para que el gerente lo revise y corrija en vez de
construirlo desde cero -- ver memoria del proyecto, "preguntas pendientes
para el gerente" (pregunta 3, catálogo maestro).

No escribe en MySQL: genera un Excel para revisión humana en
analitica_job/datos_procesados/catalogo_productos_borrador.xlsx.

Decisiones tomadas (confirmadas por el usuario, 2026-09-22):
1. Categorías: se reemplazan las categorías de prueba de la base de datos
   (Foliares/Agroquímicos/Bioestimulantes/Otros) por las 3 categorías
   reales del dataset histórico: Foliares, Químicos, Fertilizantes.
2. stock_minimo: se inicializa con el promedio mensual real de unidades
   vendidas en los últimos 12 meses móviles (misma ventana que la
   segmentación ABC), redondeado hacia arriba. Es un punto de partida
   razonable, no una política de negocio definitiva -- el gerente debería
   ajustarlo según su criterio comercial.
3. Limpieza de precio_unitario: se excluyen precios <=0 (errores de
   captura, promociones o ajustes de crédito, no precios de venta reales);
   el precio de referencia es el más frecuente (moda) entre los precios
   válidos, con empate resuelto a favor del más reciente. Si un producto no
   tiene ningún precio válido (>0), se usa el máximo observado como
   respaldo y se marca para revisión.
4. Qué productos incluir/excluir (descontinuados, faltantes) queda
   pendiente de confirmar con el gerente -- por ahora se incluyen todos los
   productos que aparecen en el historial real, todos como "activo".

Fusiones y notas manuales (revisadas caso por caso, no automatizadas para
evitar mezclar productos que en realidad son distintos -- ver
FUSIONES_CONFIRMADAS y NOTAS_REVISION más abajo). Patrón detectado en varios
casos: el mismo tamaño escrito de dos formas ("250 ML" vs "X 250"), rastro
de que distintas personas cargaron el Excel con convenciones distintas.

Fusionados (precio idéntico o casi idéntico entre ambas variantes):
- NITRO L / NITRO X L (Foliares): S/20 en el 100% de ambas.
- ITACUR X 500 / ITACUR X 500 GRS (Químicos): S/23 en el 100% de ambas.
- YASAR 250 ML / YASAR X 250 (Químicos): S/38 en el 100% de ambas.
- AGRO GIB 250 ML / AGRO GIB X 250 (Foliares): S/25 en ambas.
- ALGAMAX AGRO 500 ML / ALGAMAX AGRO X 500 (Foliares): S/25 en ambas.
- PENTA 500 ML / PENTA X 500 (Foliares): S/43 en ambas.
- ARGON 250 ML / ARGON X 250 (Químicos): S/25 vs S/24, diferencia mínima.
- CAMPO CARB 500 / CAMPO CARB 500 ML (Químicos): S/20 vs S/23, mismo texto
  salvo la unidad omitida.
- Ronda 3 (nombre "pelado", sin talla, comparado contra el resto de su
  familia): FF DIATOMIX, CABZN X GALON, MAX CAMOTE, MAGNESIO X GALON,
  PH X GALON, DHAN, ITACUR, INDOXACROP, AMAZOTE, COYOTE, ABIGEO, PROMAX,
  POTASIO L, PRODUCCION X GALON, PK X GALON, POTASIO X GALON, LANNAFARM,
  LAVA, PESOVER, YASAR -- las 20 fusiones con precio EXACTAMENTE igual
  (0% de diferencia) entre el nombre pelado y su variante con talla.
  Nota: "X GALON" y "4L" resultaron ser la misma presentación en varios
  casos (CABZN, MAGNESIO, PH, PRODUCCION, PK, POTASIO), con precio
  idéntico entre ambas formas de nombrarla.

NO fusionados, marcados con nota para el gerente (precio no coincide lo
suficiente, o hay indicios de dato sucio):
- FF NITRO / FF NITRO 50 KG / NITRO (Fertilizantes): precios distintos
  entre sí, sin evidencia suficiente.
- FF 20 20 20 50 KG / FF 20 20 20 KG / FF 20 320 20 KG (Fertilizantes):
  120-130 vs exactamente 5 en las otras dos -- posible mezcla de "precio
  por kg" con "precio por saco", o un típeo ("20 320 20" no es una fórmula
  NPK estándar). Requiere aclaración del gerente.
- FF 20.20.20 / FF20.20.20 X KGS (Fertilizantes): mismo patrón, 127 vs 5.
- PENETRUM 250 ML / PENETRUM X 250 (Foliares): S/25 vs S/10, diferencia
  real, no solo ruido.
- PH 500 ML / PH X 500 (Foliares): la mayoría de transacciones de "PH 500
  ML" están registradas en S/0 (probablemente muestras/regalos, no ventas
  reales) -- dato sucio a confirmar.
- CITOKING 250 ML / CITOKING X 250 (Foliares): S/30 vs S/25 (~20% de
  diferencia) -- caso límite, se prefirió no fusionar sin confirmación.
- CITOKING 500 ML / CITOKING X 500 (Foliares): S/53 vs S/55 -- mismo
  motivo, caso límite.

Uso:
    python -m analitica_job.generar_catalogo_borrador
"""

from __future__ import annotations

import math

import pandas as pd

from analitica_job.construir_features import indice_mes
from analitica_job.preparar_datos import (
    CARPETA_SALIDA,
    calcular_unidades_vendidas,
    cargar_ventas,
    filtrar_categoria_excluida,
    filtrar_meses_no_confiables,
)
from analitica_job.segmentar_abc import MESES_VENTANA, filtrar_ventana_movil

CATEGORIA_DISPLAY = {
    "FOLIARES": "Foliares",
    "QUIMICOS": "Químicos",
    "FERTILIZANTES": "Fertilizantes",
}
CATEGORIA_PREFIJO = {"Foliares": "FOL", "Químicos": "QUI", "Fertilizantes": "FER"}

# claves con evidencia fuerte de ser el mismo producto (precio identico en el
# 100% de sus transacciones) -- se remapean a una sola clave antes de agrupar,
# asi que todas las funciones de calculo (nombre, precio, stock_minimo) ya
# tratan ambas variantes como una sola fila.
FUSIONES_CONFIRMADAS = {
    "NITROXSEPL": "NITROL",  # "NITRO X L" -> "NITRO L" (mismo precio S/20 en 100% de ambas)
    "ITACURXSEP500GRS": "ITACURXSEP500",  # S/23 en 100% de ambas
    "YASARXSEP250": "YASAR250ML",  # S/38 en 100% de ambas
    "AGROGIBXSEP250": "AGROGIB250ML",  # S/25 en ambas
    "ALGAMAXAGROXSEP500": "ALGAMAXAGRO500ML",  # S/25 en ambas
    "PENTAXSEP500": "PENTA500ML",  # S/43 en ambas
    "ARGONXSEP250": "ARGON250ML",  # S/25 vs S/24, diferencia minima
    "CAMPOCARB500ML": "CAMPOCARB500",  # S/20 vs S/23, unidad omitida en un caso
    # ronda 3: nombres "pelados" (sin talla) con precio identico a una variante con talla
    "FFDIATOMIX": "FFDIATOMIX25KG",  # S/60 en ambas
    "CABZNXSEPGALON": "CABZN4L",  # S/70 en ambas ("X GALON" y "4L" son la misma presentacion)
    "MAXCAMOTE": "MAXCAMOTE30ML",  # S/30 en ambas
    "MAGNESIOXSEPGALON": "MAGNESIO4L",  # S/70 en ambas
    "PHXSEPGALON": "PH4L",  # S/70 en ambas
    "DHAN": "DHAN250ML",  # S/27 en ambas
    "ITACUR": "ITACURXSEP500",  # S/23 en ambas (misma clave final que ITACURXSEP500GRS de arriba)
    "INDOXACROP": "INDOXACROP250ML",  # S/65 en ambas (distinto de INDOXACROP 125/150 ML, que sí valen S/40)
    "AMAZOTE": "AMAZOTEXSEP100GRS",  # S/15 en ambas
    "COYOTE": "COYOTE250ML",  # S/15 en ambas
    "ABIGEO": "ABIGEOXSEP100",  # S/50 en ambas
    "PROMAX": "PROMAXXSEP125ML",  # S/40 en ambas
    "POTASIOL": "POTASIOL43",  # S/20 en ambas
    "PRODUCCIONXSEPGALON": "PRODUCCION4L",  # S/70 en ambas
    "PKXSEPGALON": "PK4L",  # S/70 en ambas
    "POTASIOXSEPGALON": "POTASIO4L",  # S/70 en ambas
    "LANNAFARM": "LANNAFARMXSEP100GRS",  # S/10 en ambas
    "LAVA": "LAVA250ML",  # S/20 en ambas
    "PESOVER": "PESOVERXSEP100GRS",  # S/10 en ambas
    "YASAR": "YASAR250ML",  # S/38 en ambas (misma clave final que YASARXSEP250 de arriba)
    # ronda 4: pares "cercanos pero no exactos" (1-7% de diferencia), aprobados
    # por el usuario tras revisar la tabla de evidencia
    "UREA50KG": "UREA",  # S/94 vs S/95, 1.1%
    "SILEXXSEP100GRS": "SILEX",  # S/33 vs S/32, 3.0%
    # FF NITRO / FF NITRO 50 KG / NITRO: reconsiderado (ronda 1 lo dejo como
    # nota por prudencia, pero el % de diferencia real es menor que varios
    # casos que si se fusionaron) -- las 3 variantes al mismo destino
    "FFNITRO": "NITRO",  # S/85 vs S/83, 2.4%
    "FFNITRO50KG": "NITRO",  # S/88 vs S/83, 5.7% (via NITRO)
    "SEVEROXSEP100GRS": "SEVERO",  # S/26 vs S/25, 3.8%
    "SULFATOPOTASIOXSEP50": "SULFATOPOTASIO",  # S/170 vs S/163, 4.1%
    "UREAGRAN50KG": "UREAGRAN",  # S/100 vs S/105, 4.8%
    "FFPHOS50KG": "FFPHOS",  # S/95 vs S/90, 5.3%
    "FITOALEXINXSEPGALON": "FITOALEXIN4L",  # S/90 vs S/85, 5.6%
    "MAXFRUTO30ML": "MAXFRUTO",  # S/30 vs S/28, 6.7%
    # ronda 5: busqueda exhaustiva de TODOS los pares dentro de cada familia
    # (no solo pelado-vs-talla) -- estos dos con precio EXACTAMENTE igual
    "FF2032020KG": "FF202020KG",  # S/5 en ambas ("20 320 20" probable tipeo de "20 20 20")
    "MULTIXSEPL": "MULTIL",  # S/20 en ambas (mismo patron que NITRO L / NITRO X L)
    "MULTIPROPOSITOXSEPKGS": "MULTIPROPOSITOXSEPK",  # S/12 en el 100% de ambas ("K" vs "KGS")
    # ronda 6: normalizador de unidades ampliado ("K" suelto, "KGS") -- mas casos
    # con precio 100% identico
    "FURADAN": "FURADANXSEPK",  # S/20 en las 3 variantes (bare / KG / X K)
    "FURADANKG": "FURADANXSEPK",
    "SULFATODECOBRE": "SULFATODECOBREKG",  # S/10 en ambas
    "MATARANCHAKG": "MATARANCHAKGS",  # S/60 en ambas
    # ronda 7: normalizador sin puntos -- "FF 20.20.20" y "FF 20 20 20" resultaron
    # ser la misma familia; consolida las 2 ramas ambiguas del cluster FF 20-20-20
    # en solo 2 productos (antes 4), aunque la duda entre las 2 ramas persiste
    # (S/120 vs S/5, ver NOTAS_REVISION)
    "FF202020": "FF20202050KG",  # S/120 en ambas
    "FF202020XSEPKGS": "FF202020KG",  # S/5 en ambas
}

# grupos con nombre parecido pero SIN evidencia suficiente para fusionar (el
# precio varia entre ellos) -- se marcan con una nota en vez de fusionarse.
NOTAS_REVISION = {
    "FF20202050KG": "Verificar con el gerente si es el mismo producto que 'FF 20 20 20 KG' -- "
                     "este producto ya combina 3 nombres con precio identico entre si (S/120): "
                     "'FF 20 20 20 50 KG', 'FF 20.20.20'. Precio muy distinto al de 'FF 20 20 20 "
                     "KG' (120 vs 5), posible mezcla de precio por kg con precio por saco.",
    "FF202020KG": "Verificar con el gerente si es el mismo producto que 'FF 20 20 20 50 KG' -- "
                  "este producto ya combina 3 nombres con precio identico entre si (S/5): "
                  "'FF 20 20 20 KG', 'FF 20 320 20 KG' (probable tipeo de '20 20 20') y "
                  "'FF20.20.20 X KGS'. Precio muy distinto al de 'FF 20 20 20 50 KG' (5 vs 120), "
                  "posible mezcla de precio por kg con precio por saco.",
    "PENETRUM250ML": "Verificar con el gerente si es el mismo producto que 'PENETRUM X 250' -- "
                     "precio distinto (S/25 vs S/10), diferencia real, no parece solo ruido de datos.",
    "PENETRUMXSEP250": "Verificar con el gerente si es el mismo producto que 'PENETRUM 250 ML' -- "
                       "precio distinto (S/10 vs S/25), diferencia real, no parece solo ruido de datos.",
    "PH500ML": "Verificar con el gerente si es el mismo producto que 'PH X 500' -- la mayoria de "
               "transacciones de 'PH 500 ML' estan registradas en S/0 (probablemente "
               "muestras/regalos, no ventas reales), dato sucio a confirmar.",
    "PHXSEP500": "Verificar con el gerente si es el mismo producto que 'PH 500 ML' -- ese otro "
                 "nombre tiene la mayoria de transacciones en S/0 (dato sucio a confirmar).",
    "CITOKING250ML": "Verificar con el gerente si es el mismo producto que 'CITOKING X 250' -- "
                     "precio cercano pero no igual (S/30 vs S/25, ~20% de diferencia), caso limite.",
    "CITOKINGXSEP250": "Verificar con el gerente si es el mismo producto que 'CITOKING 250 ML' -- "
                       "precio cercano pero no igual (S/25 vs S/30, ~20% de diferencia), caso limite.",
    "CITOKING500ML": "Verificar con el gerente si es el mismo producto que 'CITOKING X 500' -- "
                     "precio cercano (S/53 vs S/55), caso limite.",
    "CITOKINGXSEP500": "Verificar con el gerente si es el mismo producto que 'CITOKING 500 ML' -- "
                       "precio cercano (S/55 vs S/53), caso limite.",
    # ronda 5: mismo precio pero TALLA EXPLICITAMENTE DISTINTA (numeros distintos, no
    # solo notacion distinta) -- mas arriesgado fusionar que los casos de arriba, se
    # deja solo como nota para que el gerente lo confirme.
    "INDOXACROP125ML": "Verificar con el gerente si es el mismo producto que 'INDOXACROP 150 ML' "
                       "-- mismo precio (S/40) pero tallas distintas (125 ML vs 150 ML), podria "
                       "ser un error de tipeo en la talla de alguna de las dos.",
    "INDOXACROP150ML": "Verificar con el gerente si es el mismo producto que 'INDOXACROP 125 ML' "
                       "-- mismo precio (S/40) pero tallas distintas (150 ML vs 125 ML), podria "
                       "ser un error de tipeo en la talla de alguna de las dos.",
    "EXTINTOR200ML": "Verificar con el gerente si es el mismo producto que 'EXTINTOR 250 ML' -- "
                     "precio muy cercano (S/24 vs S/25) pero tallas distintas (200 ML vs 250 ML).",
    "EXTINTOR250ML": "Verificar con el gerente si es el mismo producto que 'EXTINTOR 200 ML' -- "
                     "precio muy cercano (S/25 vs S/24) pero tallas distintas (250 ML vs 200 ML).",
    "FINE200ML": "Verificar con el gerente si es el mismo producto que 'FINE 250 ML' -- precio "
                 "cercano (S/33 vs S/35) pero tallas distintas (200 ML vs 250 ML).",
    "FINE250ML": "Verificar con el gerente si es el mismo producto que 'FINE 200 ML' -- precio "
                 "cercano (S/35 vs S/33) pero tallas distintas (250 ML vs 200 ML).",
    # precio a revisar (revisar_precio=True): se investigaron las transacciones crudas
    # de estos 2 productos y en ambos casos TODAS las transacciones tienen cantidad=1 y
    # precio=S/0, sin excepcion -- no parece un error de captura sino una salida de
    # inventario sin venta (muestra, regalo, o consumo propio), no una venta real.
    "FERTISUELO4L": "Precio S/0 en TODAS sus transacciones (6, cantidad=1 cada una, todas "
                    "registradas por el vendedor ALCANTARA) -- no parece una venta real, podria "
                    "ser consumo propio o muestra sin cobrar. Como referencia, otros productos de "
                    "la misma familia sí tienen precio: 'FERTISUELO' (S/15) y 'FERTISUELO X GALON' "
                    "(S/50) -- el gerente debe confirmar si 4L corresponde a alguno de los dos o "
                    "es un precio aparte.",
    "THOJASYFORRAJES500ML": "Precio S/0 en su unica transaccion registrada (cantidad=1, "
                            "vendedor TIENDA) -- no parece una venta real, podria ser consumo "
                            "propio o muestra sin cobrar. No hay otro producto de la misma familia "
                            "en el catalogo para usar como referencia de precio.",
}

RUTA_SALIDA = CARPETA_SALIDA / "catalogo_productos_borrador.xlsx"


def moda_con_desempate_reciente(serie: pd.Series, orden: pd.Series) -> object:
    """Valor mas frecuente de `serie`; si hay empate, gana el que aparece
    mas tarde segun `orden` (ej. mes_idx)."""
    df = pd.DataFrame({"valor": serie, "orden": orden})
    conteo = df.groupby("valor").agg(frecuencia=("valor", "size"), ultimo=("orden", "max"))
    conteo = conteo.sort_values(["frecuencia", "ultimo"], ascending=[False, False])
    return conteo.index[0]


def calcular_nombre_y_categoria(df: pd.DataFrame) -> pd.DataFrame:
    """Por clave_dedup: nombre y categoria canonicos (el valor mas frecuente
    entre las variantes que trae el historial), para resolver las ~3% de
    filas con nombre/categoria inconsistente entre transacciones."""
    filas = []
    for clave, grupo in df.groupby("clave_dedup"):
        nombre = moda_con_desempate_reciente(grupo["producto_normalizado"], grupo["mes_idx"])
        categoria_cruda = moda_con_desempate_reciente(grupo["categoria"], grupo["mes_idx"])
        filas.append({
            "clave_dedup": clave,
            "nombre_producto": nombre,
            "categoria": CATEGORIA_DISPLAY.get(categoria_cruda, categoria_cruda),
        })
    return pd.DataFrame(filas)


def calcular_precio_referencia(df: pd.DataFrame) -> pd.DataFrame:
    """Precio mas frecuente entre los precios validos (>0) de cada producto;
    si ninguno es valido, se usa el maximo observado (aunque sea 0) y se
    marca para revision manual."""
    filas = []
    for clave, grupo in df.groupby("clave_dedup"):
        validos = grupo[grupo["precio_unitario"] > 0]
        if len(validos) > 0:
            precio = moda_con_desempate_reciente(validos["precio_unitario"], validos["mes_idx"])
            revisar = False
        else:
            precio = grupo["precio_unitario"].max()
            revisar = True
        filas.append({"clave_dedup": clave, "precio_unitario": round(float(precio), 2), "revisar_precio": revisar})
    return pd.DataFrame(filas)


def calcular_stock_minimo(df: pd.DataFrame) -> pd.DataFrame:
    """Promedio mensual de unidades vendidas en los ultimos 12 meses
    moviles (misma ventana que la segmentacion ABC), redondeado hacia
    arriba. Punto de partida, no politica de negocio definitiva."""
    ventana, _, _ = filtrar_ventana_movil(df)
    ventana = ventana.copy()
    ventana["unidades_vendidas"] = calcular_unidades_vendidas(ventana)

    total_por_producto = ventana.groupby("clave_dedup")["unidades_vendidas"].sum()
    promedio_mensual = total_por_producto / MESES_VENTANA

    return (
        promedio_mensual.apply(lambda v: max(0, math.ceil(v)))
        .rename("stock_minimo")
        .reset_index()
    )


def asignar_codigos(catalogo: pd.DataFrame) -> pd.Series:
    codigos = []
    contador = {}
    for categoria in catalogo["categoria"]:
        prefijo = CATEGORIA_PREFIJO.get(categoria, "OTR")
        contador[prefijo] = contador.get(prefijo, 0) + 1
        codigos.append(f"{prefijo}-{contador[prefijo]:03d}")
    return pd.Series(codigos, index=catalogo.index)


def main() -> None:
    df = cargar_ventas()
    df = filtrar_categoria_excluida(df)
    df = filtrar_meses_no_confiables(df)
    df["mes_idx"] = indice_mes(df["anio"], df["mes_numero"])
    df["clave_dedup"] = df["clave_dedup"].replace(FUSIONES_CONFIRMADAS)

    nombre_categoria = calcular_nombre_y_categoria(df)
    precio = calcular_precio_referencia(df)
    stock_min = calcular_stock_minimo(df)

    catalogo = nombre_categoria.merge(precio, on="clave_dedup").merge(stock_min, on="clave_dedup", how="left")
    catalogo["stock_minimo"] = catalogo["stock_minimo"].fillna(0).astype(int)
    catalogo["unidad"] = "Unidad"
    catalogo["estado"] = "Activo"
    catalogo["nota"] = catalogo["clave_dedup"].map(NOTAS_REVISION).fillna("")
    catalogo = catalogo.sort_values(["categoria", "nombre_producto"]).reset_index(drop=True)
    catalogo.insert(0, "codigo_producto", asignar_codigos(catalogo))

    catalogo = catalogo[[
        "codigo_producto", "nombre_producto", "categoria", "unidad",
        "precio_unitario", "revisar_precio", "stock_minimo", "estado", "nota", "clave_dedup",
    ]]

    catalogo.to_excel(RUTA_SALIDA, index=False)

    print(f"Catálogo borrador: {len(catalogo)} productos")
    print(f"  Por categoría: {catalogo['categoria'].value_counts().to_dict()}")
    print(f"  Con precio a revisar (sin ningún precio válido registrado): {catalogo['revisar_precio'].sum()}")
    print(f"  Con nota de revisión: {(catalogo['nota'] != '').sum()}")
    print(f"Guardado en {RUTA_SALIDA}")


if __name__ == "__main__":
    main()
