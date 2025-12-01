import time
from collections import deque
from datetime import datetime

import paramiko
from dash import Dash, dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objs as go

# ================== CONFIGURACIÓN ==================
TUBE_IP = "192.168.1.103"
USERNAME = "root"
PASSWORD = "@banano2025"

# Nombre de la interfaz dentro del TUBE (ajusta si en tu /proc/net/wireless sale otra)
INTERFACE = "ahl0"

# Máximo de muestras: 60 minutos a 1 muestra/segundo
MAX_POINTS_60_MIN = 60 * 60

# Buffer circular donde guardamos las muestras
data_buffer = deque(maxlen=MAX_POINTS_60_MIN)

ssh_client = None


# ================== FUNCIONES SSH ==================
def get_ssh_client():
    """Devuelve un cliente SSH conectado (y reconecta si hace falta)."""
    global ssh_client
    if ssh_client is not None:
        return ssh_client

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(TUBE_IP, username=USERNAME, password=PASSWORD, timeout=5)
    ssh_client = client
    return ssh_client


def run_command(cmd: str) -> str:
    """Ejecuta un comando en el TUBE y devuelve su salida en texto."""
    client = get_ssh_client()
    stdin, stdout, stderr = client.exec_command(cmd)
    return stdout.read().decode()


# ================== PARSEO DE MÉTRICAS ==================
def parse_wireless(output):
    """
    /proc/net/wireless
    Inter-| sta-|   Quality        |   Discarded   | Missed | WE
     face | tus | link level noise |  nwid  crypt  frag  retry   misc | beacon
    """
    for line in output.splitlines():
        if line.strip().startswith(INTERFACE):
            parts = line.split()
            try:
                link = float(parts[2].strip('.'))
                level = float(parts[3].strip('.'))   # dBm
                noise = float(parts[4].strip('.'))   # dBm
            except (ValueError, IndexError):
                link = level = noise = None
            return link, level, noise
    return None, None, None


def parse_dev(output):
    """
    /proc/net/dev
    Interfaz: bytes_rx ... bytes_tx ...
    """
    for line in output.splitlines():
        if line.strip().startswith(INTERFACE + ":"):
            name, rest = line.split(":", 1)
            fields = rest.split()
            try:
                rx_bytes = int(fields[0])
                tx_bytes = int(fields[8])
            except (ValueError, IndexError):
                rx_bytes = tx_bytes = None
            return rx_bytes, tx_bytes
    return None, None


def sample_metrics(prev_sample):
    """Toma una muestra nueva desde el TUBE y la mete al buffer."""
    now = datetime.now()

    wireless = run_command("cat /proc/net/wireless")
    dev = run_command("cat /proc/net/dev")

    link, level, noise = parse_wireless(wireless)
    rx_bytes, tx_bytes = parse_dev(dev)

    rx_rate = tx_rate = None
    if prev_sample and rx_bytes is not None and tx_bytes is not None:
        dt = (now - prev_sample["time"]).total_seconds()
        if dt > 0:
            rx_rate = (rx_bytes - prev_sample["rx_bytes"]) * 8 / dt  # bit/s
            tx_rate = (tx_bytes - prev_sample["tx_bytes"]) * 8 / dt  # bit/s

    sample = {
        "time": now,
        "link": link,
        "level": level,
        "noise": noise,
        "rx_bytes": rx_bytes,
        "tx_bytes": tx_bytes,
        "rx_rate": rx_rate,
        "tx_rate": tx_rate,
    }

    data_buffer.append(sample)
    return sample


# ================== DASHBOARD ==================
app = Dash(__name__)
app.title = "HaLow Tube Metrics"

app.layout = html.Div(
    style={"fontFamily": "Arial", "margin": "20px"},
    children=[
        html.H1("HaLow Tube-AHM Dashboard"),
        html.P(f"TUBE IP: {TUBE_IP} | Interfaz: {INTERFACE}"),

        html.Div(
            style={"display": "flex", "gap": "40px", "marginBottom": "20px"},
            children=[
                html.Div([
                    html.Label("Ventana de tiempo"),
                    dcc.RadioItems(
                        id="window-selector",
                        options=[
                            {"label": "Últimos 10 min", "value": 10},
                            {"label": "Últimos 30 min", "value": 30},
                            {"label": "Últimos 60 min", "value": 60},
                        ],
                        value=10,
                        inline=True,
                    ),
                ]),
                html.Div([
                    html.Label("Frecuencia de actualización"),
                    dcc.RadioItems(
                        id="interval-selector",
                        options=[
                            {"label": "1 s", "value": 1},
                            {"label": "10 s", "value": 10},
                            {"label": "30 s", "value": 30},
                        ],
                        value=1,
                        inline=True,
                    ),
                ]),
            ],
        ),

        dcc.Interval(
            id="update-interval",
            interval=1000,   # milisegundos (se actualiza luego vía callback)
            n_intervals=0,
        ),

        dcc.Graph(id="rssi-graph"),
        dcc.Graph(id="throughput-graph"),
    ],
)


# Cambiar la frecuencia del Interval cuando el usuario elige otra
@app.callback(
    Output("update-interval", "interval"),
    Input("interval-selector", "value"),
)
def update_interval(seconds):
    # convertir a milisegundos, mínimo 1000ms para no reventar nada
    return max(int(seconds * 1000), 1000)


# Actualizar gráficas cada vez que suena el Interval
@app.callback(
    [
        Output("rssi-graph", "figure"),
        Output("throughput-graph", "figure"),
    ],
    [
        Input("update-interval", "n_intervals"),
        Input("window-selector", "value"),
    ],
)
def update_graphs(n, window_minutes):
    # tomar una nueva muestra en cada tick
    prev = data_buffer[-1] if data_buffer else None
    try:
        sample_metrics(prev)
    except Exception as e:
        print("Error tomando métricas:", e)

    if not data_buffer:
        return go.Figure(), go.Figure()

    # filtrar por ventana de tiempo seleccionada
    cutoff_ts = datetime.now().timestamp() - window_minutes * 60
    filtered = [s for s in data_buffer if s["time"].timestamp() >= cutoff_ts]

    times = [s["time"] for s in filtered]
    levels = [s["level"] for s in filtered]
    noises = [s["noise"] for s in filtered]
    links = [s["link"] for s in filtered]
    rx_rates = [s["rx_rate"] for s in filtered]
    tx_rates = [s["tx_rate"] for s in filtered]

    # --- Gráfica RSSI / ruido / quality ---
    rssi_fig = go.Figure()
    rssi_fig.add_trace(go.Scatter(x=times, y=levels, mode="lines+markers", name="Señal (dBm)"))
    rssi_fig.add_trace(go.Scatter(x=times, y=noises, mode="lines+markers", name="Ruido (dBm)"))
    rssi_fig.add_trace(go.Scatter(x=times, y=links, mode="lines+markers", name="Quality"))
    rssi_fig.update_layout(
        title="Señal / Ruido / Quality",
        xaxis_title="Tiempo",
        yaxis_title="dBm / Quality",
        legend=dict(orientation="h"),
        margin=dict(l=50, r=20, t=40, b=40),
    )

    # --- Gráfica Throughput ---
    thr_fig = go.Figure()
    thr_fig.add_trace(go.Scatter(x=times, y=rx_rates, mode="lines+markers", name="RX (bit/s)"))
    thr_fig.add_trace(go.Scatter(x=times, y=tx_rates, mode="lines+markers", name="TX (bit/s)"))
    thr_fig.update_layout(
        title="Throughput enlace HaLow",
        xaxis_title="Tiempo",
        yaxis_title="bit/s",
        legend=dict(orientation="h"),
        margin=dict(l=50, r=20, t=40, b=40),
    )

    return rssi_fig, thr_fig


if __name__ == "__main__":
    # host 0.0.0.0 para verlo desde otros equipos si están en la misma red
    app.run(host="0.0.0.0", port=2020, debug=False)

