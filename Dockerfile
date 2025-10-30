FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copiar el código del proyecto (excluido lo que está en .dockerignore)
COPY . /app

# Copiar entrypoint y darle permisos
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# (Opcional) herramientas de build si necesitas compilar paquetes
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
