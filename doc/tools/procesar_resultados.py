#!/usr/bin/env python3
"""Procesa reportes en data/metrics/reports y genera artefactos en doc/resultados/*."""
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
import pandas as pd  # noqa: E402

# Rutas base
repo_root = Path(__file__).resolve().parents[2]
METRICS_DIR = repo_root / "data" / "metrics"
REPORTS_DIR = METRICS_DIR / "reports"
OUTPUT_LATEX_DIR = repo_root / "doc"
OUTPUT_FIGS_DIR = repo_root / "doc" / "resultados" / "figuras_auto"
OUTPUT_TABLES_DIR = repo_root / "doc" / "resultados" / "tablas_auto"
OUTPUT_TEX = OUTPUT_LATEX_DIR / "resultados_auto.tex"
MANIFEST = REPORTS_DIR / "manifest.json"
HALOW_DIR = METRICS_DIR


def ensure_paths() -> None:
    """Crea directorios de entrada/salida necesarios."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_LATEX_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FIGS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_TABLES_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_LATEX_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FIGS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_TABLES_DIR.mkdir(parents=True, exist_ok=True)


def _parse_timestamp(entry: Dict[str, Any]) -> datetime:
    ts_raw = entry.get("timestamp") or ""
    try:
        return datetime.fromisoformat(ts_raw)
    except Exception:
        # Fallback: si no hay timestamp ISO, ordenamos por nombre de sesión
        return datetime.min


def load_manifest() -> List[Dict[str, Any]]:
    """Lee manifest conservando todas las sesiones registradas."""
    if not MANIFEST.exists():
        return []

    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception:
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


def latex_escape(text: Any) -> str:
    """Escapa caracteres especiales para su uso en LaTeX."""
    if text is None:
        return "-"
    replacements = {
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
        "\\": r"\textbackslash{}",
    }
    return "".join(replacements.get(ch, ch) for ch in str(text))


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
        HALOW_DIR / f"{stem}-halow.csv",
    ]
    if "_" in stem:
        tail = stem.split("_", 1)[1]
        candidates.append(HALOW_DIR / f"halow_{tail}.csv")
        candidates.append(HALOW_DIR / f"{scenario}_halow_{tail}.csv")
    if "-" in stem:
        tail = stem.split("-", 1)[1]
        candidates.append(HALOW_DIR / f"{tail}-halow.csv")
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


def build_summary_row(mfile: Path, cfg_label: str, halow_path: Optional[Path]) -> Dict[str, Any]:
    """Lee un CSV de métricas MQTT y opcionalmente uno de HaLow, devuelve resumen por configuración."""
    rows = load_csv(mfile)
    msum = summarize_csv(rows)

    hsum: Dict[str, Any] = {}
    if halow_path and halow_path.exists():
        hsum = summarize_halow(load_csv(halow_path))

    row = {"config": cfg_label, "metrics_file": mfile.name}
    row.update(msum)
    row.update(hsum)
    return row


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


def infer_scenario(session_id: str) -> str:
    """Devuelve el nombre de escenario a partir del id corto de sesión."""
    low = session_id.lower()
    if low.startswith("p1"):
        return "Prueba1"
    if low.startswith("p2"):
        return "Prueba2"
    if low.startswith("p3"):
        return "Prueba3"
    if low.startswith("p4"):
        return "Prueba4"
    if low.startswith("p5"):
        return "Prueba5"
    if low.startswith("p6"):
        return "Prueba6"
    if session_id.startswith("Prueba") and "_" in session_id:
        return session_id.split("_", 1)[0]
    if session_id.startswith("Prueba"):
        return session_id
    return session_id


def discover_sessions() -> List[Dict[str, Any]]:
    """Fusiona manifest.json con los reportes presentes en disco."""
    manifest_map = {entry.get("session"): entry for entry in load_manifest() if entry.get("session")}
    by_report: Dict[Path, Dict[str, Any]] = {}
    existing_sessions: Dict[str, Dict[str, Any]] = {}

    def manifest_lookup(stem: str, key: str) -> Optional[str]:
        entry = manifest_map.get(stem)
        if not entry:
            return None
        value = entry.get(key)
        return str(value) if value else None

    for tex in REPORTS_DIR.glob("*-report.tex"):
        tex_path = tex.resolve()
        if tex_path in by_report:
            continue
        stem = tex.stem.replace("-report", "")
        scenario = manifest_lookup(stem, "scenario") or infer_scenario(stem)
        entry: Dict[str, Any] = {
            "scenario": scenario,
            "session": stem,
            "report_tex": tex_path.as_posix(),
            "report_png": tex.with_name(f"{stem}-overview.png").resolve().as_posix(),
            "csv": manifest_lookup(stem, "csv"),
            "halow_csv": manifest_lookup(stem, "halow_csv"),
            "timestamp": manifest_lookup(stem, "timestamp")
            or datetime.fromtimestamp(tex.stat().st_mtime).isoformat(),
        }
        csv_path = find_metrics_csv(stem) if not entry["csv"] else Path(entry["csv"])
        if csv_path and Path(csv_path).exists():
            entry["csv"] = csv_path.as_posix()
        halow_path = find_halow_csv(stem, scenario) if not entry["halow_csv"] else Path(entry["halow_csv"])
        if halow_path and Path(halow_path).exists():
            entry["halow_csv"] = halow_path.as_posix()
        by_report[tex_path] = entry
        existing_sessions[entry["session"]] = entry

    # Agregar sesiones basadas solo en archivos de métricas si no hay report.tex
    for mfile in METRICS_DIR.glob("*-metrics.csv"):
        stem = mfile.stem.replace("-metrics", "")
        if stem in existing_sessions:
            continue
        escenario = manifest_lookup(stem, "scenario") or (
            stem.split("_", 1)[0] if "_" in stem else infer_scenario(stem)
        )
        entry = {
            "scenario": escenario,
            "session": stem,
            "csv": manifest_lookup(stem, "csv") or mfile.as_posix(),
            "halow_csv": manifest_lookup(stem, "halow_csv"),
            "report_tex": None,
            "report_png": None,
            "timestamp": manifest_lookup(stem, "timestamp")
            or datetime.fromtimestamp(mfile.stat().st_mtime).isoformat(),
        }
        if not entry["halow_csv"]:
            halow_path = find_halow_csv(stem, escenario)
            if halow_path:
                entry["halow_csv"] = halow_path.as_posix()
        existing_sessions[stem] = entry
        by_report[mfile] = entry

    return list(by_report.values())


def fmt_int(val: Any) -> str:
    return "-" if val is None else f"{int(val)}"


def fmt_float(val: Any) -> str:
    return "-" if val is None else f"{float(val):.3f}"


def build_tex(escenarios: Dict[str, List[Dict[str, Any]]]) -> str:
    lines = [
        "% Archivo generado automaticamente por doc/tools/procesar_resultados.py",
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
        escenario_tex = latex_escape(escenario)
        slug = slugify(escenario)
        figure_entry = next(
            (s for s in reversed(sesiones_ordenadas) if s.get("report_png") and Path(s["report_png"]).exists()),
            None,
        )

        lines.append(f"\\subsubsection{{Escenario {escenario_tex} ({nodos_txt} dispositivos)}}")

        if figure_entry:
            # Paths en LaTeX deben ser relativos al directorio doc/.
            rel = (Path("..") / Path(figure_entry["report_png"]).relative_to(repo_root)).as_posix()
            lines.append("\\begin{figure}[!t]")
            lines.append("  \\centering")
            lines.append(f"  \\includegraphics[width=\\linewidth]{{{rel}}}")
            lines.append(f"  \\caption{{Evoluci\\'on de m\\'etricas del escenario {escenario_tex}.}}")
            lines.append(f"  \\label{{fig:esc-{slug}}}")
            lines.append("\\end{figure}")

        lines.append("\\begin{table}[!t]")
        lines.append("  \\centering")
        lines.append(f"  \\caption{{Resumen del escenario {escenario_tex}.}}")
        lines.append(f"  \\label{{tab:esc-{slug}}}")
        lines.append("  \\begin{tabular}{@{}lrrrrr@{}}")
        lines.append("    \\toprule")
        lines.append("    Sesi\\'on & Nodos & Duraci\\'on (s) & Mensajes & Lat. prom. (ms) & Throughput (msg/s)\\\\")
        lines.append("    \\midrule")
        for s in sesiones_ordenadas:
            m = s["metricas"]
            lines.append(
                f"    {latex_escape(s['session'])} & "
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


def generar_tablas_y_figuras(escenarios: Dict[str, List[Dict[str, Any]]]) -> None:
    """Genera tablas (CSV y LaTeX) y figuras de latencia/throughput por escenario."""
    for idx, (escenario, sesiones) in enumerate(sorted(escenarios.items()), start=1):
        sesiones_ordenadas = sorted(sesiones, key=lambda s: s.get("timestamp", ""))
        rows = []
        for s in sesiones_ordenadas:
            m = s["metricas"]
            row = {"config": s["session"]}
            row.update(m)
            rows.append(row)

        if not rows:
            continue

        df = pd.DataFrame(rows)
        nombre = f"prueba_{idx}"

        # Tabla CSV
        csv_out = OUTPUT_TABLES_DIR / f"{nombre}_resumen.csv"
        df.to_csv(csv_out, index=False)

        # Tabla LaTeX
        try:
            latex_table = df.to_latex(index=False, float_format="%.2f")
            tex_out = OUTPUT_TABLES_DIR / f"{nombre}_resumen.tex"
            tex_out.write_text(latex_table, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] No se pudo generar tabla LaTeX para {escenario}: {exc}")

        # Figura latencia promedio
        if "lat_prom_ms" in df.columns:
            plt.figure()
            plt.plot(df["config"], df["lat_prom_ms"], marker="o")
            plt.xlabel("Configuración")
            plt.ylabel("Latencia promedio (ms)")
            plt.title(f"{nombre.upper()}: Latencia promedio por configuración")
            plt.tight_layout()
            plt.savefig(OUTPUT_FIGS_DIR / f"{nombre}_latencia.png")
            plt.close()

        # Figura throughput
        if "throughput_msg_s" in df.columns:
            plt.figure()
            plt.plot(df["config"], df["throughput_msg_s"], marker="o")
            plt.xlabel("Configuración")
            plt.ylabel("Mensajes por segundo (promedio)")
            plt.title(f"{nombre.upper()}: Throughput por configuración")
            plt.tight_layout()
            plt.savefig(OUTPUT_FIGS_DIR / f"{nombre}_throughput.png")
            plt.close()


def main() -> None:
    ensure_paths()
    sesiones = discover_sessions()

    escenarios: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for entry in sesiones:
        csv_path = Path(entry.get("csv") or "")
        metricas = summarize_csv(load_csv(csv_path))

        halow_path = Path(entry.get("halow_csv") or "")
        halow_rows = load_csv(halow_path)
        metricas.update(summarize_halow(halow_rows))
        halow_figs: List[Path] = plot_halow(halow_path, OUTPUT_FIGS_DIR) if halow_rows else []

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
    generar_tablas_y_figuras(escenarios)


if __name__ == "__main__":
    main()

