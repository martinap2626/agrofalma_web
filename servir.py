"""
Arranca el sistema con waitress, un servidor WSGI apto para producción (funciona en Windows y Linux).

Uso:   python servir.py            (escucha en 127.0.0.1:8000)
Variables opcionales: HOST, PORT, HILOS.  Detrás de nginx se deja HOST=127.0.0.1; para usarlo directo en la red local, HOST=0.0.0.0.
"""
import os

from waitress import serve

from wsgi import app

if __name__ == "__main__":
    host, puerto, hilos = os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8000")), int(os.environ.get("HILOS", "8"))
    print(f"Agrofalma escuchando en http://{host}:{puerto} ({hilos} hilos)")
    serve(app, host=host, port=puerto, threads=hilos)
