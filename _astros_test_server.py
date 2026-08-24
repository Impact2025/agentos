"""Test-harness: mount alleen de radar-router op :1253 om de nieuwe
Astros-endpoints end-to-end te valideren ZONDER de volledige backend.main
te laden (die een ongerelateerde knowledge_forge-import heeft die de live
server wel overleeft maar in deze isolated test faalt).

Dit script raakt de live :1250 server NIET. Start het en curl de endpoints.
"""
import sys
sys.path.insert(0, r"D:\APPS\impactos")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.domains.radar import router as radar_router

app = FastAPI(title="Astros test-server (radar only)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.include_router(radar_router.router)

if __name__ == "__main__":
    import uvicorn
    print("Starting Astros test-server on :1253 (radar router only)")
    uvicorn.run(app, host="127.0.0.1", port=1253, log_level="info")
