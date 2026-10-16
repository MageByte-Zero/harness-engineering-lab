"""Local teaching service: fixed demo-project scope, not production authentication."""

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException

app = FastAPI()
PROJECT_ID = "demo-project"
DELIVERIES = json.loads((Path(__file__).resolve().parents[2] / "data/taskhub/deliveries.json").read_text())


@app.get("/deliveries/{delivery_id}")
def get_delivery(delivery_id: str):
    for delivery in DELIVERIES:
        if delivery["delivery_id"] == delivery_id and delivery["project_id"] == PROJECT_ID:
            return delivery
    raise HTTPException(status_code=404, detail="delivery_not_found")


SERVICES = json.loads((Path(__file__).resolve().parents[2] / "data/taskhub/services.json").read_text())


@app.get("/services/{service_id}/status")
def get_service_status(service_id: str):
    for service in SERVICES:
        if service["service_id"] == service_id:
            return service
    raise HTTPException(status_code=404, detail="service_not_found")
