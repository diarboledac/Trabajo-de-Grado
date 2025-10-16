# ThingsBoard Telemetry Simulator

Este repositorio crea (o reutiliza) dispositivos de prueba en ThingsBoard y publica telemetría concurrente para evaluar la plataforma. El flujo recomendado ejecuta **un único proceso/contendor** que envía los datos de todos los dispositivos al mismo tiempo.

## Requisitos
- Python 3.9 o superior (en Windows se recomienda el lanzador `py`).
- Acceso al tenant de ThingsBoard (`TB_URL`) y al broker MQTT (`MQTT_HOST` / `MQTT_PORT`).
- Docker (opcional) si deseas ejecutar la simulación dentro de un contenedor único.

## Variables de entorno (`.env`)
| Variable | Descripción |
|----------|-------------|
| `TB_URL` | URL base del servidor ThingsBoard (REST). |
| `TB_USERNAME`, `TB_PASSWORD` | Credenciales del tenant. |
| `DEVICE_PREFIX`, `DEVICE_COUNT` | Prefijo y cantidad de dispositivos a crear (por defecto 20). |
| `DEVICE_LABEL`, `DEVICE_TYPE` | Metadatos asignados a cada dispositivo. |
| `MQTT_HOST`, `MQTT_PORT`, `MQTT_TLS` | Destino del broker MQTT. |
| `PUBLISH_INTERVAL_SEC` | Intervalo entre publicaciones por hilo. |

Para ajustar la IP/host del servidor, modifica directamente `.env` y, si cambias la cantidad de dispositivos, vuelve a ejecutar `scripts/create_devices.py` para regenerar tokens.

## Flujo típico
1. **Instala dependencias:**
   ```bash
   py -3 -m pip install -r requirements.txt
   ```
2. **Provisiona los dispositivos en ThingsBoard** (usa el `DEVICE_COUNT` deseado):
   ```bash
   py -3 scripts/create_devices.py
   ```
   Al finalizar, los tokens quedan en `data/tokens.json` y el reporte en `data/devices.csv`.
3. **Lanza la simulación sincronizada:**
   ```bash
   py -3 scripts/send_telemetry.py
   ```
   El script crea un hilo por dispositivo, sincroniza la primera publicación con una barrera y continúa enviando telemetría cada `PUBLISH_INTERVAL_SEC`. Detén el envío con `Ctrl+C` o cerrando la consola.

## Ejecutar en Docker (un solo contenedor)
1. Construye la imagen desde la raíz:
   ```bash
   docker build -t tb-sim .
   ```
2. Ejecuta el contenedor montando `.env` y la carpeta `data` para reutilizar los tokens:
   ```bash
   docker run --rm \
     -v %CD%\data:/app/data \
     -v %CD%\.env:/app/.env:ro \
     tb-sim
   ```
   (En macOS/Linux, reemplaza `%CD%` por `$(pwd)`).

## Scripts útiles
- `scripts/create_devices.py`: crea o actualiza los dispositivos y tokens.
- `scripts/send_telemetry.py`: ejecuta la simulación concurrente recomendada.
- `scripts/create_and_stream.py`: variante que crea cada dispositivo y lanza un hilo MQTT al vuelo.
- `scripts/delete_devices.py`: elimina los dispositivos listados en `data/devices.csv`.

## Limpieza y utilidades
- Para borrar los dispositivos de ThingsBoard: `py -3 scripts/delete_devices.py`.
- Para reiniciar la simulación con otro número de dispositivos, cambia `DEVICE_COUNT` en `.env`, ejecuta `create_devices.py` y luego `send_telemetry.py`.
- Ajusta los rangos de telemetría modificando `payload()` en `scripts/send_telemetry.py`.

## Verificar conectividad
Para validar que la API de ThingsBoard responde puedes ejecutar `scripts/check_connectivity.py` (ver sección siguiente) o realizar el login/consulta mínima con un script propio antes de lanzar la simulación.
