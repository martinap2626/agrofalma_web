"""
Íconos de la interfaz como SVG en línea (trazo simple, color heredado del texto).

Reemplazan a los emojis que había antes (lápiz, papelera, lupa...): los emojis se ven distinto en cada sistema, los
lectores de pantalla los leen mal ("cubo de basura con tapa") y no siguen el estilo serio del sistema. Cada ícono es
decorativo (aria-hidden): el botón o enlace que lo contiene lleva su propio nombre con aria-label.

Uso en las plantillas:  {{ icono('editar') }}   o   {{ icono('papelera', 20) }}
Trazados basados en el conjunto Feather (licencia MIT).
"""

from markupsafe import Markup, escape

_TRAZOS = {
    "editar": '<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',
    "papelera": (
        '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
        '<path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>'
    ),
    "reactivar": '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
    "ver": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "ubicacion": '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
    "planta": (
        '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10z"/>'
        '<path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/>'
    ),
    "anular": '<circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>',
}


# explicación corta de un término, junto al encabezado de una columna o a una cifra: se ve al pasar el mouse o al enfocarla con el
# teclado (tabulador), y los lectores de pantalla leen el texto completo; así no hace falta leer párrafos largos arriba de la tabla
def info(texto: str) -> Markup:
    seguro = escape(texto)
    return Markup(f'<span class="info-termino" tabindex="0" role="note" aria-label="{seguro}" data-ayuda="{seguro}">i</span>')


# devuelve el SVG del ícono pedido; si el nombre no existe falla de inmediato (así un error de tipeo no pasa desapercibido)
def icono(nombre: str, tam: int = 18) -> Markup:
    trazo = _TRAZOS[nombre]
    return Markup(
        f'<svg class="icono icono-{nombre}" width="{tam}" height="{tam}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true" focusable="false">{trazo}</svg>'
    )
