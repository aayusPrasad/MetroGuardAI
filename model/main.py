%%writefile main.py
"FastAPI entry points for MetroGuard's image and e-commerce analysis flows."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, HttpUrl

from compliance_engine import ComplianceEngine
from extraction import extract_entities
from preprocessing import preprocess_image
from report_generator import generate_inspection_certificate
from fastapi.responses import FileResponse


RULEBOOK_PATH = os.environ.get("LPMC_RULEBOOK_PATH", "lmpc_rules_2011.json")
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024

app = FastAPI(title="MetroGuard AI", version="0.1.0")


class EcommerceRequest(BaseModel):
    """Validated request body for a public product listing URL."""

    url: HttpUrl


class _ListingParser(HTMLParser):
    """Dependency-free HTML text/image collector for the prototype fetch path."""

    def __init__(self) -> None:
        super().__init__()
        self._hidden_depth = 0
        self.text: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._hidden_depth += 1
        if tag == "img":
            source = dict(attrs).get("src") or dict(attrs).get("data-src")
            if source:
                self.images.append(source)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth and data.strip():
            self.text.append(unescape(data.strip()))


class _TextReader:
    """Adapts listing text into EasyOCR-shaped detections for Stage 2 regexes."""

    def __init__(self, text: str) -> None:
        self.lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]

    def readtext(self, _image: Any, detail: int = 1) -> list[Any]:
        return [
            ([[0, index * 24], [1000, index * 24], [1000, index * 24 + 20], [0, index * 24 + 20]], line, 1.0)
            for index, line in enumerate(self.lines)
        ]


@lru_cache(maxsize=1)
def _engine() -> ComplianceEngine:
    try:
        return ComplianceEngine(RULEBOOK_PATH)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=500, detail=f"Rulebook could not be loaded: {exc}") from exc


def _decode_image(payload: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.size == 0:
        raise HTTPException(status_code=422, detail="The uploaded file is not a decodable image.")
    return image


def _download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "MetroGuard-AI/0.1 prototype"})
    try:
        with urlopen(request, timeout=10) as response:
            payload = response.read(MAX_DOWNLOAD_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch listing resource: {exc}") from exc
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Listing resource exceeds the 10 MB prototype limit.")
    return payload


def _listing_content(url: str) -> tuple[str, list[str]]:
    parser = _ListingParser()
    try:
        parser.feed(_download(url).decode("utf-8", errors="replace"))
        parser.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not parse listing HTML: {exc}") from exc
    return "\n".join(parser.text), [urljoin(url, source) for source in parser.images[:3]]


def _image_extraction(image_urls: list[str]) -> dict[str, Any] | None:
    """Try up to three listing images only when listing text has no OCR candidates."""
    for image_url in image_urls:
        try:
            extracted = extract_entities(_decode_image(_download(image_url)), None)
        except HTTPException:
            continue
        if extracted["detections"]:
            return extracted
    return None


@app.post("/analyze/image")
async def analyze_image(
    file: UploadFile = File(...),
    manual_pack_width_cm: float | None = Form(None),
    manual_pack_height_cm: float | None = Form(None),
) -> dict[str, Any]:
    """Run the full controlled-capture pipeline, including physical font checks."""
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=422, detail="The uploaded image is empty.")
    image = _decode_image(payload)
    dimensions = None
    if manual_pack_width_cm is not None or manual_pack_height_cm is not None:
        dimensions = (manual_pack_width_cm, manual_pack_height_cm)
    try:
        stage_one = preprocess_image(image, pack_dimensions_cm=dimensions)
        stage_two = extract_entities(stage_one["deskewed_image"], stage_one["pixels_per_cm"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not stage_two["detections"]:
        raise HTTPException(status_code=422, detail="No confident text was detected in the image.")
    report = _engine().evaluate(stage_two, stage_one["pdp_area_cm2"])
    return {**report, "preprocessing": {key: value for key, value in stage_one.items() if key != "deskewed_image"}}


@app.post("/analyze/ecommerce")
def analyze_ecommerce(request: EcommerceRequest) -> dict[str, Any]:
    """Analyse listing declarations without claiming physical-image compliance."""
    url = str(request.url)
    if urlparse(url).scheme not in {"http", "https"}:
        raise HTTPException(status_code=422, detail="Only HTTP(S) product listing URLs are supported.")
    listing_text, image_urls = _listing_content(url)
    extracted = extract_entities(None, None, reader=_TextReader(listing_text)) if listing_text else None
    if extracted is None or not extracted["detections"]:
        extracted = _image_extraction(image_urls)
    if extracted is None or not extracted["detections"]:
        raise HTTPException(status_code=422, detail="No declaration text was detected in the listing or its sampled images.")
    report = _engine().evaluate(extracted, None, is_ecommerce=True)
    return {
        **report,
        "note": "E-commerce analysis checks listing declaration presence only; font size, placement, and PDP-area compliance cannot be assessed from a listing.",
    }

@app.post("/generate-report")
async def generate_report_endpoint(
    file: UploadFile = File(...),
    manual_pack_width_cm: float | None = Form(None),
    manual_pack_height_cm: float | None = Form(None),
) -> FileResponse:
    """Analyze image and directly return an official downloadable PDF inspection certificate."""
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=422, detail="The uploaded image is empty.")
    image = _decode_image(payload)
    dimensions = None
    if manual_pack_width_cm is not None or manual_pack_height_cm is not None:
        dimensions = (manual_pack_width_cm, manual_pack_height_cm)
    try:
        stage_one = preprocess_image(image, pack_dimensions_cm=dimensions)
        stage_two = extract_entities(stage_one["deskewed_image"], stage_one["pixels_per_cm"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    
    report = _engine().evaluate(stage_two, stage_one["pdp_area_cm2"])
    full_report = {
        **report,
        "preprocessing": {key: value for key, value in stage_one.items() if key != "deskewed_image"}
    }
    
    pdf_path = generate_inspection_certificate(full_report)
    return FileResponse(pdf_path, media_type="application/pdf", filename="MetroGuard_Inspection_Report.pdf")