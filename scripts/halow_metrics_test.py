import paramiko
import time
import sys
import os

# --- CONFIGURACIÓN ---
TUBE_IP = "192.168.1.103"
USERNAME = "root"
PASSWORD = "@banano2025"

# Comandos que queremos leer del TUBE
COMMANDS = [
    "cat /proc/net/wireless",    # RSSI, noise, quality
    "cat /proc/net/dev",         # throughput (bytes TX/RX)
    "iw ahl0 link"               # info del enlace HaLow (frecuencia, bitrate, señal)
]

def run_ssh_command(ssh, command):
    """Ejecuta un comando por SSH y devuelve la respuesta limpia."""
    stdin, stdout, stderr = ssh.exec_command(command)
    return stdout.read().decode().strip()


def main():
    print("\n====================================")
    print("   TUBE-AHM METRICS LIVE VIEWER")
    print("====================================\n")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    print(f"🔌 Conectando vía SSH a {TUBE_IP} ...")

    try:
        ssh.connect(TUBE_IP, username=USERNAME, password=PASSWORD, timeout=5)
        print("✅ Conexión SSH exitosa.\n")
    except Exception as e:
        print(f"❌ ERROR al conectar por SSH: {e}")
        sys.exit(1)

    print("📡 Obteniendo métricas en tiempo real (Ctrl + C para salir)...\n")

    try:
        while True:
            os.system("clear" if os.name == "posix" else "cls")
            print("📊 Métricas HaLow - Actualización cada 1 segundo\n")

            for cmd in COMMANDS:
                print(f"---- {cmd} ----")
                output = run_ssh_command(ssh, cmd)
                print(output + "\n")

            time.sleep(1)

    except KeyboardInterrupt:
        print("\n🛑 Monitoreo detenido por el usuario.")

    ssh.close()


if __name__ == "__main__":
    main()
# --- FIN DEL SCRIPT ---