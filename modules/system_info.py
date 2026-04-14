"""T.M.O.S — System Information Module
Collects CPU, RAM, disk, network, GPU, and process stats.
"""

import platform
import time
from typing import Any

import psutil

_prev_net = None
_prev_time = None


def get_stats() -> dict[str, Any]:
    """Collect full system metrics snapshot."""
    global _prev_net, _prev_time

    cpu_percent = psutil.cpu_percent(interval=0.1)
    cpu_freq = psutil.cpu_freq()
    mem = psutil.virtual_memory()

    disk_path = 'C:\\' if platform.system() == 'Windows' else '/'
    disk = psutil.disk_usage(disk_path)

    # Network delta
    net = psutil.net_io_counters()
    now = time.time()
    upload_kb = download_kb = 0.0
    if _prev_net is not None and _prev_time is not None:
        elapsed = now - _prev_time
        if elapsed > 0:
            upload_kb   = round((net.bytes_sent - _prev_net.bytes_sent) / elapsed / 1024, 1)
            download_kb = round((net.bytes_recv - _prev_net.bytes_recv) / elapsed / 1024, 1)
    _prev_net  = net
    _prev_time = now

    # Per-core CPU (for advanced UI)
    per_core = psutil.cpu_percent(percpu=True)

    # Top processes by CPU
    top_procs = []
    try:
        for proc in sorted(
            psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']),
            key=lambda p: p.info.get('cpu_percent', 0) or 0,
            reverse=True,
        )[:5]:
            info = proc.info
            top_procs.append({
                'pid': info.get('pid', 0),
                'name': info.get('name', '?'),
                'cpu': round(info.get('cpu_percent', 0) or 0, 1),
                'mem': round(info.get('memory_percent', 0) or 0, 1),
            })
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    # Battery (laptops)
    battery = None
    batt = psutil.sensors_battery()
    if batt is not None:
        battery = {
            'percent': round(batt.percent),
            'plugged': batt.power_plugged,
            'remaining': round(batt.secsleft / 60) if batt.secsleft > 0 else None,
        }

    return {
        'cpu': {
            'brand':    platform.processor() or 'CPU',
            'cores':    psutil.cpu_count(logical=False) or psutil.cpu_count(),
            'threads':  psutil.cpu_count(),
            'percent':  round(cpu_percent),
            'per_core': per_core,
            'freq_mhz': round(cpu_freq.current) if cpu_freq else 0,
        },
        'ram': {
            'used':    round(mem.used    / 1024 ** 3, 1),
            'total':   round(mem.total   / 1024 ** 3, 1),
            'percent': mem.percent,
        },
        'disk': {
            'used':    round(disk.used   / 1024 ** 3, 1),
            'total':   round(disk.total  / 1024 ** 3, 1),
            'percent': disk.percent,
        },
        'network': {
            'upload':   max(0.0, upload_kb),
            'download': max(0.0, download_kb),
            'sent_total':  round(net.bytes_sent / 1024 ** 2, 1),
            'recv_total':  round(net.bytes_recv / 1024 ** 2, 1),
        },
        'os': {
            'platform': platform.system(),
            'release':  platform.release(),
            'hostname': platform.node(),
            'uptime':   round((time.time() - psutil.boot_time()) / 3600, 1),
        },
        'battery': battery,
        'processes': top_procs,
    }
