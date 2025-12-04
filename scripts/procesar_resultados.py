#!/usr/bin/env python3
"""Procesa reportes en data/metrics/reports y genera doc/resultados_auto.tex con el template solicitado."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
METRICS_DIR = ROOT / "data" / "metrics"
REPORTS_DIR = METRICS_DIR / "reports"
OUTPUT_TEX = ROOT / "doc" / "resultados_auto.tex"
MANIFEST = REPORTS_DIR / "manifest.json"
HALOW_DIR = METRICS_DIR


def ensure_paths() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_TEX.parent.mkdir(parents=True, exist_ok=True)


def load_manifest() -> List[Dict[str, Any]]:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_manifest(entries: List[Dict[str, Any]]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_float(val: Any) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def load_csv(path: Path) -> List[Dict[str, str]]:
    if not path or not path.exists() or not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def slugify(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")


def find_metrics_csv(stem: str) -> Optional[Path]:
    """Busca el CSV de métricas asociado a un report.tex."""
    candidates = [
        METRICS_DIR / f"{stem}-metrics.csv",
    ]
    if "_" in stem:
        tail = stem.split("_", 1)[1]
        candidates.append(METRICS_DIR / f"{tail}-metrics.csv")
    for cand in candidates:
        if cand.exists():
            return cand
    return None


def find_halow_csv(stem: str, scenario: str) -> Optional[Path]:
    """Busca el CSV de métricas HaLow asociado a la sesión."""
    candidates = [
        HALOW_DIR / f"halow_{stem}.csv",
    ]
    if "_" in stem:
        tail = stem.split("_", 1)[1]
        candidates.append(HALOW_DIR / f"halow_{tail}.csv")
        candidates.append(HALOW_DIR / f"{scenario}_halow_{tail}.csv")
    for cand in candidates:
        if cand.exists():
            return cand
    return None


def summarize_csv(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """Calcula métricas básicas de MQTT a partir del CSV principal."""
    if not rows:
        return {"valida": False, "warning": "CSV vacio"}
    last = rows[-1]

    def series(name: str) -> List[float]:
        return [v for v in (parse_float(r.get(name)) for r in rows) if v is not None]

    summary: Dict[str, Any] = {}

    for key in ("total_devices", "active_clients", "connected_devices"):
        val = parse_float(last.get(key))
        if val is not None:
            summary["nodos"] = int(val)
            break

    success = parse_float(last.get("successful_publishes"))
    failed = parse_float(last.get("failed_publishes"))
    if success is not None:
        summary["exitos"] = success
    if failed is not None:
        summary["fallidos"] = failed
    if success is not None or failed is not None:
        total_msgs = (success or 0) + (failed or 0)
        summary["mensajes"] = total_msgs
        if total_msgs > 0 and failed is not None:
            summary["perdida_pct"] = failed / total_msgs * 100.0

    dur = parse_float(last.get("elapsed_seconds")) or parse_float(last.get("uptime_seconds"))
    if dur is None and rows and rows[0].get("timestamp") and last.get("timestamp"):
        try:
            t0 = datetime.fromisoformat(rows[0]["timestamp"])
            t1 = datetime.fromisoformat(last["timestamp"])
            dur = (t1 - t0).total_seconds()
        except Exception:
            dur = None
    if dur is not None:
        summary["duracion_s"] = dur

    latencias = series("avg_latency_ms")
    if latencias:
        summary["lat_prom_ms"] = mean(latencias)
        summary["lat_min_ms"] = min(latencias)
        summary["lat_max_ms"] = max(latencias)

    mps = series("messages_per_second")
    if mps:
        summary["throughput_msg_s"] = mean(mps)

    bw = series("bandwidth_mbps")
    if bw:
        summary["bandwidth_mbps"] = mean(bw)

    summary["valida"] = True
    return summary


def summarize_halow(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    if not rows:
        return {}

    def s(name: str) -> List[float]:
        vals = []
        for r in rows:
            v = parse_float(r.get(name))
            if v is not None:
                vals.append(v)
        return vals

    rssi = s("halow_rssi_dbm")
    txr = s("halow_tx_rate_mbps")
    rxr = s("halow_rx_rate_mbps")
    return {
        "halow_rssi_avg": mean(rssi) if rssi else None,
        "halow_tx_rate_avg": mean(txr) if txr else None,
        "halow_rx_rate_avg": mean(rxr) if rxr else None,
    }


def conclusiones_auto(summary: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    nodos = summary.get("nodos")
    loss = summary.get("perdida_pct")
    lat = summary.get("lat_prom_ms")
    thr = summary.get("throughput_msg_s")
    dur = summary.get("duracion_s")

    if nodos:
        lines.append(f"Se ejercitaron aproximadamente {nodos} dispositivos.")
    if loss is not None:
        if loss < 1:
            lines.append("Entrega estable con p\\'erdida <1\\%.")
        elif loss < 5:
            lines.append("P\\'erdida moderada; revisar capacidad del broker en cargas mayores.")
        else:
            lines.append("P\\'erdida elevada que sugiere saturaci\\'on o fallas de red.")
    if lat is not None:
        if lat < 50:
            lines.append("La latencia promedio se mantuvo baja para el escenario evaluado.")
        elif lat < 200:
            lines.append("La latencia creci\\'o, pero sigue en un rango manejable.")
        else:
            lines.append("La latencia elevada indica posible congesti\\'on.")
    if thr is not None:
        lines.append(f"El throughput medio fue de {thr:.2f} msg/s.")
    if dur is not None and len(lines) < 4:
        lines.append(f"La duraci\\'on observada fue de {dur:.0f} s.")

    while len(lines) < 4:
        lines.append("Datos insuficientes para generar m\\'as conclusiones.")
    return lines[:4]


def plot_halow(csv_path: Path, out_dir: Path) -> List[Path]:
    """Genera figuras de RSSI y tasas HaLow si existe el CSV."""
    rows = load_csv(csv_path)
    if not rows:
        return []

    def series(name: str) -> tuple[list[str], list[float]]:
        ts: list[str] = []
        vals: list[float] = []
        for r in rows:
            val = parse_float(r.get(name))
            if val is not None and r.get("timestamp"):
                ts.append(r["timestamp"])
                vals.append(val)
        return ts, vals

    out_dir.mkdir(parents=True, exist_ok=True)
    figs: List[Path] = []

    ts, rssi = series("halow_rssi_dbm")
    if ts and rssi:
        fig, ax = plt.subplots()
        ax.plot(ts, rssi)
        ax.set_title("HaLow RSSI")
        ax.set_ylabel("dBm")
        ax.set_xlabel("timestamp")
        fig.autofmt_xdate()
        fig.tight_layout()
        dest = out_dir / f"{csv_path.stem}-rssi.png"
        fig.savefig(dest, dpi=120)
        plt.close(fig)
        figs.append(dest)

    ts_tx, txr = series("halow_tx_rate_mbps")
    ts_rx, rxr = series("halow_rx_rate_mbps")
    if ts_tx or ts_rx:
        fig, ax = plt.subplots()
        if ts_tx:
            ax.plot(ts_tx, txr, label="TX Mbps")
        if ts_rx:
            ax.plot(ts_rx, rxr, label="RX Mbps")
        ax.legend()
        ax.set_title("HaLow bitrates")
        ax.set_ylabel("Mbps")
        ax.set_xlabel("timestamp")
        fig.autofmt_xdate()
        fig.tight_layout()
        dest = out_dir / f"{csv_path.stem}-rates.png"
        fig.savefig(dest, dpi=120)
        plt.close(fig)
        figs.append(dest)
    return figs


def discover_sessions() -> List[Dict[str, Any]]:
    """Fusiona manifest.json con los reportes presentes en disco."""
    manifest_entries = load_manifest()
    by_report: Dict[Path, Dict[str, Any]] = {}

    def register(entry: Dict[str, Any]) -> None:
        report_tex = Path(entry.get("report_tex", ""))
        if report_tex:
            by_report[report_tex.resolve()] = entry

    for entry in manifest_entries:
        register(entry)

    for tex in REPORTS_DIR.glob("*-report.tex"):
        tex_path = tex.resolve()
        if tex_path in by_report:
            continue
        stem = tex.stem.replace("-report", "")
        scenario = stem.split("_", 1)[0] if stem.startswith("Prueba") and "_" in stem else stem
        entry: Dict[str, Any] = {
            "scenario": scenario,
            "session": stem,
            "report_tex": tex_path.as_posix(),
            "report_png": tex.with_name(f"{stem}-overview.png").resolve().as_posix(),
            "csv": None,
            "halow_csv": None,
            "timestamp": datetime.fromtimestamp(tex.stat().st_mtime).isoformat(),
        }
        csv_path = find_metrics_csv(stem)
        if csv_path:
            entry["csv"] = csv_path.as_posix()
        halow_path = find_halow_csv(stem, scenario)
        if halow_path:
            entry["halow_csv"] = halow_path.as_posix()
        by_report[tex_path] = entry

    sessions = list(by_report.values())
    if len(sessions) != len(manifest_entries):
        save_manifest(sessions)
    return sessions


def fmt_int(val: Any) -> str:
    return "-" if val is None else f"{int(val)}"


def fmt_float(val: Any) -> str:
    return "-" if val is None else f"{float(val):.3f}"


def build_tex(escenarios: Dict[str, List[Dict[str, Any]]]) -> str:
    lines = [
        "% Archivo generado automaticamente por scripts/procesar_resultados.py",
        "\\section{Resultados autom\\'aticos de pruebas}",
        "\\label{sec:resultados-automaticos}",
    ]
    if not escenarios:
        lines.append("No se encontraron reportes en \\texttt{data/metrics/reports}.")
        return "\n".join(lines) + "\n"

    for escenario, sesiones in sorted(escenarios.items()):
        sesiones_ordenadas = sorted(sesiones, key=lambda s: s.get("timestamp", ""))
        nodos_header = sesiones_ordenadas[0]["metricas"].get("nodos")
        nodos_txt = f"{int(nodos_header)}" if nodos_header is not None else "?"
        slug = slugify(escenario)
        figure_entry = next(
            (s for s in reversed(sesiones_ordenadas) if s.get("report_png") and Path(s["report_png"]).exists()),
            None,
        )

        lines.append(f"\\subsubsection{{Escenario {escenario} ({nodos_txt} dispositivos)}}")

        if figure_entry:
            # Paths en LaTeX deben ser relativos al directorio que contiene DocumentoGrado.tex (doc/).
            rel = (Path("..") / Path(figure_entry["report_png"]).relative_to(ROOT)).as_posix()
            lines.append("\\begin{figure}[!t]")
            lines.append("  \\centering")
            lines.append(f"  \\includegraphics[width=\\linewidth]{{{rel}}}")
            lines.append(f"  \\caption{{Evoluci\\'on de m\\'etricas del escenario {escenario}.}}")
            lines.append(f"  \\label{{fig:esc-{slug}}}")
            lines.append("\\end{figure}")

        lines.append("\\begin{table}[!t]")
        lines.append("  \\centering")
        lines.append(f"  \\caption{{Resumen del escenario {escenario}.}}")
        lines.append(f"  \\label{{tab:esc-{slug}}}")
        lines.append("  \\begin{tabular}{@{}lrrrrr@{}}")
        lines.append("    \\toprule")
        lines.append("    Sesi\\'on & Nodos & Duraci\\'on (s) & Mensajes & Lat. prom. (ms) & Throughput (msg/s)\\\\")
        lines.append("    \\midrule")
        for s in sesiones_ordenadas:
            m = s["metricas"]
            lines.append(
                f"    {s['session']} & "
                f"{fmt_int(m.get('nodos'))} & "
                f"{fmt_int(m.get('duracion_s'))} & "
                f"{fmt_int(m.get('mensajes'))} & "
                f"{fmt_float(m.get('lat_prom_ms'))} & "
                f"{fmt_float(m.get('throughput_msg_s'))}\\\\"
            )
        lines.append("    \\bottomrule")
        lines.append("  \\end{tabular}")
        lines.append("\\end{table}")

        concl = conclusiones_auto(sesiones_ordenadas[-1]["metricas"])
        lines.append("\\paragraph{Conclusiones autom\\'aticas}")
        lines.append("\\begin{itemize}")
        for c in concl:
            lines.append(f"  \\item {c}")
        lines.append("\\end{itemize}")
        lines.append("\\FloatBarrier")
    return "\n".join(lines) + "\n"


def main() -> None:
    ensure_paths()
    sesiones = discover_sessions()

    # Evita duplicados: se prioriza la sesión que tenga prefijo de escenario (PruebaX_*)
    filtered: Dict[str, Dict[str, Any]] = {}
    for entry in sesiones:
        session_name = entry.get("session") or ""
        if session_name.startswith("Prueba"):
            parts = session_name.split("_")
            if len(parts) >= 3:
                canonical = parts[-1]
            elif "_" in session_name:
                canonical = session_name.split("_", 1)[1]
            else:
                canonical = session_name
        else:
            canonical = session_name
        priority = 1 if session_name.startswith("Prueba") else 0
        current = filtered.get(canonical)
        if current and current.get("_priority", 0) > priority:
            continue
        entry["_priority"] = priority
        filtered[canonical] = entry

    escenarios: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for entry in filtered.values():
        csv_path = Path(entry.get("csv") or "")
        rows = load_csv(csv_path)
        metricas = summarize_csv(rows)

        halow_path = Path(entry.get("halow_csv") or "")
        halow_rows = load_csv(halow_path)
        metricas.update(summarize_halow(halow_rows))
        halow_figs: List[Path] = plot_halow(halow_path, REPORTS_DIR) if halow_rows else []

        escenarios[entry.get("scenario") or "Sesiones"].append(
            {
                "scenario": entry.get("scenario") or "Sesiones",
                "session": entry.get("session") or (csv_path.stem if csv_path else "sesion"),
                "metricas": metricas,
                "report_tex": entry.get("report_tex"),
                "report_png": entry.get("report_png"),
                "halow_figs": halow_figs,
                "timestamp": entry.get("timestamp", ""),
            }
        )

    contenido = build_tex(escenarios)
    OUTPUT_TEX.write_text(contenido, encoding="utf-8")
    print(f"[OK] LaTeX generado en {OUTPUT_TEX}")


if __name__ == "__main__":
    main()
