<p align="center">
  <img src="app/static/img/logo_agrofalma.jpg" alt="Agrofalma y Consultores EIRL" width="320">
</p>

# Sistema inteligente de apoyo a decisiones comerciales — Agrofalma y Consultores EIRL

Plataforma web para registrar ventas, créditos y productos, y para apoyar las decisiones comerciales del
gerente con analítica: **predicción de demanda**, **segmentación ABC de productos** y **recomendaciones de
abastecimiento**. Tesis de Ingeniería de Sistemas y Computación (USAT, Seminario de Tesis I).

- **Metodologías:** Scrum (plataforma web) + CRISP-DM (componente analítico).
- **Tecnologías:** Python 3, Flask, Jinja2, SQLAlchemy, MySQL 8; pandas y scikit-learn para el modelo.
- **Roles:** Administrador (todo, incluida la analítica, el inventario y la auditoría) y Trabajador (sus ventas, clientes, créditos y catálogo).

## Estructura del proyecto

```
agrofalma_web/
├── app/                      Aplicación web (Flask, un módulo por área)
│   ├── auth/ usuarios/       Inicio de sesión, cambio de contraseña propio y gestión de usuarios (RF-01, RF-02)
│   ├── productos/ ventas/    Catálogo, ventas (con anulación), clientes, créditos y cobros (RF-03 a RF-07)
│   ├── inventario/           Ingresos, conteos físicos y kárdex por producto (solo Administrador)
│   ├── dashboard/ reportes/  Dashboard comercial y reportes con exportación CSV (RF-08, RF-12)
│   ├── analitica/            Predicción, segmentación ABC y recomendaciones (RF-09 a RF-11)
│   ├── auditoria/ ayuda/     Bitácora de acciones (con cadena de huellas) y centro de ayuda
│   ├── models/               Tablas (SQLAlchemy)
│   ├── seguridad.py          Bitácora encadenada, bloqueo de acceso, contraseñas, cabeceras, validaciones
│   ├── servicios_inventario.py  Cambios de stock atómicos con kárdex
│   ├── esquema.py migraciones.py  Cambios de esquema para bases antiguas y control de versiones (Alembic)
│   ├── servicios_analitica.py  Puente entre el pipeline y MySQL
│   └── comandos_*.py         Comandos de consola (respaldo, despliegue, analítica)
├── analitica_job/            Pipeline de analítica OFFLINE (no corre dentro de la web)
│   ├── pipeline/             limpieza, catálogo, ABC, modelos, recomendaciones, ejecutar
│   ├── referencia/           Mapa revisado de nombres de producto -> producto base
│   └── pimentel/             Lector de los Excel de la Sede Pimentel
├── migrations/               Migraciones del esquema de la base de datos (Alembic)
├── tests/                    Pruebas automatizadas (seguridad, concurrencia, accesibilidad, respaldo, migraciones...)
├── pruebas_carga/            Prueba de carga con 25 000 ventas simuladas
├── pruebas_accesibilidad/    Auditoría de accesibilidad (axe-core y Lighthouse) de todas las pantallas
├── deploy/                   Servicio, nginx, gunicorn, cron, política de respaldo y guía de despliegue
├── scripts/                  Utilidades (respaldo programado en Windows)
├── config.py  run.py         Configuración (variables de entorno) y arranque
└── requirements*.txt         Dependencias
```

## Puesta en marcha

```bash
python -m venv .venv && .venv\Scripts\activate          # Windows
pip install -r requirements-dev.txt
copy .env.example .env                                  # completar SECRET_KEY y datos de MySQL
flask --app run preparar-bd                             # crea o pone al día tablas, columnas, restricciones e índices
flask --app run run                                     # http://127.0.0.1:5000
```

`SECRET_KEY` es obligatoria: sin ella la aplicación no arranca. Para producción usar HTTPS y
`SESSION_COOKIE_SECURE=true` (ver `.env.example` y `deploy/GUIA_DESPLIEGUE.md`).

Después de actualizar el código (`git pull`) conviene ejecutar `flask --app run preparar-bd` (seguro de repetir: no toca los
datos) y `flask --app run verificar-despliegue` (dice qué falta antes de abrir el sistema a los usuarios).

## Analítica comercial (orden de uso)

```bash
flask --app run preparar-analitica                      # columnas y tabla de ejecuciones
flask --app run cargar-catalogo-base                    # simulación; con --aplicar --reemplazar-prueba lo carga
flask --app run plantilla-inventario                    # Excel en blanco para el stock actual
flask --app run cargar-inventario ARCHIVO.xlsx --aplicar   # o, mejor, el módulo Inventario de la web (ingresos y conteos)
flask --app run actualizar-analitica                    # corre el pipeline y guarda los resultados
```

El pipeline lee los Excel de ventas de la Sede Pimentel (2023-2026), los limpia, agrupa los productos por
nombre base, segmenta en ABC (últimos 12 meses, en soles), compara 6 métodos de pronóstico con validación
de origen móvil, elige el método por clase y pronostica el mes siguiente. Las recomendaciones solo se
generan si hay inventario cargado. Detalle y decisiones en la documentación de la tesis.

## Pruebas

```bash
python -m pytest tests -v          # usan bases de datos aparte (*_test); nunca tocan los datos reales
python pruebas_carga/sembrar_volumen.py && python pruebas_carga/prueba_carga.py
```

Las pruebas incluyen: concurrencia (ventas y pagos simultáneos), bitácora encadenada, permisos mínimos de MySQL, respaldo
cifrado de punta a punta, migraciones (la base creada por migraciones debe coincidir con los modelos) y accesibilidad
(contraste, etiquetas, regiones). Las advertencias de funciones obsoletas hacen fallar las pruebas (`pytest.ini`).
La auditoría de accesibilidad con navegador está en `pruebas_accesibilidad/`.

## Respaldo y recuperación

```bash
flask --app run respaldar-bd                  # respaldo comprimido y cifrado, retención de 7 y copia fuera del equipo
flask --app run verificar-respaldo            # simulacro: respalda, restaura en una base temporal y compara tabla por tabla
flask --app run restaurar-bd ARCHIVO --destino NOMBRE_BD     # acepta .sql, .sql.gz y .sql.gz.enc
flask --app run verificar-bitacora            # comprueba que nadie alteró la bitácora directamente en la base
```

Política, objetivos (RPO 24 h, RTO menos de 1 hora) y modelo de acta de simulacro: `deploy/POLITICA_RESPALDO.md`.

## Seguridad

Control de acceso por rol y por propiedad del registro, protección CSRF, bloqueo temporal tras intentos
fallidos, sesión que caduca por inactividad y se cierra en los demás equipos al cambiar la contraseña, cierre de
sesión por POST, contraseñas con política mínima, bitácora de auditoría de solo lectura con cadena de huellas que
detecta ediciones o borrados hechos directamente en MySQL, usuario de base de datos con permisos mínimos
(`flask --app run permisos-bd`), límite de descargas de reportes, control de precios (descuento máximo del Trabajador),
cabeceras HTTP de seguridad, validación de entradas, y protección contra el doble envío de formularios.

## Cambios de la base de datos

La estructura de la base se versiona con Alembic (`migrations/`). Para cambiar un modelo: editar el modelo,
`flask --app run db migrate -m "qué cambia"`, revisar el archivo generado y `flask --app run preparar-bd`. Ver `migrations/README.md`.
