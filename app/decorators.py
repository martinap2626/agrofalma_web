"""
Middleware de verificación de rol (RNF-03: la plataforma deberá
restringir el acceso según el rol del usuario).

Se implementa como un decorador de ruta en vez de un middleware global
porque la mayoría de rutas del sistema son accesibles para ambos roles
(ej. consultar el catálogo) y solo un subconjunto es exclusivo de
administrador (ej. registrar un producto). Un decorador explícito por
ruta deja ese criterio visible en el propio código de la vista.
"""

from functools import wraps

from flask import abort
from flask_login import current_user


# fabrica el decorador: le pasas los roles permitidos y te devuelve el decorador ya armado
def rol_requerido(*roles_permitidos):
    """
    Restringe una vista a los roles indicados.

    Uso:
        @rol_requerido("Administrador")
        def solo_admin(): ...

    Si el usuario autenticado no tiene un rol permitido, la petición
    se corta con 403 (Prohibido) antes de ejecutar la vista — nunca
    se decide la restricción dentro de la plantilla.
    """

    # este es el decorador en si, se le pega encima a la funcion de la vista
    def decorador(vista):
        # lo que realmente corre cada vez que alguien pide la ruta protegida
        @wraps(vista)
        def envoltura(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.rol.nombre_rol not in roles_permitidos:
                abort(403)
            return vista(*args, **kwargs)

        return envoltura

    return decorador
