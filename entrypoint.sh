#!/usr/bin/env bash
set -e

# Si hay requirements.txt, instálalas (runtime)
if [ -f /app/requirements.txt ]; then
    pip install --no-cache-dir -r /app/requirements.txt
fi

# Ejecuta el comando proporcionado al contenedor (por defecto "bash")
exec "$@"
