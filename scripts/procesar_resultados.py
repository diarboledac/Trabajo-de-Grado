#!/usr/bin/env python3
"""Procesa resultados experimentales, genera figuras y construye LaTeX auto."""
from __future__ import annotations

import csv
import math
import re
import textwrap
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "resultados"
FIGURES_DIR = ROOT / "figuras" / "resultados"
OUTPUT_TEX = ROOT / "docs" / "resultados_auto.tex"


def ensure_layout() -> None:
    """Crea carpetas necesarias sin borrar nada."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_TEX.parent.mkdir(parents=True, exist_ok=True)


def parse_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_csv(path: Path) -> List[Dict[str, str]]:
    """Carga un CSV en memoria respetando encabezados."""
    with path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def infer_params(path: Path, rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """Extrae parametros basicos desde el nombre del archivo o campos conocidos."""
    stem = path.stem
    params: Dict[str, Any] = {"archivo": path.name}
    match_nodes = re.search(r"n(\d+)", stem)
    if match_nodes:
        params["nodos"] = int(match_nodes.group(1))
    match_interval = re.search(r"s(\d+)", stem)
    if match_interval:
        params["intervalo_ms"] = int(match_interval.group(1))
    # Campos dentro del CSV
    if rows:
        last = rows[-1]
        for key in ("total_devices", "active_clients", "connected_devices"):
            val = parse_float(last.get(key))
            if val is not None:
                params.setdefault("nodos", int(val))
                break
        payload = parse_float(last.get("payload_bytes"))
        if payload is not None:
            params["payload_bytes"] = int(payload)
    return params


def collect_metrics(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """Resume mmetricas clave a partir de un CSV de snapshots."""
    metrics: Dict[str, Any] = {}
    if not rows:
        metrics["warning"] = "Archivo sin filas"
        metrics["valida"] = False
        return metrics

    last = rows[-1]

    def series(name: str) -> List[float]:
        return [v for v in (parse_float(r.get(name)) for r in rows) if v is not None]

    lat_values = series("avg_latency_ms")
    if lat_values:
        metrics["lat_avg_ms"] = mean(lat_values)
        metrics["lat_min_ms"] = min(lat_values)
        metrics["lat_max_ms"] = max(lat_values)

    success = parse_float(last.get("successful_publishes"))
    failed = parse_float(last.get("failed_publishes"))
    if success is not None:
        metrics["exitos"] = success
    if failed is not None:
        metrics["fallidos"] = failed
    if success is not None and failed is not None and success + failed > 0:
        metrics["perdida_pct"] = failed / (success + failed) * 100.0

    elapsed = parse_float(last.get("elapsed_seconds"))
    if elapsed is not None:
        metrics["duracion_s"] = elapsed

    mps_series = series("messages_per_second")
    if mps_series:
        metrics["throughput_msg_s"] = max(mps_series)
    elif success is not None and elapsed:
        metrics["throughput_msg_s"] = success / elapsed

    bw = series("bandwidth_mbps")
    if bw:
        metrics["bandwidth_mbps"] = max(bw)

    send_rate = series("avg_send_rate_per_device")
    if send_rate:
        metrics["envio_prom_dev"] = max(send_rate)

    # CPU (opcionales)
    for key in ("cpu_edge", "cpu_server", "cpu_broker"):
        values = series(key)
        if values:
            metrics[key] = max(values)

    # Enlace HaLow (opcionales)
    for key in ("bw_halow_kbps", "rssi", "mcs"):
        values = series(key)
        if values:
            metrics[key] = mean(values)

    # Validacion basica
    warnings: List[str] = []
    if success is None or elapsed is None:
        warnings.append("Faltan campos basicos (successful_publishes o elapsed_seconds)")
    if failed is not None and success is not None and failed > success:
        warnings.append("Fallidos mayores que exitos: posible error de prueba")
    if metrics.get("perdida_pct", 0.0) > 5.0:
        warnings.append("Perdida superior a 5%")
    metrics["advertencias"] = warnings
    metrics["valida"] = not warnings
    return metrics


def generar_conclusion(m: Dict[str, Any]) -> List[str]:
    """Construye conclusiones breves basadas en las mmetricas calculadas."""
    lines: List[str] = []
    loss = m.get("perdida_pct")
    lat = m.get("lat_avg_ms")
    bw = m.get("bandwidth_mbps")
    halow_bw = m.get("bw_halow_kbps")
    nodos = m.get("nodos") or m.get("total_devices")

    if loss is not None:
        if loss < 1.0:
            lines.append("La entrega de mensajes se mantuvo estable (pérdida < 1%).")
        elif loss < 5.0:
            lines.append("Se observó pérdida moderada de mensajes; revisar capacidad del broker.")
        else:
            lines.append("La pérdida es elevada; la prueba sugiere saturación o fallas de red.")

    if lat is not None:
        if lat < 100:
            lines.append("La latencia promedio estuvo en rango bajo para la carga aplicada.")
        elif lat < 500:
            lines.append("La latencia creció, pero se mantiene dentro de un rango aceptable.")
        else:
            lines.append("La latencia es alta; posible congestión en enlace o procesamiento.")

    if bw is not None:
        lines.append(f"El throughput pico alcanzó aproximadamente {bw:.3f} Mbps.")

    if halow_bw is not None:
        lines.append("El enlace HaLow aportó ancho de banda adicional medible en las capturas.")

    if nodos:
        lines.append(f"Se ejecutó con aproximadamente {nodos} nodos activos.")

    if not lines:
        lines.append("No se encontraron datos suficientes para extraer conclusiones.")
    return lines[:6]


def plot_xy(x: Iterable[float], y: Iterable[float], *, title: str, ylabel: str, xlabel: str, dest: Path) -> None:
    fig, ax = plt.subplots()
    ax.plot(list(x), list(y), marker="o")
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(dest, dpi=120)
    plt.close(fig)


def generar_figuras(rows: List[Dict[str, str]], stem: str) -> List[Path]:
    """Crea figuras por prueba si existen las columnas necesarias."""
    figuras: List[Path] = []
    elapsed = [parse_float(r.get("elapsed_seconds")) for r in rows]
    latency = [parse_float(r.get("avg_latency_ms")) for r in rows]
    throughput = [parse_float(r.get("messages_per_second")) for r in rows]
    loss = []
    for r in rows:
        success = parse_float(r.get("successful_publishes"))
        failed = parse_float(r.get("failed_publishes"))
        if success is not None and failed is not None and success + failed > 0:
            loss.append(failed / (success + failed) * 100.0)
        else:
            loss.append(None)

    if elapsed and any(v is not None for v in latency):
        x = [t if t is not None else idx for idx, t in enumerate(elapsed)]
        y = [v if v is not None else math.nan for v in latency]
        dest = FIGURES_DIR / f"{stem}-latencia.png"
        plot_xy(x, y, title="Latencia promedio", ylabel="ms", xlabel="Tiempo (s)", dest=dest)
        figuras.append(dest)

    if elapsed and any(v is not None for v in throughput):
        x = [t if t is not None else idx for idx, t in enumerate(elapsed)]
        y = [v if v is not None else math.nan for v in throughput]
        dest = FIGURES_DIR / f"{stem}-throughput.png"
        plot_xy(x, y, title="Throughput", ylabel="msg/s", xlabel="Tiempo (s)", dest=dest)
        figuras.append(dest)

    if elapsed and any(v is not None for v in loss):
        x = [t if t is not None else idx for idx, t in enumerate(elapsed)]
        y = [v if v is not None else math.nan for v in loss]
        dest = FIGURES_DIR / f"{stem}-perdida.png"
        plot_xy(x, y, title="Pérdida de mensajes", ylabel="% pérdida", xlabel="Tiempo (s)", dest=dest)
        figuras.append(dest)

    return figuras


def build_latex(resultados: List[Dict[str, Any]]) -> str:
    """Construye el contenido de docs/resultados_auto.tex."""
    lines = [
        "% Archivo generado automaticamente por scripts/procesar_resultados.py",
        "\\section{Resultados automáticos de pruebas}",
        "\\label{sec:resultados-automaticos}",
    ]
    if not resultados:
        lines.append("No se encontraron archivos CSV en la carpeta \\texttt{resultados/}.")
        return "\n".join(lines) + "\n"

    for resultado in resultados:
        nombre = resultado.get("archivo", "prueba")
        sub_title = f"\\subsection{{Prueba {nombre}}}"
        lines.append(sub_title)
        lines.append("\\begin{table}[h]")
        lines.append("\\centering")
        lines.append("\\begin{tabular}{l c}")
        lines.append("\\toprule")
        for label, key in (
            ("Nodos", "nodos"),
            ("Latencia media (ms)", "lat_avg_ms"),
            ("Latencia mínima (ms)", "lat_min_ms"),
            ("Latencia máxima (ms)", "lat_max_ms"),
            ("Throughput (msg/s)", "throughput_msg_s"),
            ("Pérdida (%)", "perdida_pct"),
            ("Ancho de banda (Mbps)", "bandwidth_mbps"),
            ("CPU edge (%)", "cpu_edge"),
        ):
            val = resultado.get("metricas", {}).get(key)
            if val is None:
                continue
            if isinstance(val, float):
                val_str = f"{val:.3f}"
            else:
                val_str = str(val)
            lines.append(f"{label} & {val_str}\\\\")
        lines.append("\\bottomrule")
        lines.append("\\end{tabular}")
        lines.append(f"\\caption{{Resumen de la prueba {nombre}.}}")
        lines.append("\\end{table}")

        figuras = resultado.get("figuras", [])
        for fig in figuras:
            rel_path = fig.relative_to(ROOT).as_posix()
            lines.append("\\begin{figure}[h]")
            lines.append("\\centering")
            lines.append(f"\\includegraphics[width=0.9\\linewidth]{{{rel_path}}}")
            lines.append(f"\\caption{{Gráfica generada para {nombre}.}}")
            lines.append("\\end{figure}")

        conclusiones = resultado.get("conclusiones", [])
        if conclusiones:
            lines.append("\\paragraph{Conclusiones automáticas}")
            lines.append("\\begin{itemize}")
            for c in conclusiones:
                lines.append(f"  \\item {c}")
            lines.append("\\end{itemize}")
    return "\n".join(lines) + "\n"


def procesar_archivo(path: Path) -> Dict[str, Any]:
    """Procesa un CSV individual y retorna mmetricas, figuras y conclusiones."""
    rows = load_csv(path)
    params = infer_params(path, rows)
    metrics = collect_metrics(rows)
    figuras = generar_figuras(rows, path.stem)
    conclusion = generar_conclusion({**params, **metrics})
    return {
        "archivo": path.name,
        "metricas": {**params, **metrics},
        "figuras": figuras,
        "conclusiones": conclusion,
    }


def main() -> None:
    ensure_layout()
    csv_files = sorted(RESULTS_DIR.glob("*.csv"))
    resultados: List[Dict[str, Any]] = []
    for csv_path in csv_files:
        try:
            resultados.append(procesar_archivo(csv_path))
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] No se pudo procesar {csv_path.name}: {exc}")
    contenido = build_latex(resultados)
    OUTPUT_TEX.write_text(contenido, encoding="utf-8")
    print(f"[OK] Archivo LaTeX actualizado: {OUTPUT_TEX}")
    if not csv_files:
        print("[INFO] No se encontraron CSV en resultados/. Se generó plantilla vacía.")


if __name__ == "__main__":
    main()
