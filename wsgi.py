"""Punto de entrada para servidores de producción (gunicorn, waitress). No activa el modo depuración."""
from app import create_app

app = create_app()
