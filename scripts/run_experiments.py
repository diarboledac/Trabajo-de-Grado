#!/usr/bin/env python3
"""Orquesta pruebas predefinidas ejecutando run_stress_suite.py."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts" / "mqtt"
RUN_SUITE = SCRIPTS_DIR / "run_stress_suite.py"
METRICS_DIR = ROOT / "data" / "metrics"
REPORTS_DIR = METRICS_DIR / "reports"
MANIFEST = REPORTS_DIR / "manifest.json"


def snapshot(paths: Iterable[Path]) -> set[Path]:
    return {p.resolve() for p in paths}


def list_metrics_files() -> set[Path]:
    return snapshot(METRICS_DIR.glob("*-metrics.csv")) | snapshot(METRICS_DIR.glob("halow_*.csv"))


def list_report_files() -> set[Path]:
    return snapshot(REPORTS_DIR.glob("*-report.tex")) | snapshot(REPORTS_DIR.glob("*-overview.png"))


def load_manifest() -> list:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_manifest(entries: list) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def tag_artifacts(prefix: str, new_csv: set[Path], new_reports: set[Path]) -> None:
    """Renombra/copias artefactos nuevos con prefijo de escenario y actualiza manifest."""
    manifest = load_manifest()
    halow_csv = None
    for csv_path in new_csv:
        if csv_path.name.startswith("halow_"):
            halow_csv = csv_path
            continue
        target = csv_path.with_name(f"{prefix}_{csv_path.name}")
        if not target.exists():
            target.write_bytes(csv_path.read_bytes())
    report_pairs = {}
    for tex in (p for p in new_reports if p.suffix == ".tex"):
        stem = tex.stem.replace("-report", "")
        png = tex.with_name(f"{stem}-overview.png")
        report_pairs[tex] = png if png in new_reports else None
    for tex, png in report_pairs.items():
        tex_target = tex.with_name(f"{prefix}_{tex.name}")
        if not tex_target.exists():
            tex_target.write_bytes(tex.read_bytes())
        png_target = None
        if png and png.exists():
            png_target = png.with_name(f"{prefix}_{png.name}")
            if not png_target.exists():
                png_target.write_bytes(png.read_bytes())
        if halow_csv and halow_csv.exists():
            halow_target = halow_csv.with_name(f"{prefix}_{halow_csv.name}")
            if not halow_target.exists():
                halow_target.write_bytes(halow_csv.read_bytes())
        else:
            halow_target = halow_csv

        manifest.append(
            {
                "scenario": prefix,
                "session": tex_target.stem.replace("-report", ""),
                "report_tex": tex_target.as_posix(),
                "report_png": png_target.as_posix() if png_target else None,
                "csv": str(METRICS_DIR / tex.stem.replace("-report", "-metrics.csv")),
                "halow_csv": halow_target.as_posix() if halow_target else None,
                "timestamp": datetime.now().isoformat(),
            }
        )
    save_manifest(manifest)


def run_suite(args: List[str], *, escenario: str) -> None:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    before_csv = list_metrics_files()
    before_reports = list_report_files()
    cmd = [sys.executable, str(RUN_SUITE)] + args
    print(f"[INFO] Ejecutando {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise SystemExit(f"run_stress_suite fallo con codigo {result.returncode}")
    after_csv = list_metrics_files()
    after_reports = list_report_files()
    new_csv = after_csv - before_csv
    new_reports = after_reports - before_reports
    if not new_csv:
        print("[WARN] No se detectaron CSV nuevos en data/metrics.")
    if not new_reports:
        print("[WARN] No se detectaron reportes nuevos en data/metrics/reports.")
    tag_artifacts(escenario, new_csv, new_reports)
    print(f"[OK] Prueba {escenario} completada. CSV nuevos: {len(new_csv)}, reportes: {len(new_reports)}")


def run_prueba_1() -> None:
    run_suite(
        [
            "--duration",
            "150",
            "--device-count",
            "20",
            "--interval",
            "10",
            "--ramp-percentages",
            "25",
            "50",
            "100",
            "--deactivate-after",
        ],
        escenario="Prueba1",
    )


def run_prueba_2() -> None:
    for count in (50, 100, 200, 400):
        run_suite(
            [
                "--duration",
                "180",
                "--device-count",
                str(count),
                "--interval",
                "5",
                "--deactivate-after",
            ],
            escenario=f"Prueba2_n{count}",
        )


def run_prueba_3() -> None:
    for interval in (10, 5, 2, 1):
        run_suite(
            [
                "--duration",
                "180",
                "--device-count",
                "200",
                "--interval",
                str(interval),
                "--deactivate-after",
            ],
            escenario=f"Prueba3_int{interval}",
        )


def run_prueba_4() -> None:
    for label, padding in (("small", 0), ("medium", 256), ("large", 1024)):
        run_suite(
            [
                "--duration",
                "180",
                "--device-count",
                "200",
                "--interval",
                "5",
                "--payload-padding",
                str(padding),
                "--deactivate-after",
            ],
            escenario=f"Prueba4_{label}",
        )


def run_prueba_5() -> None:
    for label, interval in (("sin_aggregate", 5), ("con_rate_limit", 10)):
        run_suite(
            [
                "--duration",
                "240",
                "--device-count",
                "200",
                "--interval",
                str(interval),
                "--deactivate-after",
            ],
            escenario=f"Prueba5_{label}",
        )


def run_prueba_6() -> None:
    run_suite(
        [
            "--duration",
            "7200",
            "--device-count",
            "1000",
            "--interval",
            "5",
            "--ramp-percentages",
            "25",
            "50",
            "100",
            "--deactivate-after",
        ],
        escenario="Prueba6",
    )


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Uso: py -3 scripts/run_experiments.py <1-6>")
    mapping = {
        "1": run_prueba_1,
        "2": run_prueba_2,
        "3": run_prueba_3,
        "4": run_prueba_4,
        "5": run_prueba_5,
        "6": run_prueba_6,
    }
    choice = sys.argv[1]
    if choice not in mapping:
        raise SystemExit("Prueba no soportada. Usa 1..6")
    mapping[choice]()


if __name__ == "__main__":
    main()
