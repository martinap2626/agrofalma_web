# Auditoría de accesibilidad

Revisa todas las pantallas del sistema (31: acceso, 24 del Administrador y 6 del Trabajador) con dos herramientas:

* **axe-core**: reglas WCAG 2.1 nivel A y AA más buenas prácticas.
* **Lighthouse**: puntaje de accesibilidad de 0 a 100.

Complementa las pruebas automáticas de `tests/test_accesibilidad.py` (contraste de colores, campos con etiqueta, regiones y
ausencia de emojis), que corren con cada `pytest` sin necesitar navegador.

## Cómo repetirla

Requiere Node.js y Google Chrome. Se usa una base de datos de prueba (por ejemplo una copia de la real), nunca la de operación.

```
cd pruebas_accesibilidad
npm install
# con el sistema corriendo (python servir.py) y un usuario Administrador y uno Trabajador de prueba:
set ADMIN_USUARIO=...      & set ADMIN_CLAVE=...
set TRABAJADOR_USUARIO=... & set TRABAJADOR_CLAVE=...
set ID_VENTA=1 & set ID_PRODUCTO=1
node auditar.mjs http://127.0.0.1:8000 resultados/accesibilidad.json
```

(En PowerShell: `$env:ADMIN_USUARIO="..."`.) El resultado queda en el `.json` y la consola muestra un resumen; el comando termina con
código distinto de cero si encuentra algún hallazgo de axe.

El auditor salta la política de seguridad de contenido (CSP) **solo en su propia pestaña**, porque necesita inyectar axe-core;
el sistema real mantiene la política que prohíbe scripts en línea.

## Último resultado (13/10/2026)

31 pantallas: **Lighthouse 100 en todas** (antes: mínimo 85, promedio 95,1) y **0 hallazgos de axe** (antes: contraste en 26 pantallas,
encabezados de tabla vacíos en 7, campos sin etiqueta, sin región principal en el acceso).

Lo que ninguna herramienta automática puede decir: si el orden de lectura con un lector de pantalla es cómodo o si todo se puede
usar solo con el teclado. Para eso sirven la prueba de tareas con usuarios y el TAM.
