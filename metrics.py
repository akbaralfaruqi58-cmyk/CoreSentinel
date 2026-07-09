"""
CoreSentinel - Metrics Collector
=================================
Modul ini bertanggung jawab mengumpulkan seluruh metrik performa sistem:
- CPU (per-core usage, clock speed, suhu)
- GPU (usage, suhu, clock, memori VRAM) - via GPUtil (NVIDIA) dengan fallback aman
- RAM (terpakai vs tersedia, tren)
- Disk I/O (kecepatan baca/tulis real-time)
- Proses (top process by CPU/RAM, dampak terhadap multi-core)

Didesain agar tetap berjalan dengan baik meskipun beberapa sensor tidak tersedia
di platform tertentu (mis. suhu CPU tidak terbaca di Windows, atau tidak ada GPU NVIDIA).
"""

import time
import logging
import psutil
from collections import deque
from datetime import datetime

import gpu_windows  # aman diimpor di semua OS; otomatis nonaktif jika bukan Windows

logger = logging.getLogger("coresentinel.metrics")

try:
    import GPUtil
    _GPU_AVAILABLE = True
except Exception:
    _GPU_AVAILABLE = False

# Kata kunci proses "berat" yang relevan dengan skenario tugas:
# Browser (termasuk akses Web AI), Zoom, YouTube (terdeteksi lewat proses browser/player)
HEAVY_PROCESS_KEYWORDS = {
    "chrome": "Browser (Chrome)",
    "msedge": "Browser (Edge)",
    "firefox": "Browser (Firefox)",
    "brave": "Browser (Brave)",
    "opera": "Browser (Opera)",
    "zoom": "Zoom Meeting",
    "youtube": "YouTube (proses terkait)",
    "electron": "Aplikasi Electron (mis. Discord/Slack)",
}

# Riwayat historis untuk perhitungan tren & delta disk I/O
HISTORY_MAXLEN = 120  # menyimpan 120 sampel (2 menit @1s) untuk tren

history = {
    "cpu_percent": deque(maxlen=HISTORY_MAXLEN),
    "ram_percent": deque(maxlen=HISTORY_MAXLEN),
    "disk_read_mbps": deque(maxlen=HISTORY_MAXLEN),
    "disk_write_mbps": deque(maxlen=HISTORY_MAXLEN),
    "timestamps": deque(maxlen=HISTORY_MAXLEN),
}

_last_disk_io = psutil.disk_io_counters()
_last_disk_time = time.time()

# Cache objek psutil.Process yang PERSISTEN antar-snapshot.
# Ini penting: psutil.cpu_percent(None) menghitung delta terhadap panggilan
# SEBELUMNYA pada objek Process yang SAMA. Jika kita membuat objek Process baru
# setiap kali (mis. lewat psutil.process_iter() pada setiap snapshot), setiap
# panggilan akan selalu dianggap "panggilan pertama" dan selalu mengembalikan 0.0
# -- inilah yang menyebabkan tabel proses tampak selalu kosong. Dengan menyimpan
# objek Process yang sama di cache ini, delta CPU% terhitung benar antar-siklus.
_process_cache = {}

for pid in psutil.pids():
    try:
        p = psutil.Process(pid)
        p.cpu_percent(None)  # baseline pertama, hasil awal diabaikan
        _process_cache[pid] = p
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        pass


def _get_cpu_metrics():
    per_core = psutil.cpu_percent(interval=None, percpu=True)
    overall = sum(per_core) / len(per_core) if per_core else 0.0

    freq = psutil.cpu_freq()
    freq_current = round(freq.current, 1) if freq else None
    freq_max = round(freq.max, 1) if freq and freq.max else None

    temp = None
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            # ambil sensor 'coretemp' (Linux) atau sensor pertama yang tersedia
            for key in ("coretemp", "k10temp", "cpu_thermal"):
                if key in temps and temps[key]:
                    temp = round(temps[key][0].current, 1)
                    break
            if temp is None:
                first_key = next(iter(temps))
                if temps[first_key]:
                    temp = round(temps[first_key][0].current, 1)
    except (AttributeError, NotImplementedError):
        temp = None

    return {
        "overall_percent": round(overall, 1),
        "per_core_percent": [round(c, 1) for c in per_core],
        "core_count_logical": psutil.cpu_count(logical=True),
        "core_count_physical": psutil.cpu_count(logical=False),
        "clock_current_mhz": freq_current,
        "clock_max_mhz": freq_max,
        "temperature_c": temp,
    }


def _get_gpu_metrics():
    # 1. Coba jalur NVIDIA (nvidia-smi) dulu -- datanya paling lengkap:
    #    load, suhu, clock, dan VRAM akurat.
    if _GPU_AVAILABLE:
        try:
            gpus = GPUtil.getGPUs()
            if gpus:
                result = []
                for g in gpus:
                    result.append({
                        "name": g.name,
                        "load_percent": round(g.load * 100, 1),
                        "memory_used_mb": round(g.memoryUsed, 1),
                        "memory_total_mb": round(g.memoryTotal, 1),
                        "memory_percent": round((g.memoryUsed / g.memoryTotal) * 100, 1) if g.memoryTotal else None,
                        "temperature_c": g.temperature,
                    })
                return {"available": True, "gpus": result, "source": "nvidia_smi"}
        except Exception as e:
            logger.warning(f"Gagal membaca GPU via GPUtil/nvidia-smi: {e}")

    # 2. Fallback: GPU generik Windows (Intel/AMD/NVIDIA tanpa nvidia-smi) via WMI.
    #    Catatan: suhu & clock TIDAK tersedia lewat jalur ini (lihat gpu_windows.py).
    if gpu_windows.is_supported():
        result = gpu_windows.get_gpu_metrics()
        if result["available"]:
            return result

    return {
        "available": False,
        "reason": "Tidak ada GPU NVIDIA terdeteksi, dan deteksi generik (WMI) tidak tersedia/gagal di sistem ini.",
        "gpus": [],
    }


def _get_ram_metrics():
    vm = psutil.virtual_memory()
    return {
        "total_gb": round(vm.total / (1024 ** 3), 2),
        "used_gb": round(vm.used / (1024 ** 3), 2),
        "available_gb": round(vm.available / (1024 ** 3), 2),
        "percent": vm.percent,
    }


def _get_disk_metrics():
    global _last_disk_io, _last_disk_time
    current_io = psutil.disk_io_counters()
    now = time.time()
    elapsed = max(now - _last_disk_time, 1e-6)

    read_mbps = ((current_io.read_bytes - _last_disk_io.read_bytes) / elapsed) / (1024 ** 2)
    write_mbps = ((current_io.write_bytes - _last_disk_io.write_bytes) / elapsed) / (1024 ** 2)

    _last_disk_io = current_io
    _last_disk_time = now

    disk_usage = psutil.disk_usage("/")

    return {
        "read_mbps": round(max(read_mbps, 0), 2),
        "write_mbps": round(max(write_mbps, 0), 2),
        "total_gb": round(disk_usage.total / (1024 ** 3), 2),
        "used_gb": round(disk_usage.used / (1024 ** 3), 2),
        "percent": disk_usage.percent,
    }


def _classify_process(name: str):
    name_lower = name.lower()
    for keyword, label in HEAVY_PROCESS_KEYWORDS.items():
        if keyword in name_lower:
            return label
    return None


def _refresh_process_cache():
    """Menyinkronkan cache dengan proses yang benar-benar hidup saat ini:
    menambahkan proses baru (dengan baseline cpu_percent) dan membuang proses
    yang sudah berakhir, tanpa mengganti objek Process yang masih hidup."""
    current_pids = set(psutil.pids())
    cached_pids = set(_process_cache.keys())

    for pid in cached_pids - current_pids:
        _process_cache.pop(pid, None)

    for pid in current_pids - cached_pids:
        try:
            p = psutil.Process(pid)
            p.cpu_percent(None)  # baseline pertama untuk proses baru ini
            _process_cache[pid] = p
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass


def _get_process_metrics(top_n=8):
    _refresh_process_cache()

    procs = []
    for pid, p in list(_process_cache.items()):
        try:
            # cpu_percent(None) di sini menghitung delta sejak PANGGILAN TERAKHIR
            # pada objek Process persisten ini (kurang lebih 1 detik yang lalu,
            # mengikuti interval broadcaster) -- bukan lagi selalu 0.0.
            cpu = p.cpu_percent(None)
            mem = p.memory_percent()
            name = p.name()
            if cpu > 0 or mem > 0.05:
                category = _classify_process(name or "")
                procs.append({
                    "pid": pid,
                    "name": name,
                    "cpu_percent": round(cpu, 1),
                    "memory_percent": round(mem, 2),
                    "category": category,
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            _process_cache.pop(pid, None)
            continue

    procs.sort(key=lambda x: x["cpu_percent"], reverse=True)
    top_processes = procs[:top_n]

    heavy_processes = [p for p in procs if p["category"]]
    heavy_processes.sort(key=lambda x: x["cpu_percent"], reverse=True)

    return {
        "top_processes": top_processes,
        "heavy_processes": heavy_processes[:top_n],
        "total_process_count": len(procs),
    }


def collect_snapshot():
    """Mengumpulkan satu snapshot lengkap seluruh metrik dan memperbarui riwayat tren."""
    cpu = _get_cpu_metrics()
    gpu = _get_gpu_metrics()
    ram = _get_ram_metrics()
    disk = _get_disk_metrics()
    proc = _get_process_metrics()

    ts = datetime.now().strftime("%H:%M:%S")
    history["cpu_percent"].append(cpu["overall_percent"])
    history["ram_percent"].append(ram["percent"])
    history["disk_read_mbps"].append(disk["read_mbps"])
    history["disk_write_mbps"].append(disk["write_mbps"])
    history["timestamps"].append(ts)

    return {
        "timestamp": ts,
        "cpu": cpu,
        "gpu": gpu,
        "ram": ram,
        "disk": disk,
        "process": proc,
        "history": {k: list(v) for k, v in history.items()},
    }
