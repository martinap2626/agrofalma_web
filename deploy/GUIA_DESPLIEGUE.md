# Guía breve de despliegue

El sistema está listo para instalarse; falta decidir dónde corre. `flask verificar-despliegue` indica qué falta antes de abrirlo a los usuarios.

## Cómo elegir

| | A. VPS con HTTPS | B. Equipo de la oficina |
|---|---|---|
| Costo | Servidor propio o alquilado (VPS): costo mensual bajo | Sin costo adicional; usa un equipo existente |
| Acceso | Desde cualquier lugar, con HTTPS | Solo dentro de la oficina |
| Seguridad | Alta si se sigue la guía (HTTPS, firewall, usuario de base propio) | Depende de la red local y del equipo |
| Mantenimiento | Requiere administrar un servidor Linux | Más simple, pero depende de que el equipo esté encendido |
| Recomendado para | Operación real a mediano plazo | Validación con usuarios y pruebas (TAM) |

## Opción A: servidor Linux con nginx y HTTPS

1. Preparar el servidor (Ubuntu o similar) con Python 3.12+, MySQL 8 y nginx; abrir solo los puertos 80 y 443 (MySQL no se expone).
2. Crear en MySQL la base agrofalma_bd y un usuario propio con permisos solo sobre ella (no root).
3. Copiar el proyecto a /opt/agrofalma/agrofalma_web, crear el entorno virtual e instalar: pip install -r requirements-prod.txt.
4. Copiar deploy/.env.produccion.ejemplo como .env y completar SECRET_KEY (64 caracteres aleatorios), datos de la base, SESSION_COOKIE_SECURE=true y CONFIAR_EN_PROXY=true.
5. Restaurar los datos (flask restaurar-bd con el último respaldo) o cargar desde cero: flask preparar-bd, flask cargar-catalogo-base, flask importar-ventas-historicas --aplicar, flask actualizar-analitica.
6. Instalar el servicio (deploy/agrofalma.service) y el sitio de nginx (deploy/nginx.conf.ejemplo); obtener el certificado HTTPS con certbot.
7. Ejecutar flask verificar-despliegue: no debe haber FALLOS y cada AVISO debe estar entendido.
8. Programar los respaldos y la actualización mensual de la analítica (deploy/cron_respaldo.txt).
9. Activar los usuarios reales con su propia contraseña y entregar el manual de usuario.

## Opción B: equipo Windows en la oficina

1. Instalar Python y MySQL en el equipo de la oficina; crear la base y un usuario propio como en la opción A.
2. Instalar el sistema (pip install -r requirements-prod.txt) y completar el .env (sin HTTPS: SESSION_COOKIE_SECURE=false y CONFIAR_EN_PROXY=false).
3. Arrancar con python servir.py (HOST=0.0.0.0 para que otros equipos de la red local entren por la IP del equipo, por ejemplo http://192.168.1.10:8000).
4. Dejarlo como tarea de inicio de Windows y activar el respaldo con scripts/programar_respaldo_windows.ps1.
5. Limitación: sin HTTPS ni acceso desde fuera de la oficina; sirve para operar en la red local y para la validación con usuarios.

## Actualizar y recuperar

- Linux: ejecutar deploy/actualizar.sh. Hace respaldo, trae el código, instala dependencias, prepara la base, verifica y reinicia solo si no hay fallos.
- Cada mes: cargar las ventas nuevas y ejecutar flask actualizar-analitica (queda programado en cron).
- Si algo sale mal: flask restaurar-bd con el respaldo previo a la actualización.
