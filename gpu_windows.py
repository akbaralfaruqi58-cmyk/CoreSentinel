"""
CoreSentinel - GPU Generik Windows (fallback untuk Intel/AMD)
===============================================================
Modul ini mendeteksi metrik GPU secara VENDOR-NETRAL di Windows menggunakan
WMI Performance Counters (Win32_PerfFormattedData_GPUPerformanceCounters_*),
yaitu API level-OS yang sama dipakai oleh tab "GPU" di Task Manager sejak
Windows 10 (build 1803+). Karena ini API bawaan Windows -- bukan API vendor
seperti nvidia-smi -- modul ini bekerja untuk GPU Intel integrated, AMD
(integrated/dedicated), maupun NVIDIA tanpa driver/tool tambahan khusus vendor.

Dipakai sebagai fallback di metrics.py: NVIDIA via GPUtil/nvidia-smi dicoba
lebih dahulu (datanya lebih detail: suhu & clock akurat); jika tidak ada GPU
NVIDIA, modul ini dicoba sebagai jalur kedua.

KETERBATASAN YANG DIKETAHUI (harap disampaikan apa adanya ke pengguna):
- Suhu GPU TIDAK tersedia lewat WMI generik ini. Windows tidak mengekspos
  sensor suhu GPU secara vendor-netral; nilai akan selalu None/"N/A".
- Clock speed (MHz) TIDAK tersedia lewat performance counter ini.
- Hanya berjalan di Windows (modul otomatis nonaktif di Linux/macOS, tanpa
  menyebabkan error saat diimpor).
- Membutuhkan paket `WMI` dan `pywin32` (lihat requirements.txt, hanya
  terpasang di Windows karena environment marker `sys_platform == "win32"`).
"""

import platform
import logging

logger = logging.getLogger("coresentinel.gpu_windows")

_WMI_AVAILABLE = False
_wmi_client = None

if platform.system() == "Windows":
    try:
        import wmi  # paket pip: WMI
        _wmi_client = wmi.WMI(namespace="root\\CIMV2")
        _WMI_AVAILABLE = True
    except Exception as e:
        logger.warning(f"Paket 'wmi' tidak tersedia atau gagal diinisialisasi: {e}")
        _WMI_AVAILABLE = False


def is_supported():
    """True hanya jika berjalan di Windows DAN paket wmi berhasil diinisialisasi."""
    return _WMI_AVAILABLE


def _get_adapters():
    """Nama & total VRAM tiap adapter grafis, dari Win32_VideoController
    (kelas WMI standar yang selalu ada untuk semua vendor GPU)."""
    adapters = []
    try:
        for gpu in _wmi_client.Win32_VideoController():
            name = gpu.Name or "GPU Tidak Dikenal"
            adapter_ram_raw = getattr(gpu, "AdapterRAM", None)
            try:
                adapter_ram = int(adapter_ram_raw) if adapter_ram_raw not in (None, "") else None
            except (TypeError, ValueError):
                adapter_ram = None
            # AdapterRAM di banyak driver modern sering ter-cap di 4GB (bug WMI lama);
            # kita tetap tampilkan apa adanya dan biarkan pengguna mengetahuinya.
            adapters.append({
                "name": name,
                "adapter_ram_mb": round(adapter_ram / (1024 ** 2), 1) if adapter_ram else None,
            })
    except Exception as e:
        logger.warning(f"Gagal membaca Win32_VideoController: {e}")
    return adapters


def get_gpu_metrics():
    """
    Mengumpulkan metrik GPU generik via WMI Performance Counters.
    Mengembalikan struktur dict yang KOMPATIBEL dengan format
    metrics._get_gpu_metrics() versi NVIDIA, agar bisa dipakai langsung
    sebagai pengganti di server tanpa mengubah kontrak data ke frontend.
    """
    if not _WMI_AVAILABLE:
        return {
            "available": False,
            "reason": "Modul WMI tidak tersedia (bukan Windows, atau paket 'wmi'/'pywin32' belum terpasang).",
            "gpus": [],
        }

    try:
        adapters = _get_adapters()

        # Setiap adapter GPU di Windows punya banyak "engine" (3D, Copy,
        # VideoDecode, VideoEncode, dst), masing-masing proses punya baris sendiri.
        # Task Manager menghitung persentase "GPU" dengan mengambil nilai TERTINGGI
        # di antara semua engine 3D pada saat itu -- kita meniru logika yang sama.
        #
        # CATATAN PENTING: paket 'wmi' mengembalikan properti numerik WMI bertipe
        # uint64 (mis. UtilizationPercentage, DedicatedUsage, SharedUsage) sebagai
        # STRING, bukan int/float Python -- ini perilaku standar library 'wmi'
        # untuk tipe 64-bit. Maka setiap nilai WAJIB di-cast eksplisit sebelum
        # dipakai dalam operasi numerik, atau akan muncul TypeError.
        max_util = 0.0
        try:
            for row in _wmi_client.query(
                "SELECT UtilizationPercentage FROM Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine"
            ):
                try:
                    util = float(row.UtilizationPercentage or 0)
                except (TypeError, ValueError):
                    util = 0.0
                if util > max_util:
                    max_util = util
        except Exception as e:
            logger.warning(f"Query GPUEngine gagal (mungkin OS lebih lama dari Windows 10 1803): {e}")

        # Total memori (dedicated + shared) yang sedang dipakai lintas seluruh proses
        mem_used_bytes = 0
        try:
            for row in _wmi_client.query(
                "SELECT DedicatedUsage, SharedUsage FROM Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory"
            ):
                try:
                    dedicated = int(row.DedicatedUsage or 0)
                except (TypeError, ValueError):
                    dedicated = 0
                try:
                    shared = int(row.SharedUsage or 0)
                except (TypeError, ValueError):
                    shared = 0
                mem_used_bytes += dedicated + shared
        except Exception as e:
            logger.warning(f"Query GPUAdapterMemory gagal: {e}")

        primary = adapters[0] if adapters else {"name": "GPU Terintegrasi", "adapter_ram_mb": None}
        mem_used_mb = round(mem_used_bytes / (1024 ** 2), 1) if mem_used_bytes else None

        gpu_entry = {
            "name": primary["name"],
            "load_percent": round(max_util, 1),
            "memory_used_mb": mem_used_mb,
            "memory_total_mb": primary["adapter_ram_mb"],
            "memory_percent": (
                round((mem_used_mb / primary["adapter_ram_mb"]) * 100, 1)
                if mem_used_mb and primary["adapter_ram_mb"]
                else None
            ),
            # Tidak tersedia secara generik di Windows -- lihat docstring modul.
            "temperature_c": None,
        }

        return {"available": True, "gpus": [gpu_entry], "source": "windows_wmi_generic"}

    except Exception as e:
        logger.warning(f"Gagal membaca metrik GPU via WMI: {e}")
        return {"available": False, "reason": f"Gagal membaca GPU via WMI: {e}", "gpus": []}
