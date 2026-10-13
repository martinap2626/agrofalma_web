"""Paginación hecha por el servidor: cada página pide a la base de datos solo sus filas, no toda la tabla."""

from __future__ import annotations


def paginar(consulta, pagina_raw, por_pagina: int):
    """
    Devuelve (filas, total, total_paginas, pagina) de una consulta ya filtrada y ordenada.
    Una página fuera de rango (por ejemplo ?pagina=999 o ?pagina=abc) se corrige a la última o a la primera.
    """
    try:
        pagina = max(int(pagina_raw), 1)
    except (TypeError, ValueError):
        pagina = 1
    total = consulta.count()
    total_paginas = max((total + por_pagina - 1) // por_pagina, 1)
    pagina = min(pagina, total_paginas)
    filas = consulta.offset((pagina - 1) * por_pagina).limit(por_pagina).all()
    return filas, total, total_paginas, pagina
