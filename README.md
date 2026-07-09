# CoreSentinel
### Real-time System Resource & Workload Monitoring Tool

CoreSentinel adalah aplikasi monitoring berbasis web yang memantau penggunaan
sumber daya komputer (CPU, GPU, RAM, Disk I/O, dan Proses) secara **real-time**,
dirancang untuk menganalisis dampak beban kerja komputasi — termasuk skenario
penggunaan Browser (termasuk akses Web AI), Zoom, dan YouTube secara bersamaan.

---

## 1. Arsitektur

```
┌─────────────────────┐        WebSocket (1x/detik)        ┌──────────────────────┐
│   BACKEND (Python)   │ ───────────────────────────────▶  │  FRONTEND (Browser)  │
│                      │                                    │                       │
│  metrics.py          │  Snapshot JSON:                    │  index.html           │
│   - psutil (CPU/RAM/ │  { cpu, gpu, ram, disk,             │  style.css            │
│     Disk/Proses)     │    process, history,               │  app.js               │
│   - GPUtil (GPU)     │    recommendations }               │   - Chart.js (tren)   │
│                      │                                    │   - Live core-grid    │
│  recommender.py      │                                    │   - Tabel proses      │
│   - Rule-based       │                                    │   - Panel rekomendasi │
│     analysis engine  │                                    │                       │
│                      │                                    │                       │
│  server.py           │                                    │                       │
│   - FastAPI          │                                    │                       │
│   - WebSocket        │                                    │                       │
│     broadcaster      │                                    │                       │
└─────────────────────┘                                    └──────────────────────┘
```

**Alur kerja:**
1. `server.py` menjalankan *background task* asinkron yang memanggil
   `metrics.collect_snapshot()` setiap 1 detik.
2. Snapshot dianalisis oleh `recommender.generate_recommendations()` untuk
   menghasilkan rekomendasi optimasi.
3. Snapshot lengkap disiarkan (`broadcast`) ke seluruh klien yang terhubung
   melalui WebSocket `/ws/metrics`.
4. Dashboard di browser menerima data dan memperbarui UI secara langsung
   tanpa perlu refresh (live update).

---

## 2. Pemenuhan Kebutuhan Fungsional

| # | Kebutuhan | Implementasi |
|---|-----------|---------------|
| a | CPU & GPU: beban, suhu, clock | `metrics._get_cpu_metrics()` (psutil: `cpu_percent(percpu=True)`, `cpu_freq()`, `sensors_temperatures()`); `metrics._get_gpu_metrics()` mencoba GPU NVIDIA lebih dulu (GPUtil/nvidia-smi: load, suhu, clock, VRAM lengkap), lalu fallback ke deteksi generik Intel/AMD di Windows (`gpu_windows.py` via WMI Performance Counters: load & VRAM, tanpa suhu/clock) |
| b | RAM: terpakai vs tersedia + tren | `metrics._get_ram_metrics()` + buffer riwayat `history["ram_percent"]` (120 sampel/2 menit), divisualisasikan via Chart.js |
| c | Disk I/O: kecepatan baca/tulis | `metrics._get_disk_metrics()` menghitung delta byte terbaca/tertulis dibagi waktu antar-sampel (MB/s real-time) |
| d | Proses & dampak multi-core | `metrics._get_process_metrics()` me-ranking proses by CPU%, mendeteksi kategori Browser/Zoom/YouTube via `HEAVY_PROCESS_KEYWORDS`, dan panel CPU menampilkan **distribusi beban per-core** untuk mengamati ketimpangan akibat proses single-thread dominan |
| e | Rekomendasi (AI-assisted) | `recommender.py`: *expert system* rule-based yang menganalisis level & tren metrik, menghasilkan rekomendasi berjenjang (baik/info/peringatan/kritis) — dapat dikembangkan lebih lanjut dengan memanggil Anthropic API untuk analisis bahasa alami (lihat bagian 5) |

---

## 3. Cara Menjalankan

### Prasyarat
- Python 3.10+
- (Opsional) GPU NVIDIA dengan driver terpasang untuk metrik GPU penuh — di
  luar itu, panel GPU akan menampilkan pesan bahwa GPU tidak terdeteksi
  (aplikasi tetap berjalan normal).

### Langkah instalasi

```bash
# 1. Ekstrak/clone folder coresentinel, lalu masuk ke direktori
cd coresentinel

# 2. (Disarankan) buat virtual environment
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 3. Install dependency
pip install -r requirements.txt

# 4. Jalankan server
uvicorn server:app --host 0.0.0.0 --port 8000
```

Buka browser ke **http://localhost:8000** — dashboard akan langsung
menampilkan metrik real-time.

### Menguji beban kerja (sesuai skenario tugas)
Untuk mengamati dampaknya di dashboard, jalankan beberapa aplikasi berat
secara bersamaan sambil memperhatikan panel **CPU** (distribusi per-core),
**Proses** (kategori Browser/Zoom/YouTube), dan **Rekomendasi**:
- Buka beberapa tab Browser, termasuk satu tab yang mengakses layanan Web AI
  (mis. chatbot berbasis web).
- Jalankan panggilan Zoom.
- Putar video YouTube.

---

## 4. Struktur Berkas

```
coresentinel/
├── server.py          # Entry point FastAPI + WebSocket broadcaster
├── metrics.py          # Modul pengumpulan metrik sistem (CPU/GPU/RAM/Disk/Proses)
├── gpu_windows.py       # Fallback deteksi GPU generik Intel/AMD di Windows (via WMI)
├── recommender.py       # Rule-based recommendation engine
├── requirements.txt     # Dependency Python
├── static/
│   ├── index.html       # Struktur dashboard
│   ├── style.css        # Tema visual (dark control-room/telemetry)
│   └── app.js            # Client WebSocket + rendering Chart.js
└── README.md
```

### Dukungan GPU: NVIDIA vs Intel/AMD

`metrics._get_gpu_metrics()` mencoba dua jalur secara berurutan:

1. **NVIDIA** (via `GPUtil`/`nvidia-smi`) — jalur utama, data paling lengkap:
   beban, suhu, clock, dan VRAM akurat.
2. **Fallback generik** (`gpu_windows.py`, khusus Windows) — memakai WMI
   Performance Counters (`Win32_PerfFormattedData_GPUPerformanceCounters_*`),
   API level-OS yang sama dipakai tab "GPU" di Task Manager. Jalur ini bekerja
   untuk **GPU Intel integrated maupun AMD** tanpa perlu tool vendor khusus,
   karena Windows sendiri yang mengekspos counter-nya — bukan driver vendor.

**Keterbatasan jalur fallback ini yang perlu diketahui:**
- **Suhu GPU tidak tersedia** — Windows tidak mengekspos sensor suhu GPU
  secara vendor-netral melalui WMI standar.
- **Clock speed (MHz) tidak tersedia** melalui performance counter ini.
- Hanya aktif di Windows; di Linux/macOS modul ini otomatis nonaktif tanpa
  menyebabkan error (fallback pesan "GPU tidak terdeteksi" tetap ditampilkan
  dengan wajar).
- Membutuhkan paket `WMI` dan `pywin32` (sudah ditandai `sys_platform ==
  "win32"` di `requirements.txt`, jadi otomatis hanya terpasang di Windows).

Jika suhu/clock akurat untuk GPU Intel/AMD dibutuhkan untuk analisis lebih
dalam, pengembangan lanjutan dapat mengintegrasikan library close-to-hardware
seperti LibreHardwareMonitor (via jembatan WMI-nya sendiri) sebagai jalur
ketiga.

---

## 5. Pengembangan Lanjutan (Opsional)

Fitur rekomendasi saat ini bersifat **rule-based** agar cepat, deterministik,
dan tidak bergantung pada koneksi internet/API eksternal — cocok untuk
monitoring real-time berkelanjutan. Untuk pengembangan lanjutan, `recommender.py`
dapat diperluas agar mengirim ringkasan snapshot ke LLM (mis. Anthropic API)
guna menghasilkan narasi rekomendasi yang lebih kontekstual dan adaptif,
misalnya dengan menambahkan fungsi `generate_ai_narrative(snapshot)` yang
memanggil endpoint `/v1/messages` menggunakan hasil analisis rule-based
sebagai konteks prompt.

## 6. Keterbatasan yang Diketahui

- Sensor suhu CPU (`psutil.sensors_temperatures()`) hanya tersedia penuh di
  Linux; pada Windows/macOS dapat mengembalikan `null` — dashboard menangani
  ini dengan menampilkan "N/A".
- Deteksi GPU: NVIDIA didukung penuh (load, suhu, clock, VRAM) via
  `nvidia-smi`. Intel integrated & AMD didukung di Windows lewat fallback WMI
  generik (`gpu_windows.py`) — namun suhu & clock speed untuk GPU non-NVIDIA
  tidak tersedia lewat jalur ini, hanya load & VRAM.
- Deteksi "YouTube" dilakukan melalui proses browser terkait (bukan
  pembacaan judul tab), karena psutil tidak memiliki akses ke level tab
  browser individual.
