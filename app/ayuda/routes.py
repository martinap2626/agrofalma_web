"""
Centro de ayuda dentro del sistema (accesible para ambos roles): guía paso a
paso de las tareas de cada rol, preguntas frecuentes con los mensajes que el
sistema puede mostrar y un glosario de los términos de la analítica.

Es contenido estático a propósito (una plantilla): no consulta la base de
datos, así que sigue disponible aunque la base de datos esté caída, justo
cuando más puede necesitarla alguien que ve un mensaje de error.
"""

from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("ayuda", __name__, url_prefix="/ayuda")


# muestra la guía de ayuda; la plantilla enseña las secciones que le corresponden al rol de quien la abre
@bp.route("")
@login_required
def inicio():
    return render_template("ayuda/inicio.html")
