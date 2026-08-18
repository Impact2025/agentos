"""Test-harness: mount de leads-router (prospecting) op :1253 om de nieuwe
Lead Machine-endpoints end-to-end te valideren ZONDER de live :1250 te killen.

Draait init_db() zodat de quality-kolommen + lead_opt_outs-tabel gemigreerd
worden tegen dezelfde live data/agentos.db. Start en curl de endpoints.
"""
import sys
sys.path.insert(0, r"D:\APPS\agentos")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.domains.prospecting import router as leads_router
from backend.shared.database import init_db

# Migreer de schema-uitbreidingen (quality_score/label/reason + lead_opt_outs).
init_db()

app = FastAPI(title="Leads test-server (prospecting only)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.include_router(leads_router.router)

if __name__ == "__main__":
    import uvicorn
    print("Starting Leads test-server on :1253 (prospecting router only)")
    uvicorn.run(app, host="127.0.0.1", port=1253, log_level="info")
