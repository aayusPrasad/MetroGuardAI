"""FastAPI application for MetroGuard AI with integrated image quality recovery."""

from __future__ import annotations

import io
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urljoin

import cv2
import numpy as np
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from compliance_engine import ComplianceEngine
from extraction import extract_entities
from image_enhancement import enhance_low_res_image
from report_generator import generate_inspection_certificate

app = FastAPI(
    title="MetroGuard AI",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_ENGINE: ComplianceEngine | None = None
MIN_DIMENSION_PX = 1200


def _engine() -> ComplianceEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = ComplianceEngine()
    return _ENGINE


def _decode_image(payload: bytes) -> np.ndarray:
    arr = np.frombuffer(payload, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=422, detail="Invalid image format.")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def preprocess_image(
    image: np.ndarray, pack_dimensions_cm: tuple[float | None, float | None] | None = None
) -> dict[str, Any]:
    h, w, _ = image.shape
    if pack_dimensions_cm and pack_dimensions_cm[0] and pack_dimensions_cm[1]:
        width_cm, height_cm = pack_dimensions_cm
        pixels_per_cm = (w / width_cm + h / height_cm) / 2.0
        pdp_area_cm2 = width_cm * height_cm
        calibration_method = "manual_input"
    else:
        pixels_per_cm = 58.62
        pdp_area_cm2 = (w / pixels_per_cm) * (h / pixels_per_cm)
        calibration_method = "estimated_default"

    return {
        "deskewed_image": image,
        "pixels_per_cm": pixels_per_cm,
        "pdp_area_cm2": pdp_area_cm2,
        "calibration_method": calibration_method,
    }


def _run_pipeline(
    image: np.ndarray,
    dimensions: tuple[float | None, float | None] | None,
    is_molded: bool,
    is_ecommerce: bool = False,
) -> dict[str, Any]:
    """Shared pipeline logic used by /analyze/image and /generate-report."""
    h, w, _ = image.shape

    enhancement_meta = {
        "image_enhancement_applied": False,
        "original_resolution": f"{w}x{h}",
        "enhancement_method": "none",
    }

    if min(h, w) < MIN_DIMENSION_PX:
        try:
            image, enhancement_meta = enhance_low_res_image(image)
        except Exception as e:
            return {
                "overall_status": "insufficient_image_quality",
                "message": f"Image resolution ({w}x{h}px) is too low, and recovery failed: {e}",
                "is_ecommerce": is_ecommerce,
                "is_molded": is_molded,
                "fields": {},
                "violations": [],
                "not_detected_fields": [],
                "preprocessing": enhancement_meta,
            }

    stage_one = preprocess_image(image, pack_dimensions_cm=dimensions)
    stage_two = extract_entities(stage_one["deskewed_image"], stage_one["pixels_per_cm"])

    detections = stage_two.get("detections", [])
    avg_confidence = (
        sum(d["confidence"] for d in detections) / len(detections) if detections else 0.0
    )

    if enhancement_meta["image_enhancement_applied"] and (avg_confidence < 0.35 or not detections):
        return {
            "overall_status": "insufficient_image_quality",
            "message": (
                f"Image enhancement ({enhancement_meta['enhancement_method']}) was applied, "
                f"but OCR confidence remained critically low (Avg: {avg_confidence:.2f}). "
                f"Please recapture at a natively higher resolution."
            ),
            "is_ecommerce": is_ecommerce,
            "is_molded": is_molded,
            "fields": {},
            "violations": [],
            "not_detected_fields": [],
            "preprocessing": enhancement_meta,
        }

    report = _engine().evaluate(
        extraction=stage_two,
        pdp_area_cm2=stage_one["pdp_area_cm2"],
        is_molded=is_molded,
        is_ecommerce=is_ecommerce,
    )

    merged_preprocessing = {k: v for k, v in stage_one.items() if k != "deskewed_image"}
    merged_preprocessing.update(enhancement_meta)

    return {
        **report,
        "is_molded": is_molded,
        "preprocessing": merged_preprocessing,
    }


@app.post("/analyze/image")
async def analyze_image(
    file: UploadFile = File(...),
    manual_pack_width_cm: float | None = Form(None),
    manual_pack_height_cm: float | None = Form(None),
    is_molded: bool = Form(False),
) -> dict[str, Any]:
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=422, detail="Empty image.")

    image = _decode_image(payload)
    dimensions = (
        (manual_pack_width_cm, manual_pack_height_cm)
        if (manual_pack_width_cm or manual_pack_height_cm)
        else None
    )

    try:
        return _run_pipeline(image, dimensions, is_molded)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class EcommerceRequest(BaseModel):
    url: str


@app.post("/analyze/ecommerce")
async def analyze_ecommerce(payload: EcommerceRequest) -> dict[str, Any]:
    """
    NOTE: extract_entities() only accepts an image array, not raw text — there
    is no text-based extraction path in the current codebase. This endpoint
    therefore downloads product images from the listing page and runs the
    same image pipeline used for physical scans, matching what the README
    describes ("falls back to up to three listing images"). It does NOT
    parse HTML text directly, since extraction.py has no support for that.
    """
    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="URL must start with http:// or https://")

    try:
        resp = requests.get(payload.url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=422, detail=f"Could not fetch listing: {exc}") from exc

    soup = BeautifulSoup(resp.text, "html.parser")
    img_tags = soup.find_all("img", src=True)[:3]  # first 3 images, per README

    if not img_tags:
        raise HTTPException(status_code=422, detail="No product images found on this listing.")

    last_result: dict[str, Any] | None = None
    fetch_errors: list[str] = []

    for img_tag in img_tags:
        img_url = img_tag["src"]

        # Resolve every non-absolute form (protocol-relative "//", root-
        # relative "/path", and plain relative "file.png") against the
        # page's own URL. This is the fix: the previous version only
        # handled "//" and leading "/" cases, so a bare relative filename
        # (e.g. "crispy-crunch-label.png") was never resolved to a
        # fetchable absolute URL.
        if not img_url.startswith("http"):
            img_url = urljoin(payload.url, img_url)

        try:
            img_resp = requests.get(img_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            img_resp.raise_for_status()
            image = _decode_image(img_resp.content)
        except Exception as e:
            fetch_errors.append(f"{img_url}: {e}")
            continue

        result = _run_pipeline(image, dimensions=None, is_molded=False, is_ecommerce=True)
        last_result = result

        if result.get("overall_status") != "insufficient_image_quality":
            break

    if last_result is None:
        detail = "Could not extract declarations from any listing image."
        if fetch_errors:
            detail += " Errors: " + "; ".join(fetch_errors)
        raise HTTPException(status_code=422, detail=detail)

    return {
        **last_result,
        "note": (
            "This is an e-commerce listing scan. Font size, placement, and "
            "PDP-area compliance cannot be assessed reliably without a "
            "controlled physical capture — treat this result as a "
            "declaration-presence check only."
        ),
    }


@app.post("/generate-report")
async def generate_report(
    file: UploadFile = File(...),
    manual_pack_width_cm: float | None = Form(None),
    manual_pack_height_cm: float | None = Form(None),
    is_molded: bool = Form(False),
    format: str = Form("pdf"),  # "pdf" or "docx"
) -> FileResponse:
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=422, detail="Empty image.")

    image = _decode_image(payload)
    dimensions = (
        (manual_pack_width_cm, manual_pack_height_cm)
        if (manual_pack_width_cm or manual_pack_height_cm)
        else None
    )

    result = _run_pipeline(image, dimensions, is_molded)

    if result.get("overall_status") == "insufficient_image_quality":
        raise HTTPException(status_code=422, detail=result.get("message", "Image quality too low."))

    if format == "docx":
        from report_generator import generate_inspection_docx
        with NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp_path = tmp.name
        generate_inspection_docx(result, output_path=tmp_path)
        return FileResponse(
            path=tmp_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="MetroGuard_Inspection_Report.docx",
        )

    with NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    generate_inspection_certificate(result, output_path=tmp_path)
    return FileResponse(
        path=tmp_path,
        media_type="application/pdf",
        filename="MetroGuard_Inspection_Report.pdf",
    )
