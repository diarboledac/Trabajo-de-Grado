.PHONY: run-demo

run-demo:
	@echo "[1/3] Verificando conectividad con ThingsBoard..."
	python3 scripts/mqtt/check_connectivity.py
	@echo "[2/3] Aprovisionando una flota mínima de prueba..."
	DEVICE_COUNT=5 python3 scripts/mqtt/create_devices.py
	@echo "[3/3] Ejecutando una corrida corta del simulador..."
	python3 scripts/mqtt/run_stress_suite.py --skip-provision --device-count 5 --duration 30 --deactivate-after
