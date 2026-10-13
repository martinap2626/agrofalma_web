"""
Pruebas de accesibilidad que se pueden comprobar sin navegador (WCAG 2.1 nivel AA): contraste de colores, campos con
nombre, tablas con encabezados, regiones de la página y ausencia de emojis. Complementan (no reemplazan) la revisión
con axe-core y Lighthouse sobre las pantallas reales.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.iconos import icono
from conftest import crear_producto

RAIZ = Path(__file__).resolve().parent.parent
PLANTILLAS = RAIZ / "app" / "templates"
CSS = (RAIZ / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Contraste de colores (mínimo 4,5:1 para texto normal)
# ---------------------------------------------------------------------------
def luminancia(hexa):
    hexa = hexa.lstrip("#")
    if len(hexa) == 3:
        hexa = "".join(c * 2 for c in hexa)
    canales = []
    for i in (0, 2, 4):
        v = int(hexa[i:i + 2], 16) / 255
        canales.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * canales[0] + 0.7152 * canales[1] + 0.0722 * canales[2]


def contraste(a, b):
    la, lb = sorted((luminancia(a), luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def variables_css():
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{3,6})\s*;", CSS))


# propiedades de una regla CSS (con var(--x) ya resuelto a su color)
def regla(selector):
    cuerpo = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert cuerpo, f"No se encontró la regla {selector}"
    variables = variables_css()
    propiedades = {}
    for nombre, valor in re.findall(r"([\w-]+)\s*:\s*([^;]+);", cuerpo.group(1)):
        valor = valor.strip()
        enlace = re.fullmatch(r"var\((--[\w-]+)\)", valor)
        propiedades[nombre] = variables[enlace.group(1)] if enlace else valor
    return propiedades


PARES = [
    # (selector, propiedad del fondo, color de texto si no está en la regla)
    (".etiqueta-pendiente", "background", None),
    (".etiqueta-alerta", "background", None),
    (".etiqueta-activo", "background", None),
    (".etiqueta-inactivo", "background", None),
    (".etiqueta-parcial", "background", None),
    (".etiqueta-credito", "background", None),
    (".boton-salir", "background", None),
    (".alerta-success", "background", None),
    (".alerta-danger", "background", None),
    (".alerta-warning", "background", None),
]


@pytest.mark.parametrize("selector,propiedad,_", PARES)
def test_el_texto_de_cada_etiqueta_y_aviso_tiene_contraste_suficiente(selector, propiedad, _):
    props = regla(selector)
    assert contraste(props["color"], props[propiedad]) >= 4.5, (
        f"{selector}: {props['color']} sobre {props[propiedad]} = {contraste(props['color'], props[propiedad]):.2f}:1"
    )


@pytest.mark.parametrize("nombre,fondo", [
    ("--gris-texto", "#ffffff"), ("--gris-texto", "#f4f6f4"), ("--rojo-texto", "#ffffff"), ("--rojo-boton", "#ffffff"),
    ("--texto-secundario", "#ffffff"), ("--texto-secundario", "#f4f6f4"), ("--verde-primario", "#ffffff"),
])
def test_los_colores_de_texto_sobre_fondo_claro_tienen_contraste_suficiente(nombre, fondo):
    color = variables_css()[nombre]
    assert contraste(color, fondo) >= 4.5, f"{nombre} {color} sobre {fondo} = {contraste(color, fondo):.2f}:1"


def test_el_boton_peligro_y_el_enlace_de_cuenta_tienen_contraste_suficiente():
    peligro = regla(".boton-peligro")
    assert contraste(peligro["color"], peligro["background"]) >= 4.5
    cuenta = regla(".enlace-cuenta")
    assert contraste(cuenta["color"], "#ffffff") >= 4.5  # sobre la barra superior blanca


# ---------------------------------------------------------------------------
# Plantillas: sin emojis, sin encabezados vacíos, íconos válidos
# ---------------------------------------------------------------------------
EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U00002B50\U0000FE0F]")


def archivos_de_interfaz():
    return [p for p in list(PLANTILLAS.rglob("*.html")) + list((RAIZ / "app" / "static" / "js").glob("*.js"))]


def test_la_interfaz_no_usa_emojis():
    con_emoji = [f"{p.relative_to(RAIZ)}: {m.group()!r}" for p in archivos_de_interfaz() for m in EMOJI.finditer(p.read_text(encoding="utf-8"))]
    assert not con_emoji, con_emoji


def test_ninguna_tabla_tiene_encabezados_vacios():
    vacios = [p.relative_to(RAIZ).as_posix() for p in PLANTILLAS.rglob("*.html") if re.search(r"<th[^>]*>\s*</th>", p.read_text(encoding="utf-8"))]
    assert not vacios, vacios


def test_los_iconos_declarados_existen_y_son_decorativos():
    usados = {m for p in PLANTILLAS.rglob("*.html") for m in re.findall(r"icono\('(\w+)'", p.read_text(encoding="utf-8"))}
    assert usados, "no se encontró ningún ícono en las plantillas"
    for nombre in usados:
        svg = str(icono(nombre))
        assert svg.startswith("<svg") and 'aria-hidden="true"' in svg
    with pytest.raises(KeyError):
        icono("no-existe")


def test_cada_boton_de_icono_tiene_nombre_accesible():
    sin_nombre = []
    for p in PLANTILLAS.rglob("*.html"):
        for etiqueta in re.findall(r"<(?:a|button)\b[^>]*class=\"icono-accion\"[^>]*>", p.read_text(encoding="utf-8")):
            if "aria-label=" not in etiqueta:
                sin_nombre.append(f"{p.name}: {etiqueta[:80]}")
    assert not sin_nombre, sin_nombre


# ---------------------------------------------------------------------------
# Páginas ya dibujadas: campos con etiqueta, una sola región principal
# ---------------------------------------------------------------------------
class Revisor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controles = []        # (etiqueta HTML, id, aria-label, dentro de <label>)
        self.etiquetas_for = set()
        self.en_label = False
        self.mains = 0

    def handle_starttag(self, tag, atributos):
        a = dict(atributos)
        if tag == "main":
            self.mains += 1
        if tag == "label":
            self.en_label = True
            if a.get("for"):
                self.etiquetas_for.add(a["for"])
        if tag in ("input", "select", "textarea"):
            tipo = (a.get("type") or "text").lower()
            if tipo in ("hidden", "submit", "button", "checkbox", "radio") and tag == "input":
                return
            self.controles.append((tag, a.get("id"), a.get("aria-label") or a.get("aria-labelledby"), self.en_label))

    def handle_endtag(self, tag):
        if tag == "label":
            self.en_label = False


def revisar(html):
    r = Revisor()
    r.feed(html)
    return r


@pytest.mark.parametrize("ruta,cliente_fixture", [
    ("/ventas/nueva", "trabajador_a"), ("/ventas/clientes/nuevo", "trabajador_a"), ("/ventas/clientes", "trabajador_a"),
    ("/productos/nuevo", "admin"), ("/usuarios/nuevo", "admin"), ("/eventos/nuevo", "admin"), ("/auditoria", "admin"),
    ("/reportes", "admin"), ("/cuenta/contrasena", "trabajador_a"), ("/inventario", "admin"), ("/analitica/simulador", "admin"),
])
def test_todo_campo_de_formulario_tiene_etiqueta_y_la_pagina_una_region_principal(request, ruta, cliente_fixture):
    c = request.getfixturevalue(cliente_fixture)
    r = c.get(ruta)
    assert r.status_code == 200, ruta
    revision = revisar(r.get_data(as_text=True))
    assert revision.mains == 1, f"{ruta}: debe haber exactamente una región <main> (hay {revision.mains})"
    sin_etiqueta = [
        (tag, id_) for tag, id_, aria, en_label in revision.controles
        if not aria and not en_label and id_ not in revision.etiquetas_for
    ]
    assert not sin_etiqueta, f"{ruta}: campos sin etiqueta {sin_etiqueta}"


def test_el_detalle_de_inventario_y_el_login_tambien_cumplen(app, admin, cliente):
    id_producto = crear_producto(app, "ACC-1")
    for c, ruta in ((admin, f"/inventario/{id_producto}"), (cliente, "/login")):
        revision = revisar(c.get(ruta).get_data(as_text=True))
        assert revision.mains == 1, ruta
        assert not [x for x in revision.controles if not x[2] and not x[3] and x[1] not in revision.etiquetas_for], ruta


def test_las_lineas_de_la_venta_tienen_nombre_accesible(trabajador_a):
    html = trabajador_a.get("/ventas/nueva").get_data(as_text=True)
    for nombre in ('aria-label="Producto"', 'aria-label="Cantidad"', 'aria-label="Precio unitario en soles"'):
        assert html.count(nombre) >= 2, nombre  # la fila inicial y la plantilla de líneas nuevas
