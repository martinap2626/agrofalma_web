"""
Levanta la aplicación con waitress (servidor WSGI de producción, no el de
desarrollo de Flask) apuntando a la base de datos de carga (agrofalma_carga).

Uso:  python pruebas_carga/servidor_carga.py [puerto] [hilos]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from waitress import serve  # noqa: E402

from app import create_app  # noqa: E402
from sembrar_volumen import ConfigCarga  # noqa: E402


class ConfigServidorCarga(ConfigCarga):
    LOG_ARCHIVO = None  # no ensuciar logs/ con la prueba de carga


if __name__ == "__main__":
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 5055
    hilos = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    serve(create_app(ConfigServidorCarga), host="127.0.0.1", port=puerto, threads=hilos)
