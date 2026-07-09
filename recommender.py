"""
CoreSentinel - Recommendation Engine
=====================================
Modul ini menganalisis snapshot metrik terkini beserta tren historisnya, lalu
menghasilkan rekomendasi optimasi berbasis aturan (rule-based expert system) —
sebuah pendekatan "AI ringan" yang meniru penalaran seorang teknisi sistem
berpengalaman. Pendekatan rule-based dipilih agar rekomendasi bersifat
deterministik, dapat dijelaskan (explainable), dan tidak memerlukan
koneksi API eksternal untuk beroperasi (cocok untuk monitoring real-time).

Setiap rekomendasi memiliki: level urgensi, kategori sumber daya, dan aksi
yang disarankan.
"""

import statistics


def _trend(values, window=10):
    """Menghitung arah tren sederhana: naik / turun / stabil dari N sampel terakhir."""
    if len(values) < window:
        window = len(values)
    if window < 2:
        return "stabil"
    recent = list(values)[-window:]
    first_half = statistics.mean(recent[: window // 2]) if window // 2 > 0 else recent[0]
    second_half = statistics.mean(recent[window // 2:])
    diff = second_half - first_half
    if diff > 5:
        return "naik"
    elif diff < -5:
        return "turun"
    return "stabil"


def generate_recommendations(snapshot):
    recs = []
    cpu = snapshot["cpu"]
    gpu = snapshot["gpu"]
    ram = snapshot["ram"]
    disk = snapshot["disk"]
    proc = snapshot["process"]
    history = snapshot["history"]

    cpu_trend = _trend(history["cpu_percent"])
    ram_trend = _trend(history["ram_percent"])

    # --- Analisis CPU ---
    if cpu["overall_percent"] >= 85:
        recs.append({
            "level": "kritis",
            "kategori": "CPU",
            "pesan": f"Beban CPU sangat tinggi ({cpu['overall_percent']}%) dan tren {cpu_trend}. "
                     f"Pertimbangkan menutup proses berat yang tidak sedang digunakan aktif.",
        })
    elif cpu["overall_percent"] >= 60:
        recs.append({
            "level": "peringatan",
            "kategori": "CPU",
            "pesan": f"Beban CPU cukup tinggi ({cpu['overall_percent']}%) dengan tren {cpu_trend}. "
                     f"Perhatikan aplikasi yang berjalan bersamaan.",
        })

    if cpu.get("temperature_c") and cpu["temperature_c"] >= 85:
        recs.append({
            "level": "kritis",
            "kategori": "CPU",
            "pesan": f"Suhu CPU {cpu['temperature_c']}°C mendekati batas aman. "
                     f"Periksa sirkulasi udara/pendingin perangkat.",
        })

    # Ketimpangan beban antar-core (indikasi proses single-threaded menghambat multitasking)
    per_core = cpu.get("per_core_percent") or []
    if len(per_core) >= 2:
        max_core = max(per_core)
        min_core = min(per_core)
        if max_core - min_core >= 50 and max_core >= 70:
            recs.append({
                "level": "info",
                "kategori": "Multi-core",
                "pesan": f"Distribusi beban antar-core tidak merata (core tertinggi {max_core}% vs "
                         f"terendah {min_core}%). Ini mengindikasikan sebuah proses membebani satu core "
                         f"secara dominan (umum terjadi pada tab browser aktif atau rendering video).",
            })

    # --- Analisis GPU ---
    if gpu.get("available") and gpu["gpus"]:
        for g in gpu["gpus"]:
            if g["load_percent"] >= 90:
                recs.append({
                    "level": "peringatan",
                    "kategori": "GPU",
                    "pesan": f"GPU {g['name']} bekerja pada {g['load_percent']}% beban. "
                             f"Jika tidak sedang rendering/gaming, kemungkinan ada tab browser/AI "
                             f"yang memanfaatkan akselerasi GPU secara intensif.",
                })
            if g.get("temperature_c") and g["temperature_c"] >= 80:
                recs.append({
                    "level": "kritis",
                    "kategori": "GPU",
                    "pesan": f"Suhu GPU {g['temperature_c']}°C tinggi. Periksa ventilasi dan kebersihan kipas.",
                })

    # --- Analisis RAM ---
    if ram["percent"] >= 90:
        recs.append({
            "level": "kritis",
            "kategori": "RAM",
            "pesan": f"Penggunaan RAM {ram['percent']}% ({ram['used_gb']} GB dari {ram['total_gb']} GB). "
                     f"Risiko swapping ke disk yang memperlambat sistem secara signifikan.",
        })
    elif ram["percent"] >= 75:
        recs.append({
            "level": "peringatan",
            "kategori": "RAM",
            "pesan": f"Penggunaan RAM {ram['percent']}%, tren {ram_trend}. "
                     f"Tutup tab/aplikasi yang tidak terpakai untuk mencegah kehabisan memori.",
        })

    # --- Analisis Disk I/O ---
    if disk["read_mbps"] + disk["write_mbps"] >= 200:
        recs.append({
            "level": "info",
            "kategori": "Disk I/O",
            "pesan": f"Aktivitas disk tinggi (baca {disk['read_mbps']} MB/s, tulis {disk['write_mbps']} MB/s). "
                     f"Umumnya wajar saat membuka aplikasi besar atau caching video, tetapi jika berkelanjutan "
                     f"periksa proses penyebabnya.",
        })

    # --- Analisis Proses Berat (Browser/Zoom/YouTube/Web AI) ---
    heavy = proc.get("heavy_processes", [])
    if heavy:
        top = heavy[0]
        total_heavy_cpu = round(sum(p["cpu_percent"] for p in heavy), 1)
        recs.append({
            "level": "info",
            "kategori": "Proses",
            "pesan": f"Terdeteksi {len(heavy)} proses aplikasi berat sedang berjalan (kontribusi CPU gabungan "
                     f"~{total_heavy_cpu}%), didominasi oleh {top['category']} (PID {top['pid']}, "
                     f"{top['cpu_percent']}% CPU). Ini konsisten dengan skenario penggunaan Browser/Zoom/YouTube "
                     f"secara bersamaan.",
        })

    if not recs:
        recs.append({
            "level": "baik",
            "kategori": "Umum",
            "pesan": "Seluruh sumber daya sistem dalam kondisi normal dan stabil.",
        })

    return recs
