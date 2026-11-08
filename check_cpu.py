# /// script
# requires-python = ">=3.10"
# dependencies = ["psutil>=5.9,<8"]
# ///
"""Run on the computer hosting Meet: uv run check_cpu.py"""
import json
import platform
import subprocess
from pathlib import Path

import psutil


def cpu_name():
    system = platform.system()
    if system == 'Linux':
        try:
            for line in Path('/proc/cpuinfo').read_text().splitlines():
                if line.startswith('model name'):
                    return line.partition(':')[2].strip()
        except OSError:
            pass
    commands = {
        'Darwin': ['sysctl', '-n', 'machdep.cpu.brand_string'],
        'Windows': ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                    "(Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name) -join '; '"],
    }
    if system in commands:
        try:
            result = subprocess.run(commands[system], capture_output=True, text=True,
                                    timeout=10, check=True)
            if result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return platform.processor() or 'Unknown'


def main():
    model = cpu_name()
    # Sample a short interval to distinguish a busy computer from an idle one.
    per_core = psutil.cpu_percent(interval=1, percpu=True)
    memory = psutil.virtual_memory()
    report = {
        'os': platform.system(),
        'architecture': platform.machine(),
        'cpu': model,
        'physical_cores': psutil.cpu_count(logical=False),
        'logical_cpus': psutil.cpu_count(logical=True),
        'cpu_busy_percent': round(sum(per_core) / len(per_core), 1) if per_core else None,
        'ram_total_gib': round(memory.total / 1024**3, 1),
        'ram_available_gib': round(memory.available / 1024**3, 1),
        'note': 'One-second utilization snapshot, not a sustained performance or Whisper benchmark.',
    }
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
