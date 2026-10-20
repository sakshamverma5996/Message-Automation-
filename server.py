import csv
import json
import os
import sys
import webbrowser
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import uvicorn

from engine import WhatsAppCampaignEngine

TEMPLATE_FILE = "message_template.txt"
CONTACTS_FILE = "contacts.csv"
CONFIG_FILE = "config.json"
RESULTS_FILE = "results.csv"

app = FastAPI(title="WhatsApp Bulk Messenger Hub")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = WhatsAppCampaignEngine()

# Helpers
def read_template():
    if os.path.exists(TEMPLATE_FILE):
        with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return "Hello {name},\n\nWe have an update regarding your note: {note}.\n\nBest regards,\nMy Team"

def read_contacts():
    contacts = []
    if os.path.exists(CONTACTS_FILE):
        try:
            with open(CONTACTS_FILE, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    contacts.append(row)
        except Exception:
            pass
    return contacts

def read_config():
    default_config = {
        "min_delay_seconds": 10,
        "max_delay_seconds": 22,
        "headless": False,
        "browser_channel": "msedge",
        "default_country_code": "+91",
        "user_data_dir": "./whatsapp_profile",
        "batch_size": 25,
        "batch_pause_seconds": 120
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                default_config.update(json.load(f))
        except Exception:
            pass
    return default_config

# Models
class MessagePayload(BaseModel):
    template: str

class ContactsPayload(BaseModel):
    contacts: List[Dict[str, Any]]

class CampaignStartPayload(BaseModel):
    message: str
    contacts: List[Dict[str, Any]]
    config: Optional[Dict[str, Any]] = None

# Routes
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Error: static/index.html not found!</h1>"

@app.get("/api/data")
async def get_initial_data():
    return {
        "template": read_template(),
        "contacts": read_contacts(),
        "config": read_config()
    }

@app.post("/api/message/save")
async def save_message(payload: MessagePayload):
    with open(TEMPLATE_FILE, "w", encoding="utf-8") as f:
        f.write(payload.template)
    return {"success": True, "message": "Template saved successfully"}

@app.post("/api/contacts/save")
async def save_contacts(payload: ContactsPayload):
    if not payload.contacts:
        return {"success": True, "count": 0}

    fieldnames = list(payload.contacts[0].keys())
    for req in ["phone", "name", "note"]:
        if req not in fieldnames:
            fieldnames.append(req)

    with open(CONTACTS_FILE, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(payload.contacts)
    return {"success": True, "count": len(payload.contacts)}

@app.post("/api/campaign/start")
async def start_campaign(payload: CampaignStartPayload):
    cfg = payload.config or read_config()
    # Save active template & contacts
    with open(TEMPLATE_FILE, "w", encoding="utf-8") as f:
        f.write(payload.message)

    success, msg = engine.start_campaign(
        contacts=payload.contacts,
        message_template=payload.message,
        config=cfg
    )
    return {"success": success, "message": msg}

@app.post("/api/campaign/stop")
async def stop_campaign():
    engine.stop()
    return {"success": True, "message": "Stop signal sent"}

@app.get("/api/campaign/state")
async def get_campaign_state():
    return engine.get_status()

@app.get("/api/results/download")
async def download_results():
    if not os.path.exists(RESULTS_FILE):
        raise HTTPException(status_code=404, detail="No campaign results file found yet.")
    return FileResponse(
        path=RESULTS_FILE,
        filename="whatsapp_campaign_report.csv",
        media_type="text/csv"
    )

if __name__ == "__main__":
    port = 8000
    print(f"\n=======================================================")
    print(f"[*] WhatsApp Campaign Dashboard running at:")
    print(f"    http://localhost:{port}")
    print(f"=======================================================\n")
    try:
        webbrowser.open(f"http://localhost:{port}")
    except Exception:
        pass
    uvicorn.run("server:app", host="127.0.0.1", port=port, reload=False)
