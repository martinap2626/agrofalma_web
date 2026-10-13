# Guía breve de despliegue

El sistema está listo para instalarse; falta decidir dónde corre. `flask verificar-despliegue` indica qué falta antes de abrirlo a los usuarios.

## Cómo elegir

| | A. VPS con HTTPS | B. Equipo de la oficina |
|---|---|---|
| Costo | Servidor propio o alquilado (VPS): costo mensual bajo | Sin costo adicional; usa un equipo existente |
| Acceso | Desde cualquier lugar, con HTTPS | Solo dentro de la oficina |
| Seguridad | Alta si se sigue la guía (HTTPS, firewall, usuario de base propio) | Depende de la red local y del equipo |
| Procesos | Varios (gunicorn): aprovecha los núcleos y tolera la caída de un proceso | Uno solo con varios hilos (waitress) |
| Mantenimiento | Requiere administrar un servidor Linux | Más simple, pero depende de que el equipo esté encendido |
| Recomendado para | Operación real a mediano plazo | Validación con usuarios y pruebas (TAM) |

## Opción A: servidor Linux con nginx y HTTPS

1. Preparar el servidor (Ubuntu o similar) con Python 3.12+, MySQL 8 y nginx. Firewall: abrir solo los puertos 80 y 443 (y el 22 limitado a tu IP); MySQL no se expone: `sudo ufw default deny incoming && sudo ufw allow 80,443/tcp && sudo ufw allow from TU_IP to any port 22 && sudo ufw enable`.
2. Crear en MySQL la base agrofalma_bd y **dos usuarios**: uno de **administración** (para instalar y actualizar la estructura) y el de **la aplicación**, con permisos mínimos: `flask permisos-bd` imprime el SQL (la aplicación no puede borrar ni editar la bitácora ni el kárdex, ni crear ni borrar tablas). Nunca usar root.
3. Copiar el proyecto a /opt/agrofalma/agrofalma_web, crear el entorno virtual e instalar: `pip install -r requirements-prod.txt` (incluye gunicorn).
4. Copiar deploy/.env.produccion.ejemplo como .env y completar SECRET_KEY (64 caracteres aleatorios), datos de la base, SESSION_COOKIE_SECURE=true, CONFIAR_EN_PROXY=true, **RESPALDO_CLAVE** (y guardarla también fuera del servidor) y **RESPALDO_COPIA_DIR** (carpeta fuera del servidor).
5. Preparar la base con el usuario de **administración** (DB_USER y DB_PASSWORD temporales): `flask preparar-bd`. Restaurar los datos (flask restaurar-bd con el último respaldo) o cargarlos desde cero: flask cargar-catalogo-base, flask importar-ventas-historicas --aplicar, flask actualizar-analitica. Luego dejar en el .env el usuario de la **aplicación**.
6. Instalar el servicio (deploy/agrofalma.service, usa gunicorn con varios procesos y reinicio automático) y el sitio de nginx (deploy/nginx.conf.ejemplo, con límites de peticiones); obtener el certificado HTTPS con certbot.
7. Ejecutar `flask verificar-despliegue`: no debe haber FALLOS y cada AVISO debe estar entendido.
8. Programar los respaldos, el simulacro semanal y la actualización mensual de la analítica (deploy/cron_respaldo.txt).
9. Activar un monitoreo externo de disponibilidad sobre `https://TU_DOMINIO/salud` (por ejemplo UptimeRobot, gratis): avisa por correo si el sistema deja de responder.
10. Activar los usuarios reales con su propia contraseña (cada uno puede cambiarla desde «Cambiar contraseña») y entregar el manual de usuario.
11. Repetir la prueba de carga en el servidor (pruebas_carga/prueba_carga.py) y guardar el resultado como evidencia; y, si se desea, un escaneo OWASP ZAP en modo baseline contra una copia del sistema.

## Opción B: equipo Windows en la oficina

1. Instalar Python y MySQL en el equipo de la oficina; crear la base y un usuario propio como en la opción A (`flask permisos-bd`).
2. Instalar el sistema (pip install -r requirements-prod.txt) y completar el .env (sin HTTPS: SESSION_COOKIE_SECURE=false y CONFIAR_EN_PROXY=false).
3. Arrancar con python servir.py (HOST=0.0.0.0 para que otros equipos de la red local entren por la IP del equipo, por ejemplo http://192.168.1.10:8000).
4. Dejarlo como tarea de inicio de Windows y activar el respaldo y el simulacro semanal con scripts/programar_respaldo_windows.ps1.
5. Limitación: sin HTTPS ni acceso desde fuera de la oficina; sirve para operar en la red local y para la validación con usuarios.

## Actualizar y recuperar

- Linux: ejecutar deploy/actualizar.sh. Hace respaldo, trae el código, revisa vulnerabilidades de las dependencias, instala, prepara la base, verifica y reinicia solo si no hay fallos.
- Cada mes: cargar las ventas nuevas y ejecutar flask actualizar-analitica (queda programado en cron).
- Si algo sale mal: flask restaurar-bd con el respaldo previo a la actualización. Política, objetivos (RPO 24 h, RTO menos de 1 hora) y modelo de acta de simulacro: deploy/POLITICA_RESPALDO.md.
