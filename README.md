# ThingsBoard Telemetry Load Lab

Repositorio para ejecutar pruebas de esfuerzo reproducibles sobre un tenant de ThingsBoard. Incluye herramientas para aprovisionar dispositivos simulados, publicar telemetría vía MQTT, recopilar métricas, analizar resultados y repetir corridas con un solo comando o mediante contenedores Docker.

---

## 1. Componentes principales
- **Simulador asíncrono (`scripts/mqtt/mqtt_stress_async.py`)**: abre conexiones MQTT para cada dispositivo, publica carga configurable y expone un dashboard Flask con métricas en tiempo real.
- **Orquestador (`scripts/mqtt/run_stress_suite.py`)**: automatiza el flujo completo (aprovisionar ? activar ? ejecutar ? desactivar) y acepta los mismos argumentos que el simulador.
- **Herramientas auxiliares**: creación/limpieza de dispositivos, activación/desactivación manual y detención segura de corridas en curso.
- **Infraestructura Docker**: `Dockerfile`, `entrypoint.sh` y `docker-compose.yml` permiten replicar el entorno en cualquier máquina con Docker Desktop.
- **Locust (`locustfile.py`)**: ejemplo mínimo para generar tráfico HTTP adicional si se desea comparar cargas.

---

## 2. Estructura del repositorio
```
.venv/                      # Ambiente virtual recomendado (ignorando en Git)
.vscode/                    # Tareas y launchers para VS Code
scripts/
  mqtt/
    activate_devices.py     # Activa dispositivos (por nombre/prefijo/todos)
    create_devices.py       # Aprovisiona o actualiza la flota en ThingsBoard
    deactivate_devices.py   # Desactiva dispositivos y actualiza control local
    delete_by_prefix.py     # Elimina dispositivos cuyo nombre comparta prefijo
    delete_devices.py       # Elimina los IDs listados en data/provisioning
    metrics_server.py       # Dashboard Flask consumido por el simulador
    mqtt_stress_async.py    # Simulador principal (asyncio + MQTT)
    report_last_run.py      # Resumen legible del último JSON generado
    run_stress_suite.py     # Automatiza todo el flujo de prueba
    send_telemetry.py       # Simulador heredado (threads) opcional
    stop_simulation.py      # Envía señal (SIGTERM/SIGINT) al simulador activo
    tb.py                   # Cliente REST simplificado para ThingsBoard
    toggle_devices.py       # API común para activar/desactivar desde CLI
locustfile.py               # Ejemplo de carga HTTP usando Locust
requirements.txt            # Dependencias Python de la solución
Dockerfile                  # Imagen para ejecutar run_stress_suite en contenedor
entrypoint.sh               # Instala dependencias al iniciar el contenedor
docker-compose.yml          # Shell interactiva sobre la imagen construida
.env.example                # Plantilla de variables de entorno
.env                        # Configuración real (no compartir)
data/
  provisioning/             # tokens.json + devices.csv (ignorados en Git)
  logs/                     # Logs en formato JSONL por corrida
  metrics/                  # Snapshots CSV por shard/corrida
  control/                  # Archivos de control (PID, dispositivos deshabilitados)
```
Todo lo que viva en `data/` queda fuera del repositorio (`.gitignore`) y puede montarse como volumen dentro de contenedores para conservar tokens, logs y reportes.

---

## 3. Requisitos y preparación
### 3.1 Software necesario
| Recurso | Detalle |
|---------|---------|
| Python 3.9+ | Ejecución directa desde el sistema operativo. |
| Docker Desktop (opcional) | Permite crear contenedores idénticos en otras máquinas. |
| Acceso a ThingsBoard | Endpoint REST y broker MQTT accesibles desde la máquina de pruebas. |
| Credenciales Tenant | Usuario/clave con permiso para crear y eliminar dispositivos. |

### 3.2 Variables de entorno (`.env`)
Copia `.env.example` a `.env` y completa los valores reales antes de iniciar cualquier flujo. Los campos mínimos son:

| Variable | Descripción |
|----------|-------------|
| `TB_URL`, `TB_USERNAME`, `TB_PASSWORD` | Endpoint REST y credenciales del tenant de ThingsBoard. |
| `DEVICE_PREFIX`, `DEVICE_COUNT`, `DEVICE_PROFILE_ID` | Identificadores de la flota simulada. |
| `MQTT_HOST`, `MQTT_PORT`, `MQTT_TLS`, `MQTT_TOPIC`, `MQTT_QOS` | Parámetros de conexión al broker MQTT configurado en ThingsBoard. |
| `PUBLISH_INTERVAL_SEC`, `SIM_DURATION_SEC`, `RAMP_PERCENTAGES` | Ritmo de publicación y plan de rampas. |
| `METRICS_HOST`, `METRICS_PORT` | Bind del dashboard Flask local (por defecto 0.0.0.0:5050). |
| `DISABLED_DEVICES_FILE`, `SIM_PID_FILE` | Archivos de control usados para toggles y detención remota.

> Las variables `TB_PARENT_*` son opcionales: si se completan, las herramientas de borrado replicarán la limpieza en un servidor padre (por ejemplo, ThingsBoard central) además del edge local.

### 3.3 Instalación local (Python)
```bash
# Crear y activar entorno (opcional pero recomendado)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Instalar dependencias
pip install --upgrade pip
pip install -r requirements.txt
```
Ejecuta `py -3 -m compileall scripts` tras cambios estructurales para detectar errores tempranamente.

---

## 4. Ejecución local
### 4.1 Flujo completo con un solo comando
```bash
py -3 scripts/mqtt/run_stress_suite.py --deactivate-after --duration 120 --device-count 20
```
El orquestador realiza las siguientes acciones:
1. (Opcional) Aprovisiona la flota (`create_devices.py`), tomando parámetros de `.env`.
2. Activa todos los dispositivos (`activate_devices.py --all`).
3. Ejecuta el simulador asíncrono (`mqtt_stress_async.py`) con los argumentos provistos y cualquier parámetro adicional tras `--`.
4. Si se indicó `--deactivate-after`, desactiva nuevamente los dispositivos para que no queden enviando telemetría.

### 4.2 Ejecución directa del simulador
```bash
py -3 scripts/mqtt/mqtt_stress_async.py --duration 300 --device-count 200 --tokens-file data/provisioning/tokens.json
```
Argumentos frecuentes:
- `--ramp-percentages 25 50 100` para introducir la carga poco a poco.
- `--disable-dashboard` si se ejecuta en entornos sin acceso a puertos externos.
- `--log-dir`, `--metrics-dir` para redirigir artefactos a otra carpeta.

### 4.3 Control manual de dispositivos
- Activar todo: `py -3 scripts/mqtt/activate_devices.py --all`
- Desactivar dispositivos específicos: `py -3 scripts/mqtt/deactivate_devices.py --devices sim-001 sim-002`
- Alternar con reglas personalizadas: `py -3 scripts/mqtt/toggle_devices.py --prefix laboratorio --deactivate`

Los estados se registran en `data/control/disabled_devices.json`; el simulador consulta este archivo periódicamente para pausar o reanudar dispositivos durante una corrida en curso.

### 4.4 Detener una corrida en segundo plano
Si tienes el PID almacenado (por defecto en `data/control/mqtt_stress.pid`), puedes terminar el proceso con:
```bash
py -3 scripts/mqtt/stop_simulation.py --cleanup
```
Añade `--force` si necesitas enviar SIGKILL tras agotar el tiempo de espera.

---

## 5. Ejecución con Docker
### 5.1 Construcción de la imagen
```bash
docker build -t tb-load-lab .
```
> Asegúrate de que `.env` y la carpeta `data/` existan antes de construir. `.dockerignore` evita que el contexto incluya datos sensibles.

### 5.2 Ejecutar una prueba desde la imagen
Linux/macOS:
```bash
docker run --rm --env-file .env \
  -v "$(pwd)/data:/app/data" \
  tb-load-lab --deactivate-after --duration 120 --device-count 20
```
Windows PowerShell:
```powershell
docker run --rm --env-file .env `
  -v "${PWD}\data:/app/data" `
  tb-load-lab --deactivate-after --duration 120 --device-count 20
```
Todos los argumentos posteriores al nombre de la imagen se reenvían a `run_stress_suite.py`. Si necesitas opciones específicas del simulador, agrégalas al final, por ejemplo:
```bash
docker run --rm --env-file .env -v "$(pwd)/data:/app/data" \
  tb-load-lab --deactivate-after --duration 120 --device-count 20 \
  -- --host 192.168.2.125 --port 1883 --tokens-file data/provisioning/tokens.json
```

### 5.3 Usar docker-compose como shell
```bash
docker compose up --build
```
Esto abrirá un contenedor interactivo (comando por defecto `bash`) con el código montado en `/app`. Dentro del contenedor ejecuta los scripts igual que en la sección de ejecución local.

---

## 6. Scripts destacados
| Script | Descripción | Uso típico |
|--------|-------------|------------|
| `scripts/mqtt/create_devices.py` | Crea o actualiza los dispositivos simulados y sus tokens. | Ejecutar al cambiar `DEVICE_COUNT` o para refrescar tokens. |
| `scripts/mqtt/delete_devices.py` | Elimina los dispositivos listados en `data/provisioning/devices.csv`. | Limpieza tras pruebas controladas. |
| `scripts/mqtt/delete_by_prefix.py` | Borra todos los dispositivos de un prefijo dado. | Restablecer el entorno cuando hay colisiones en los nombres. |
| `scripts/mqtt/report_last_run.py` | Resume `data/runs/latest.json` de forma legible. | Documentar resultados tras cada corrida. |
| `scripts/mqtt/run_stress_suite.py` | Pipeline completo (provisión ? activación ? simulación ? desactivación). | Ejecución estándar de carga. |
| `scripts/mqtt/stop_simulation.py` | Envía una señal al proceso del simulador leyendo el PID guardado. | Interrumpir pruebas sin acceder a la consola original. |
| `scripts/mqtt/activate_devices.py` / `deactivate_devices.py` | Control masivo del estado de la flota. | Preparar y limpiar el entorno antes/después de cada sesión. |
| `locustfile.py` | Cliente HTTP de ejemplo para Locust. | Lanza cargas adicionales: `locust -f locustfile.py`. |

---

## 7. Artefactos generados
| Carpeta | Contenido |
|---------|-----------|
| `data/provisioning/` | `tokens.json` (mapa nombre/token) y `devices.csv` (ID, nombre, label, token). |
| `data/logs/` | Archivos `.jsonl` con eventos por dispositivo (conexión, publicación, errores). |
| `data/metrics/` | CSVs con snapshots periódicos (`messages_per_second`, latencias, tasas de error). |
| `data/control/` | `disabled_devices.json` y `mqtt_stress.pid` para control manual. |

Conserva esta carpeta entre corridas para reutilizar tokens y comparar resultados; cuando montes la imagen Docker, recuerda mapear `data/` como volumen.

---

## 8. Flujo operativo recomendado
1. **Preparación**: actualizar `.env`, activar dispositivos necesarios.
2. **Verificación rápida**: `py -3 scripts/mqtt/check_connectivity.py` asegura acceso REST.
3. **Provisión (opcional)**: `py -3 scripts/mqtt/create_devices.py`.
4. **Ejecución**: `py -3 scripts/mqtt/run_stress_suite.py --deactivate-after --duration <seg> --device-count <n>`.
5. **Monitoreo**: abrir `http://<host-dashboard>:5050` para revisar métricas (puerto configurable).
6. **Reporte**: `py -3 scripts/mqtt/report_last_run.py` y revisar `data/metrics/`.
7. **Limpieza**: `py -3 scripts/mqtt/deactivate_devices.py --all` o ejecutar con `--deactivate-after`.

---

## 9. Solución de problemas
- **No se generan tokens** ? Revisa `DEVICE_PROFILE_ID` y credenciales; el mensaje de error saldrá por consola.
- **MQTT desconecta constantemente** ? Verifica certificados si `MQTT_TLS=1`, QoS seleccionado y latencia de red; revisa `disconnect_causes` en el dashboard o en los logs.
- **Dashboard no visible** ? Confirma que el proceso está activo y que el puerto `METRICS_PORT` no está ocupado. En Windows ejecuta `netstat -ano | findstr :5050`.
- **Docker no arranca** ? Asegúrate de que Docker Desktop esté activo y que `data/` exista en la máquina anfitriona para montarlo como volumen.
- **Locust no encuentra el host** ? Establece la variable `TARGET_PATH` y la URL objetivo al lanzar `locust` (`locust -f locustfile.py --host http://mi-host`).

---

## 10. Recursos adicionales
- [Documentación oficial de ThingsBoard](https://thingsboard.io/docs/)
- [asyncio-mqtt](https://github.com/sbtinstruments/asyncio-mqtt)
- [Flask](https://flask.palletsprojects.com/)
- [Locust](https://docs.locust.io/)

Para reportes o soporte interno, adjunta los archivos `data/logs/<run-id>.jsonl`, `data/metrics/<run-id>.csv`, `data/runs/<run-id>.json` y una copia depurada de `.env`.

Happy load testing!
