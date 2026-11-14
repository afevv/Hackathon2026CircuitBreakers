from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.engine import run_migration
from app.llm_worker import DICT_PATH, save_dictionary
from app.models import MigrationRequest
from app.parser import parse_legacy_xml

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
SAMPLES_DIR = DATA_DIR / "samples"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Project A.S.S.E.R.T.")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def load_sample_document() -> Any:
    text = (SAMPLES_DIR / "TEXTDATA.TDS").read_text(encoding="utf-8")
    image = (SAMPLES_DIR / "IMAGEDATA.IDS").read_text(encoding="utf-8")
    return parse_legacy_xml(text, image)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/dictionary")
async def dictionary() -> dict[str, Any]:
    if not DICT_PATH.exists():
        return {"rules": {}, "history": []}
    return json.loads(DICT_PATH.read_text(encoding="utf-8"))


@app.post("/api/reset-dictionary")
async def reset_dictionary() -> dict[str, Any]:
    initial = {
        "rules": {
            "text_supply_voltage": {
                "type": "text",
                "match": "Supply Voltage %4d V",
                "can_signal": "SUPPLY_VOLTAGE",
                "polarity": "NORMAL",
                "output": "<FC ID=\"FC_001\" Source=\"TEXTDATA.TDS\" Legacy=\"Supply Voltage %4d V\" Pattern=\"%1$d\" Signal=\"SUPPLY_VOLTAGE\" />"
            },
            "text_engine_speed": {
                "type": "text",
                "match": "Engine Speed %4d RPM",
                "can_signal": "ENGINE_SPEED",
                "polarity": "NORMAL",
                "output": "<FC ID=\"FC_002\" Source=\"TEXTDATA.TDS\" Legacy=\"Engine Speed %4d RPM\" Pattern=\"%1$d\" Signal=\"ENGINE_SPEED\" />"
            },
            "image_tortuga": {
                "type": "image",
                "match": "TORTUGA.BMP",
                "can_signal": "ICON_TORTUGA",
                "polarity": "NORMAL",
                "output": "<Image ID=\"IMG_001\" Source=\"IMAGEDATA.IDS\" File=\"TORTUGA.BMP\" />"
            }
        },
        "history": []
    }
    save_dictionary(initial)
    return initial


@app.post("/api/migrate")
async def migrate(payload: MigrationRequest | None = None) -> dict[str, Any]:
    payload = payload or MigrationRequest()
    document = load_sample_document()
    scenario = payload.scenario
    if scenario == "hazard":
        for item in document.text_entries:
            if item.legacy_format.startswith("Hydraulic Temp"):
                item.can_signal = "CAN_HYD_FAULT"
                item.polarity = "INVERTED"
    image_ids = {image.id for image in document.image_entries}
    unknown_ids = set(payload.aspect_ratio_overrides) - image_ids
    if unknown_ids:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown IDS image IDs in aspect ratio overrides: {', '.join(sorted(unknown_ids))}",
        )
    ratio_overrides = {
        image_id: (ratio.width, ratio.height)
        for image_id, ratio in payload.aspect_ratio_overrides.items()
    }
    try:
        result = run_migration(
            document,
            scenario=scenario,
            use_local_ollama=payload.use_local_ollama,
            aspect_ratio_overrides=ratio_overrides,
            screen_image_base64=payload.screen_image_base64,
            asset_images=payload.asset_images,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response = result.model_dump()
    response["input_document"] = document.model_dump()
    return response


@app.get("/api/health")
async def api_health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
