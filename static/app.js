/* CoreSentinel — Dashboard Client
   Menghubungkan ke WebSocket backend, merender metrik real-time ke UI,
   dan memperbarui grafik tren (Chart.js) untuk RAM & Disk I/O. */

const WS_URL = `ws://${window.location.host}/ws/metrics`;
let socket;
let reconnectTimer = null;

const connStatusEl = document.getElementById("conn-status");
const connLabelEl = document.getElementById("conn-label");
const clockEl = document.getElementById("live-clock");

function setConnected(isConnected) {
  connStatusEl.classList.toggle("online", isConnected);
  connLabelEl.textContent = isConnected ? "Terhubung" : "Menghubungkan…";
}

function connect() {
  socket = new WebSocket(WS_URL);

  socket.onopen = () => setConnected(true);
  socket.onclose = () => {
    setConnected(false);
    reconnectTimer = setTimeout(connect, 1500);
  };
  socket.onerror = () => socket.close();

  socket.onmessage = (event) => {
    const snapshot = JSON.parse(event.data);
    renderSnapshot(snapshot);
  };
}

connect();

// ---------- Live clock ----------
setInterval(() => {
  clockEl.textContent = new Date().toLocaleTimeString("id-ID");
}, 1000);

// ---------- Chart.js setup (defensif: jangan sampai kegagalan CDN mematikan seluruh dashboard) ----------
const chartDefaults = {
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: 300 },
  scales: {
    x: { display: false },
    y: { beginAtZero: true, grid: { color: "#1E2530" }, ticks: { color: "#6B7688", font: { size: 9 } } },
  },
  plugins: { legend: { display: false } },
  elements: { point: { radius: 0 }, line: { tension: 0.35, borderWidth: 2 } },
};

let ramChart = null;
let diskChart = null;

function initCharts() {
  if (typeof Chart === "undefined") {
    console.error("CoreSentinel: Chart.js gagal dimuat dari CDN — grafik tren dinonaktifkan, panel lain tetap berjalan normal.");
    return;
  }
  try {
    ramChart = new Chart(document.getElementById("ram-chart"), {
      type: "line",
      data: { labels: [], datasets: [{ data: [], borderColor: "#4CD3E0", backgroundColor: "rgba(76,211,224,0.1)", fill: true }] },
      options: { ...chartDefaults, scales: { ...chartDefaults.scales, y: { ...chartDefaults.scales.y, max: 100 } } },
    });

    diskChart = new Chart(document.getElementById("disk-chart"), {
      type: "line",
      data: {
        labels: [],
        datasets: [
          { data: [], borderColor: "#4CD3E0", backgroundColor: "rgba(76,211,224,0.08)", fill: true, label: "Baca" },
          { data: [], borderColor: "#F0A959", backgroundColor: "rgba(240,169,89,0.08)", fill: true, label: "Tulis" },
        ],
      },
      options: chartDefaults,
    });
  } catch (err) {
    console.error("CoreSentinel: gagal menginisialisasi Chart.js:", err);
    ramChart = null;
    diskChart = null;
  }
}

initCharts();

// ---------- Renderers ----------
// Setiap panel dirender dalam blok try/catch terpisah: jika satu panel gagal
// (mis. data tak terduga), panel lain tetap ter-update, tidak ikut "membeku".
function renderSnapshot(s) {
  safeRender("CPU", () => renderCpu(s.cpu));
  safeRender("GPU", () => renderGpu(s.gpu));
  safeRender("RAM", () => renderRam(s.ram, s.history));
  safeRender("Disk", () => renderDisk(s.disk, s.history));
  safeRender("Proses", () => renderProcess(s.process));
  safeRender("Rekomendasi", () => renderRecommendations(s.recommendations));
}

function safeRender(label, fn) {
  try {
    fn();
  } catch (err) {
    console.error(`CoreSentinel: gagal merender panel ${label}:`, err);
  }
}

function levelClass(percent, warnAt = 60, critAt = 85) {
  if (percent >= critAt) return "crit";
  if (percent >= warnAt) return "warn";
  return "";
}

function renderCpu(cpu) {
  document.getElementById("cpu-overall").textContent = cpu.overall_percent.toFixed(1);
  document.getElementById("cpu-clock").textContent = cpu.clock_current_mhz ? Math.round(cpu.clock_current_mhz) : "N/A";
  document.getElementById("cpu-temp").textContent = cpu.temperature_c !== null ? cpu.temperature_c.toFixed(0) : "N/A";

  const grid = document.getElementById("core-grid");
  const cores = cpu.per_core_percent || [];

  if (grid.children.length !== cores.length) {
    grid.innerHTML = cores.map((_, i) => `
      <div class="core-bar-wrap">
        <div class="core-bar-track"><div class="core-bar-fill" id="core-fill-${i}" style="height:0%"></div></div>
        <span class="core-label">C${i}</span>
      </div>`).join("");
  }

  cores.forEach((val, i) => {
    const fill = document.getElementById(`core-fill-${i}`);
    if (!fill) return;
    fill.style.height = `${Math.min(val, 100)}%`;
    fill.className = `core-bar-fill ${levelClass(val)}`;
  });
}

function renderGpu(gpu) {
  const container = document.getElementById("gpu-content");
  if (!gpu.available || !gpu.gpus.length) {
    container.innerHTML = `<p class="empty-state">${gpu.reason || "GPU tidak terdeteksi."}<br><br>Catatan: deteksi GPU saat ini mendukung GPU NVIDIA via nvidia-smi, serta deteksi generik Intel/AMD di Windows via WMI. Jika masih tidak terdeteksi, pastikan dependency 'WMI' dan 'pywin32' sudah terpasang (lihat requirements.txt).</p>`;
    return;
  }
  const isGeneric = gpu.source === "windows_wmi_generic";
  const genericNote = isGeneric
    ? `<p class="empty-state" style="margin-top:6px;">Sumber data: WMI generik Windows. Suhu &amp; clock speed tidak tersedia lewat jalur ini untuk GPU non-NVIDIA.</p>`
    : "";
  container.innerHTML = gpu.gpus.map(g => `
    <div class="gpu-card">
      <div class="gpu-card-name">${g.name}</div>
      <div class="gpu-metrics-row">
        <div class="readout">
          <span class="readout-val" style="color:${g.load_percent >= 85 ? '#F1605C' : '#4CD3E0'}">${g.load_percent}</span><span class="readout-unit">%</span>
          <span class="readout-label">Beban</span>
        </div>
        <div class="readout">
          <span class="readout-val">${g.memory_used_mb ?? 'N/A'}</span><span class="readout-unit">MB</span>
          <span class="readout-label">VRAM Terpakai</span>
        </div>
        <div class="readout">
          <span class="readout-val">${g.temperature_c ?? 'N/A'}</span><span class="readout-unit">°C</span>
          <span class="readout-label">Suhu</span>
        </div>
      </div>
    </div>
  `).join("") + genericNote;
}

function renderRam(ram, history) {
  document.getElementById("ram-used").textContent = ram.used_gb;
  document.getElementById("ram-avail").textContent = ram.available_gb;

  if (!ramChart) return;
  ramChart.data.labels = history.timestamps;
  ramChart.data.datasets[0].data = history.ram_percent;
  ramChart.update("none");
}

function renderDisk(disk, history) {
  document.getElementById("disk-read").textContent = disk.read_mbps;
  document.getElementById("disk-write").textContent = disk.write_mbps;

  if (!diskChart) return;
  diskChart.data.labels = history.timestamps;
  diskChart.data.datasets[0].data = history.disk_read_mbps;
  diskChart.data.datasets[1].data = history.disk_write_mbps;
  diskChart.update("none");
}

function renderProcess(proc) {
  document.getElementById("proc-count").textContent = `${proc.total_process_count} proses aktif`;
  const tbody = document.getElementById("proc-tbody");
  tbody.innerHTML = proc.top_processes.map(p => `
    <tr>
      <td>${p.pid}</td>
      <td>${p.name}</td>
      <td>${p.category ? `<span class="category-tag">${p.category}</span>` : "—"}</td>
      <td>${p.cpu_percent}</td>
      <td>${p.memory_percent}</td>
    </tr>
  `).join("");
}

function renderRecommendations(recos) {
  const list = document.getElementById("reco-list");
  list.innerHTML = recos.map(r => `
    <div class="reco-item ${r.level}">
      <span class="reco-tag">${r.level} · ${r.kategori}</span>
      <div>${r.pesan}</div>
    </div>
  `).join("");
}
