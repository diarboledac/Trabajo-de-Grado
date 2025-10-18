#!/usr/bin/env python3
"""Launch MQTT telemetry using previously provisioned devices (starts dashboard too)."""
from __future__ import annotations

import threading
import time

from run_dashboard import DashboardServer, start_dashboard_server
from simcore import (
    MAX_DEVICES,
    METRICS_FILE,
    STOP_FILE,
    SimulatorConfig,
    StartCoordinator,
    StopSignal,
    MetricsCollector,
    MetricsReporter,
    MetricsWriter,
    SimLoop,
    ensure_data_dir,
    load_tokens,
    write_idle_metrics,
)


def main() -> None:
    config = SimulatorConfig.load()
    tokens = load_tokens(limit=config.device_count)
    if not tokens:
        write_idle_metrics("No hay tokens. Ejecuta provision_devices.py primero.")
        raise SystemExit("[ERROR] data/tokens.json no existe o esta vacio. Ejecuta provision_devices.py")

    device_items = sorted(tokens.items())
    device_count = len(device_items)
    if device_count == 0:
        write_idle_metrics("No se encontraron dispositivos para transmitir.")
        raise SystemExit("[ERROR] data/tokens.json no contiene dispositivos.")

    if device_count < config.device_count:
        print(
            f"[WARN] DEVICE_COUNT={config.device_count} pero solo hay {device_count} tokens. "
            "Se usara la cantidad disponible."
        )

    ensure_data_dir()
    stop_signal = StopSignal(STOP_FILE)
    stop_signal.clear_flag()
    barrier = threading.Barrier(device_count + 1)
    coordinator = StartCoordinator(config.start_lead_time)
    metrics = MetricsCollector(device_count, config.publish_interval)

    for name, _ in device_items:
        metrics.register_device(name)

    dashboard_server: DashboardServer | None = None
    reporter: MetricsReporter | None = None
    try:
        dashboard_server = start_dashboard_server(
            config.dashboard_host,
            config.dashboard_port,
            config.dashboard_refresh_ms,
        )
        print(f"[INFO] Dashboard integrado disponible en {dashboard_server.address}.")
    except OSError as exc:
        print(f"[WARN] No se pudo iniciar el dashboard integrado: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Error iniciando el dashboard integrado: {exc}")
        dashboard_server = None

    running_event = threading.Event()

    def status_getter() -> str:
        if not running_event.is_set():
            return "starting"
        if stop_signal.is_set():
            return "stopping"
        return "running"

    def extra_provider() -> dict[str, str]:
        if not running_event.is_set():
            return {"message": "Inicializando telemetria"}
        if stop_signal.is_set():
            return {"message": "Deteniendo telemetria"}
        return {"message": "Telemetria en ejecucion"}

    writer = MetricsWriter(metrics, status_getter, interval=2.0, extra_provider=extra_provider)
    writer.write_snapshot()
    writer.start()

    reporter = MetricsReporter(metrics, interval=5.0)
    reporter.start()

    workers: list[SimLoop] = []
    for name, token in device_items:
        worker = SimLoop(
            name=name,
            token=token,
            config=config,
            barrier=barrier,
            start_coordinator=coordinator,
            metrics=metrics,
            stop_signal=stop_signal,
        )
        worker.start()
        workers.append(worker)

    try:
        start_index = barrier.wait()
        running_event.set()
        if start_index == 0:
            coordinator.release()
            wall_time = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(time.time() + config.start_lead_time),
            )
            print(
                f"[INFO] Telemetria sincronizada lista ({device_count} dispositivos). "
                f"Primer pulso a las {wall_time} con lead {config.start_lead_time:.2f}s."
            )
        else:
            coordinator.wait()
    except threading.BrokenBarrierError:
        print("[WARN] No todos los workers llegaron a la barrera; revisa la configuracion MQTT.")

    print(
        f"[INFO] Enviando telemetria a {config.mqtt_host}:{config.mqtt_port} "
        f"(TLS={'si' if config.mqtt_tls else 'no'})."
    )
    print(f"[INFO] Puedes detener la simulacion con Ctrl+C o ejecutando stop_telemetry.py (crea {STOP_FILE.name}).")

    try:
        while not stop_signal.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[INFO] Ctrl+C recibido, deteniendo telemetria...")
        stop_signal.trip()
    finally:
        stop_signal.trip()
        for worker in workers:
            worker.join(timeout=2)
        writer.stop(final_status="stopped", extra={"message": "Telemetria detenida"})
        stop_signal.clear_flag()
        if reporter is not None and reporter.is_alive():
            reporter.stop()
            reporter.join(timeout=2)
        if dashboard_server is not None and dashboard_server.is_alive():
            dashboard_server.shutdown()
            dashboard_server.join(timeout=2)
            print("[INFO] Dashboard integrado detenido.")
        snapshot = metrics.snapshot()
        print("[OK] Telemetria detenida.")
        print("[METRICS] Resumen final:")
        print(f"  Dispositivos totales: {snapshot.device_count}/{MAX_DEVICES}")
        print(f"  Conectados: {snapshot.connected_devices}")
        print(f"  Desconectados: {snapshot.disconnected_devices}")
        print(f"  Fallidos: {snapshot.failed_devices}")
        print(f"  Paquetes enviados: {snapshot.total_packets_sent}")
        print(f"  Paquetes fallidos: {snapshot.total_packets_failed}")
        print(f"  Volumen total: {snapshot.total_volume_mb:.3f} MB")
        print(f"  Ancho de banda: {snapshot.bandwidth_mbps:.3f} Mbps")
        print(f"  Archivo de metricas: {METRICS_FILE}")


if __name__ == "__main__":
    main()
