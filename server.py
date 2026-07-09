"""
CoreSentinel - Real-time System Resource Monitor
==================================================
Entry point aplikasi. Menjalankan server FastAPI yang:
1. Menyajikan dashboard web statis (HTML/CSS/JS).
2. Membuka koneksi WebSocket ("/ws/metrics") yang mengirim snapshot metrik
   sistem setiap 1 detik ke seluruh klien yang terhubung.

Jalankan dengan:
    uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import metrics
import recommender

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("coresentinel")

app = FastAPI(title="CoreSentinel", description="Real-time System Resource Monitor")

SAMPLE_INTERVAL_SECONDS = 1.0


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Klien terhubung. Total koneksi aktif: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"Klien terputus. Total koneksi aktif: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.append(connection)
        for dc in dead_connections:
            self.disconnect(dc)


manager = ConnectionManager()


@app.websocket("/ws/metrics")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Klien tidak wajib mengirim apapun; loop utama broadcaster yang mendorong data.
            await asyncio.sleep(3600)
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def metrics_broadcaster():
    """Task background yang berjalan terus-menerus mengumpulkan & menyiarkan metrik."""
    while True:
        try:
            snapshot = metrics.collect_snapshot()
            snapshot["recommendations"] = recommender.generate_recommendations(snapshot)
            if manager.active_connections:
                await manager.broadcast(snapshot)
        except Exception as e:
            logger.exception(f"Gagal mengumpulkan/menyiarkan metrik: {e}")
        await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(metrics_broadcaster())
    logger.info("CoreSentinel backend aktif. Broadcaster metrik dimulai.")


@app.get("/")
async def serve_dashboard():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
