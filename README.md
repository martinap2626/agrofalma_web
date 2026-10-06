<p align="center">
  <img src="app/static/img/logo_agrofalma.jpg" alt="Agrofalma y Consultores EIRL" width="320">
</p>

# Sistema inteligente de apoyo a decisiones comerciales — Agrofalma y Consultores EIRL

Plataforma web para registrar ventas, créditos y productos, y para apoyar las decisiones comerciales del
gerente con analítica: **predicción de demanda**, **segmentación ABC de productos** y **recomendaciones de
abastecimiento**. Tesis de Ingeniería de Sistemas y Computación (USAT, Seminario de Tesis I).

- **Metodologías:** Scrum (plataforma web) + CRISP-DM (componente analítico).
- **Tecnologías:** Python 3, Flask, Jinja2, SQLAlchemy, MySQL 8; pandas y scikit-learn para el modelo.
- **Roles:** Administrador (todo, incluida la analítica y la auditoría) y Trabajador (sus ventas, clientes, créditos y catálogo).

## Estructura del proyecto

```
agrofalma_web/
├── app/                      Aplicación web (Flask, un módulo por área)
│   ├── auth/ usuarios/       Inicio de sesión y gestión de usuarios (RF-01, RF-02)
│   ├── productos/ ventas/    Catálogo, ventas, clientes, créditos y cobros (RF-03 a RF-07)
│   ├── dashboard/ reportes/  Dashboard comercial y reportes con exportación CSV (RF-08, RF-12)
│   ├── analitica/            Predicción, segmentación ABC y recomendaciones (RF-09 a RF-11)
│   ├── auditoria/ ayuda/     Bitácora de acciones y centro de ayuda
│   ├── models/               Tablas (SQLAlchemy)
│   ├── seguridad.py          Bitácora, bloqueo de acceso, contraseñas, cabeceras, validaciones
│   ├── servicios_analitica.py  Puente entre el pipeline y MySQL
│   └── comandos_*.py         Comandos de consola (respaldo, analítica)
├── analitica_job/            Pipeline de analítica OFFLINE (no corre dentro de la web)
│   ├── pipeline/             limpieza, catálogo, ABC, modelos, recomendaciones, ejecutar
│   ├── referencia/           Mapa revisado de nombres de producto -> producto base
│   └── pimentel/             Lector de los Excel de la Sede Pimentel
├── tests/                    Pruebas automatizadas (seguridad, funcionales, respaldo, analítica)
├── pruebas_carga/            Prueba de carga con 25 000 ventas simuladas
├── scripts/                  Utilidades (respaldo programado en Windows)
├── config.py  run.py         Configuración (variables de entorno) y arranque
└── requirements*.txt         Dependencias
```

## Puesta en marcha

```bash
python -m venv .venv && .venv\Scripts\activate          # Windows
pip install -r requirements-dev.txt
copy .env.example .env                                  # completar SECRET_KEY y datos de MySQL
flask --app run crear-tablas-seguridad                  # bitácora de auditoría
flask --app run crear-indices                           # índices por fecha
flask --app run run                                     # http://127.0.0.1:5000
```

`SECRET_KEY` es obligatoria: sin ella la aplicación no arranca. Para producción usar HTTPS y
`SESSION_COOKIE_SECURE=true` (ver `.env.example`).

## Analítica comercial (orden de uso)

```bash
flask --app run preparar-analitica                      # columnas y tabla de ejecuciones
flask --app run cargar-catalogo-base                    # simulación; con --aplicar --reemplazar-prueba lo carga
flask --app run plantilla-inventario                    # Excel en blanco para el stock actual
flask --app run cargar-inventario ARCHIVO.xlsx --aplicar
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

## Respaldo y recuperación

```bash
flask --app run respaldar-bd                  # respaldo con retención de 7
flask --app run verificar-respaldo            # prueba de recuperación: respalda, restaura en una base temporal y compara
flask --app run restaurar-bd ARCHIVO --destino NOMBRE_BD
```

## Seguridad

Control de acceso por rol y por propiedad del registro, protección CSRF, bloqueo temporal tras intentos
fallidos, sesión que caduca por inactividad, contraseñas con política mínima, bitácora de auditoría de solo
lectura (menú Auditoría), cabeceras HTTP de seguridad y validación de entradas.
