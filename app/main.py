"""Recon microservice. Compare loop only — repairs happen in RMS after confirm."""
from __future__ import annotations

import logging
import threading

import uvicorn
from fastapi import FastAPI

from app.config import settings
from app.loop import loop_forever

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Recon Service", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": "recon"}


@app.on_event("startup")
def startup():
    thread = threading.Thread(target=loop_forever, name="recon-loop", daemon=True)
    thread.start()
    logger.info("recon_service listening on %s", settings.RECON_SERVICE_PORT)


def main():
    uvicorn.run(app, host="0.0.0.0", port=settings.RECON_SERVICE_PORT)


if __name__ == "__main__":
    main()
