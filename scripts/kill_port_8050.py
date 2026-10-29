"""
Kill any process listening on port 8050 (or a custom port specified via --port).
Safe for Windows and Linux.
"""
import argparse
import os
import subprocess
import sys


def find_pids_on_port(port: int) -> set:
    pids = set()
    if os.name == "nt":
        try:
            output = subprocess.check_output(f"netstat -ano | findstr :{port}", shell=True).decode(errors="ignore")
            for line in output.strip().splitlines():
                parts = line.strip().split()
                if len(parts) >= 5 and "LISTENING" in parts:
                    pid = int(parts[-1])
                    if pid > 0:
                        pids.add(pid)
        except Exception:
            pass
    else:
        try:
            output = subprocess.check_output(["lsof", "-t", f"-i:{port}"]).decode(errors="ignore")
            for line in output.strip().splitlines():
                if line.strip().isdigit():
                    pids.add(int(line.strip()))
        except Exception:
            pass
    return pids


def kill_pid(pid: int) -> bool:
    try:
        if os.name == "nt":
            subprocess.run(f"taskkill /PID {pid} /T /F", shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.kill(pid, 9)
        return True
    except Exception as e:
        print(f"Error killing PID {pid}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Kill service listening on port 8050 (or custom port)")
    parser.add_argument("--port", type=int, default=8050, help="Port to clear (default: 8050)")
    args = parser.parse_args()

    port = args.port
    print(f"Searching for active processes listening on port {port}...")
    pids = find_pids_on_port(port)

    if not pids:
        print(f"No active process found listening on port {port}. Port is already free.")
        return 0

    success = True
    for pid in sorted(pids):
        print(f"Found process listening on port {port} (PID: {pid}). Killing process tree...")
        if kill_pid(pid):
            print(f"Successfully killed PID {pid}.")
        else:
            print(f"Failed to kill PID {pid}.")
            success = False

    remaining = find_pids_on_port(port)
    if remaining:
        print(f"Warning: Processes still detected on port {port}: {remaining}")
        return 1

    print(f"Port {port} is now free and clean.")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
