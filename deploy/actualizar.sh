#!/usr/bin/env bash
# Actualiza el sistema en el servidor Linux: respaldo -> código -> dependencias -> base de datos -> verificación -> reinicio.
# Si la verificación falla, NO reinicia el servicio. Ejecutar desde agrofalma_web/.
# Nota: `flask preparar-bd` cambia la estructura de la base y necesita un usuario con permisos de administración
# (ver deploy/GUIA_DESPLIEGUE.md, paso 5): exporta DB_USER y DB_PASSWORD de administración solo para ese comando.
set -euo pipefail
source .venv/bin/activate
flask respaldar-bd
git pull --ff-only
pip install -r requirements-prod.txt
# revisión de vulnerabilidades conocidas en las dependencias (informativa: si encuentra algo, lo muestra pero no detiene la actualización)
pip install --quiet pip-audit && pip-audit -r requirements.txt || echo "AVISO: pip-audit encontró dependencias con vulnerabilidades; revisar la lista de arriba."
flask preparar-bd
flask verificar-despliegue
sudo systemctl restart agrofalma
echo "Actualización terminada."
