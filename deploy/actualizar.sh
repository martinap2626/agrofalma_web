#!/usr/bin/env bash
# Actualiza el sistema en el servidor Linux: respaldo -> código -> dependencias -> base de datos -> verificación -> reinicio.
# Si la verificación falla, NO reinicia el servicio. Ejecutar desde agrofalma_web/.
set -euo pipefail
source .venv/bin/activate
flask respaldar-bd
git pull --ff-only
pip install -r requirements-prod.txt
flask preparar-bd
flask verificar-despliegue
sudo systemctl restart agrofalma
echo "Actualización terminada."
