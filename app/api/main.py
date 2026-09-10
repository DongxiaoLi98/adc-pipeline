"""ADC Pipeline API — thin service layer over the Step 1–6 data layer.

Run locally:
    export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres
    uvicorn app.api.main:app --reload
Then submit a run:
    curl -XPOST localhost:8000/v1/runs -H 'Authorization: Bearer dev-submitter' \\
         -H 'Idempotency-Key: k1' -H 'content-type: application/json' -d '{}'
"""
from __future__ import annotations

from fastapi import FastAPI

from .v1 import health, runs

app = FastAPI(title="ADC Pipeline API", version="0.1.0")
app.include_router(health.router)
app.include_router(runs.router)
