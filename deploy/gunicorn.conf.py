"""
Configuración de gunicorn para el servidor Linux (detrás de nginx).  Uso:  gunicorn -c deploy/gunicorn.conf.py wsgi:app

Varios procesos (workers) en lugar de uno solo: el sistema aprovecha más de un núcleo y, si un proceso falla o se
reinicia, los demás siguen atendiendo. Cada proceso carga pandas y scikit-learn (la analítica), así que ocupa unos 150 MB:
en un servidor de 1 GB conviene 2 procesos (el valor por defecto); con más memoria se puede subir con WORKERS.

NOTA: esta configuración no se pudo probar en el equipo de desarrollo (Windows no ejecuta gunicorn). Debe probarse en el
servidor con `flask verificar-despliegue` y con la prueba de carga (pruebas_carga/prueba_carga.py) antes de abrirlo al público.
"""

import os

bind = os.environ.get("BIND", "127.0.0.1:8000")   # solo escucha dentro del servidor; nginx es quien recibe a los usuarios
workers = int(os.environ.get("WORKERS", "2"))
worker_class = "gthread"                           # cada proceso atiende varias peticiones a la vez con hilos
threads = int(os.environ.get("HILOS", "4"))
timeout = 60                                       # una petición que tarda más de 60 s se corta (el sistema responde en menos de 1 s)
graceful_timeout = 30                              # al reiniciar, deja terminar lo que está en curso
keepalive = 5
max_requests = 1000                                # cada proceso se recicla cada ~1000 peticiones: evita que la memoria crezca sin límite
max_requests_jitter = 100                          # ...y no todos a la vez
forwarded_allow_ips = "127.0.0.1"                  # solo se confía en el proxy local (nginx) para saber la IP real y si fue HTTPS
accesslog = "logs/gunicorn_acceso.log"
errorlog = "logs/gunicorn_errores.log"
loglevel = "info"
limit_request_line = 4094                          # direcciones demasiado largas se rechazan
limit_request_fields = 50
limit_request_field_size = 8190
