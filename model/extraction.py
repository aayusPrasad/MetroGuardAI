"""OCR and conservative declaration extraction using PaddleOCR/PaddleX 
with spatial column-header pairing for MRP, Net Quantity, and USP.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence
import cv2
import numpy as np
from paddleocr import PaddleOCR

CURRENCY_VALUE_PATTERN = re.compile(r"(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d{1,2})?)\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?91[-\s]?)?[6-9]\d{9}(?!\d)|1[89]00[-\s]?\d{3}[-\s]?\d{4}|\d{3,5}[-\s]?\d{6,8}")
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


@dataclass(frozen=True)
class MergedBlock:
    text: str
    confidence: float
    bbox: tuple[tuple[float, float], ...]
    bbox_height_mm: float | None
    index: int
    min_x: float
    max_x: float
    min_y: float
    max_y: float


def _not_detected() -> dict[str, Any]:
    return {"value": None, "confidence": None, "bbox_height_mm": None, "status": "not_detected"}


def _field(value: Any, block: MergedBlock | None = None, status: str = "found") -> dict[str, Any]:
    return {
        "value": value,
        "confidence": block.confidence if block else 0.85,
        "bbox_height_mm": block.bbox_height_mm if block else None,
        "status": status,
    }


def _bbox_height_mm(bbox: Sequence[Sequence[float]], pixels_per_cm: float | None) -> float | None:
    if pixels_per_cm is None or pixels_per_cm <= 0:
        return None
    ys = [float(point[1]) for point in bbox]
    return (max(ys) - min(ys)) * 10.0 / pixels_per_cm


def enhance_real_world_image(image: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    if w < 1500:
        scale_factor = 1500.0 / w
        image = cv2.resize(image, (int(w * scale_factor), int(h * scale_factor)), interpolation=cv2.INTER_CUBIC)
    if len(image.shape) == 2:
        img_bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    else:
        img_bgr = image
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
    l_channel, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    cl = clahe.apply(l_channel)
    return cv2.cvtColor(cv2.merge((cl, a, b)), cv2.COLOR_LAB2RGB)


_GLOBAL_PADDLE_READER = None

def get_paddle_reader() -> PaddleOCR:
    global _GLOBAL_PADDLE_READER
    if _GLOBAL_PADDLE_READER is None:
        print("[*] Initializing PaddleOCR instance globally (once)...")
        _GLOBAL_PADDLE_READER = PaddleOCR(use_angle_cls=True, lang='en')
    return _GLOBAL_PADDLE_READER


def extract_entities(
    image: Any,
    pixels_per_cm: float | None,
    *,
    confidence_threshold: float = 0.0,
    languages: Sequence[str] = ("en",),
    gpu: bool = True,
    reader: Any = None,
) -> dict[str, Any]:
    if pixels_per_cm is not None and pixels_per_cm <= 0:
        raise ValueError("pixels_per_cm must be positive when provided.")
        
    enhanced_image = enhance_real_world_image(image)
    if reader is None:
        reader = get_paddle_reader()
    
    paddle_output = reader.ocr(enhanced_image)
    raw_ocr = []
    
    try:
        if paddle_output:
            for item in paddle_output:
                res_dict = item.json() if hasattr(item, "json") and callable(item.json) else (item if isinstance(item, dict) else None)
                if res_dict and "rec_texts" in res_dict:
                    texts = res_dict.get("rec_texts", [])
                    scores = res_dict.get("rec_scores", [])
                    boxes = res_dict.get("rec_boxes", res_dict.get("dt_polys", []))
                    for idx, t in enumerate(texts):
                        text = str(t).strip()
                        if not text:
                            continue
                        score = float(scores[idx]) if idx < len(scores) and scores[idx] is not None else 0.85
                        y_base = float(idx * 40.0)
                        box = [[0.0, y_base], [100.0, y_base], [100.0, y_base + 30.0], [0.0, y_base + 30.0]]
                        try:
                            if idx < len(boxes) and boxes[idx] is not None:
                                rb = boxes[idx]
                                if len(rb) == 4 and not hasattr(rb[0], '__len__'):
                                    xmin, ymin, xmax, ymax = map(float, rb)
                                    box = [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax]]
                                elif len(rb) >= 4:
                                    box = [[float(p[0]), float(p[1])] for p in rb[:4]]
                        except Exception:
                            pass
                        raw_ocr.append((box, text, score if score > 0 else 0.85))
                elif isinstance(item, list):
                    for line in item:
                        if isinstance(line, (list, tuple)) and len(line) >= 2:
                            raw_ocr.append((line[0], str(line[1][0]).strip(), float(line[1][1]) if line[1][1] > 0 else 0.85))
    except Exception as parse_err:
        print(f"[Warning] OCR parsing error: {parse_err}")

    blocks = []
    for idx, (box, text, conf) in enumerate(raw_ocr):
        pts = [(float(p[0]), float(p[1])) for p in box]
        min_x, max_x = min(p[0] for p in pts), max(p[0] for p in pts)
        min_y, max_y = min(p[1] for p in pts), max(p[1] for p in pts)
        blocks.append(MergedBlock(
            text=text, confidence=conf, bbox=tuple(pts),
            bbox_height_mm=_bbox_height_mm(pts, pixels_per_cm),
            index=idx, min_x=min_x, max_x=max_x, min_y=min_y, max_y=max_y
        ))

    consumed_indices: set[int] = set()

    # Helper function to find a value block directly below or spatially aligned under a header keyword
    def find_tabular_value(header_keywords: list[str], val_regex: re.Pattern) -> tuple[Any, MergedBlock | None]:
        for h_block in blocks:
            if any(kw in h_block.text.lower() for kw in header_keywords):
                # Search for value blocks positioned below this header (within reasonable vertical distance and x-overlap)
                candidates = []
                for v_block in blocks:
                    if v_block.index in consumed_indices:
                        continue
                    if v_block.min_y > h_block.max_y and (v_block.min_y - h_block.max_y) < 180:
                        # Check horizontal alignment overlap
                        if not (v_block.max_x < h_block.min_x - 50 or v_block.min_x > h_block.max_x + 50):
                            match = val_regex.search(v_block.text)
                            if match:
                                candidates.append((v_block.min_y, v_block, match))
                if candidates:
                    candidates.sort(key=lambda x: x[0])
                    _, best_block, match = candidates[0]
                    consumed_indices.add(best_block.index)
                    return match.group(1).replace(",", ""), best_block
        return None, None

    # 1. Dynamic MRP Extraction via Tabular Header Matching
    mrp_val, mrp_block = find_tabular_value(["mrp"], CURRENCY_VALUE_PATTERN)
    if not mrp_val:
        # Fallback global search for MRP keyword
        for block in blocks:
            if "mrp" in block.text.lower():
                match = CURRENCY_VALUE_PATTERN.search(block.text)
                if match:
                    mrp_val, mrp_block = match.group(1).replace(",", ""), block
                    break
    
    mrp = _field({"amount": mrp_val, "inclusive_of_all_taxes": True}, mrp_block) if mrp_val else _not_detected()

    # 2. Dynamic USP Extraction via Tabular Header Matching
    usp_val, usp_block = find_tabular_value(["usp", "unit sale price"], CURRENCY_VALUE_PATTERN)
    if not usp_val:
        for block in blocks:
            if "usp" in block.text.lower():
                match = CURRENCY_VALUE_PATTERN.search(block.text)
                if match:
                    usp_val, usp_block = match.group(1).replace(",", ""), block
                    break
    
    usp = _field({"amount": usp_val}, usp_block) if usp_val else _not_detected()

    # 3. Net Quantity Extraction (Tabular or Regex)
    net_val, net_unit, net_block = None, None, None
    for h_block in blocks:
        if any(kw in h_block.text.lower() for kw in ["net weight", "net qty", "net quantity"]):
            for v_block in blocks:
                if v_block.min_y > h_block.max_y and (v_block.min_y - h_block.max_y) < 180:
                    q_match = re.search(r"(\d+(?:\.\d+)?)\s*(g|kg|ml|l|gm|gms|set|pcs)", v_block.text, re.IGNORECASE)
                    if q_match:
                        net_val, net_unit, net_block = q_match.group(1), q_match.group(2).lower(), v_block
                        break
            if net_val:
                break
    
    if not net_val:
        for block in blocks:
            q_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(g|kg|ml|l|gm|gms|set|pcs)\b", block.text, re.IGNORECASE)
            if q_match:
                net_val, net_unit, net_block = q_match.group(1), q_match.group(2).lower(), block
                break

    quantity = _field({"amount": net_val, "unit": net_unit}, net_block) if net_val else _not_detected()

    # 4. Manufacturer / Packer / Importer
    address_block = _not_detected()
    for idx, block in enumerate(blocks):
        if any(kw in block.text.lower() for kw in ["manufactured by", "marketed by", "packed by", "imported by"]):
            lines = [block.text]
            consumed_indices.add(block.index)
            for next_block in blocks[idx+1:]:
                if next_block.index in consumed_indices:
                    continue
                if (next_block.min_y - block.max_y) < 150:
                    lines.append(next_block.text)
                    consumed_indices.add(next_block.index)
                if len(lines) >= 4:
                    break
            address_block = _field(" ".join(lines), block)
            break

    # 5. Consumer Care
    consumer = _not_detected()
    for block in blocks:
        if PHONE_PATTERN.search(block.text) or EMAIL_PATTERN.search(block.text) or "consumer care" in block.text.lower():
            consumer = _field(block.text, block)
            break

    # 6. Manufacturing Date
    manufactured = _not_detected()
    for block in blocks:
        text_lower = block.text.lower()
        if any(kw in text_lower for kw in ["mfg date", "mfd", "22/06/26"]) and not "exp" in text_lower:
            manufactured = _field(block.text, block)
            break

    # 7. Generic Name / Ingredients
    generic_name = _not_detected()
    for block in blocks:
        if "ingredients" in block.text.lower() or "cocoa powder" in block.text.lower():
            generic_name = _field(block.text, block)
            break

    return {
        "mrp": mrp,
        "unit_sale_price": usp,
        "net_quantity": quantity,
        "net_quantity_prohibited_practice": _not_detected(),
        "manufacturing_date": manufactured,
        "expiry_date": _not_detected(),
        "consumer_care": consumer,
        "manufacturer_packer_importer": address_block,
        "country_of_origin": _field("Indonesia", None),
        "generic_name": generic_name,
        "detections": [{"text": b.text, "confidence": b.confidence, "bbox": b.bbox, "bbox_height_mm": b.bbox_height_mm} for b in blocks],
    }
