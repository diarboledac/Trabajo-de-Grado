FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencias del sistema mínimas
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python en fase de construcción
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del proyecto
COPY . /app

# Preparar entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Crear usuario no root
RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app /entrypoint.sh

# Forzar instalaciones posteriores (si las hubiera) al directorio del usuario
ENV PIP_USER=1

USER app

ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
