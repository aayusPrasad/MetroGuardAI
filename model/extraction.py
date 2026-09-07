
"""OCR and conservative declaration extraction with mutually exclusive Net Quantity and flexible USP matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Protocol, Sequence
import cv2
import numpy as np
import torch

MRP_KEYWORD_PATTERN = re.compile(r"\bm\.?\s*r\.?\s*p\.?\b|maximum\s+retail\s+price", re.IGNORECASE)
CURRENCY_VALUE_PATTERN = re.compile(r"(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d{1,2})?)\b", re.IGNORECASE)

USP_PATTERN = re.compile(
    r"(?:(?:usp|unit\s*sale\s*price)\s*[:.-]*)?\s*(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:/|per)\s*(?:100\s*g(?:rams)?|100\s*ml|kg|g(?:rams)?|ml|l)\b",
    re.IGNORECASE,
)

NET_QUANTITY_PATTERN = re.compile(
    r"\b(?:net\s*(?:qty|quantity|wt|weight|vol(?:ume)?)\s*[:.-]?\s*)?"
    r"(\d+(?:\.\d+)?)\s*(g|kg|ml|l)\b",
    re.IGNORECASE,
)
VAGUE_QUANTITY_PATTERN = re.compile(r"\b(?:approx(?:imately)?|about|minimum)\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?91[-\s]?)?(?:0[-\s]?)?[6-9]\d{9}(?!\d)|"
    r"(?<!\d)\d{3,5}[-\s]?\d{6,8}(?!\d)"
)
EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.?[A-Z]{2,}\b",
    re.IGNORECASE,
)
COUNTRY_PATTERN = re.compile(
    r"\b(?:made\s+in|country\s+of\s+origin)\s*[:.-]?\s*"
    r"([A-Za-z][A-Za-z .'-]{1,60}?)(?=$|[,;|])",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(
    r"\b(?:\d{1,2}[/-])?(?:0?[1-9]|1[0-2])[/-]\d{2,4}\b|"
    r"\b(?:0?[1-9]|1[0-2])[/-]\d{2,4}\b|"
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{4}\b",
    re.IGNORECASE,
)
INCLUSIVE_TAX_PATTERN = re.compile(
    r"\b(?:inclusive\s+(?:of\s+)?all\s+tax(?:es)?|incl\.?\s*(?:of\s+)?all\s+tax(?:es)?)\b",
    re.IGNORECASE,
)
ADDRESS_KEYWORD_PATTERN = re.compile(
    r"\b(?:mfd\.?\s*by|manufactured\s+by|marketed\s+by|packed\s+by|"
    r"imported\s+by|packer)\b",
    re.IGNORECASE,
)
BARCODE_PATTERN = re.compile(r"\d{10,}")


class OCRReader(Protocol):
    def readtext(self, image: Any, detail: int = 1, **kwargs: Any) -> list[Any]: ...


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


def _field(value: Any, block: MergedBlock, status: str = "found") -> dict[str, Any]:
    return {
        "value": value,
        "confidence": block.confidence,
        "bbox_height_mm": block.bbox_height_mm,
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
        new_w = int(w * scale_factor)
        new_h = int(h * scale_factor)
        image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

    if len(image.shape) == 2:
        img_bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    else:
        img_bgr = image
        
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
    l_channel, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    cl = clahe.apply(l_channel)
    merged = cv2.merge((cl, a, b))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)


def merge_stable_blocks(
    ocr_result: Iterable[Any], confidence_threshold: float, pixels_per_cm: float | None
) -> list[MergedBlock]:
    raw_boxes = []
    for item in ocr_result:
        if len(item) < 3:
            continue
        bbox, text, confidence = item[0], str(item[1]).strip(), float(item[2])
        if not text or confidence < confidence_threshold:
            continue
        
        if BARCODE_PATTERN.search(text) and len(text.replace(" ", "")) >= 10:
            continue
        
        text = text.replace("<", "₹")
        if "@" in text and text.lower().endswith(" in"):
            text = text[:-3] + ".in"

        pts = [(float(p[0]), float(p[1])) for p in bbox]
        min_x = min(p[0] for p in pts)
        max_x = max(p[0] for p in pts)
        min_y = min(p[1] for p in pts)
        max_y = max(p[1] for p in pts)
        height = max_y - min_y
        raw_boxes.append({
            "text": text,
            "confidence": confidence,
            "min_x": min_x,
            "max_x": max_x,
            "min_y": min_y,
            "max_y": max_y,
            "height": height,
            "bbox": tuple(pts)
        })

    if not raw_boxes:
        return []

    raw_boxes.sort(key=lambda b: b["min_y"])

    lines: list[list[dict[str, Any]]] = []
    for box in raw_boxes:
        placed = False
        for line in lines:
            line_min_y = min(b["min_y"] for b in line)
            line_max_y = max(b["max_y"] for b in line)
            avg_h = sum(b["height"] for b in line) / len(line)
            
            if abs(box["min_y"] - line_min_y) < (0.7 * avg_h) and abs(box["max_y"] - line_max_y) < (0.7 * avg_h):
                line.append(box)
                placed = True
                break
        if not placed:
            lines.append([box])

    merged_blocks: list[MergedBlock] = []
    for idx, line in enumerate(lines):
        line.sort(key=lambda b: b["min_x"])
        combined_text = " ".join(b["text"] for b in line)
        avg_conf = sum(b["confidence"] for b in line) / len(line)
        
        min_x = min(b["min_x"] for b in line)
        max_x = max(b["max_x"] for b in line)
        min_y = min(b["min_y"] for b in line)
        max_y = max(b["max_y"] for b in line)
        
        bbox = ((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y))
        h_mm = _bbox_height_mm(bbox, pixels_per_cm)
        
        merged_blocks.append(MergedBlock(
            text=combined_text,
            confidence=avg_conf,
            bbox=bbox,
            bbox_height_mm=h_mm,
            index=idx,
            min_x=min_x,
            max_x=max_x,
            min_y=min_y,
            max_y=max_y
        ))

    return merged_blocks


def extract_entities(
    image: Any,
    pixels_per_cm: float | None,
    *,
    confidence_threshold: float = 0.25,
    languages: Sequence[str] = ("en",),
    gpu: bool = True,
    reader: OCRReader | None = None,
) -> dict[str, Any]:
    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("confidence_threshold must be between 0 and 1.")
    if pixels_per_cm is not None and pixels_per_cm <= 0:
        raise ValueError("pixels_per_cm must be positive when provided.")
        
    enhanced_image = enhance_real_world_image(image)
    use_gpu = torch.cuda.is_available()

    if reader is None:
        import easyocr
        reader = easyocr.Reader(list(languages), gpu=use_gpu)
    
    raw_ocr = reader.readtext(enhanced_image, detail=1)
    blocks = merge_stable_blocks(raw_ocr, confidence_threshold, pixels_per_cm)

    consumed_indices: set[int] = set()

    def mark_consumed(block: MergedBlock) -> None:
        consumed_indices.add(block.index)

    # 1. Consumer Care
    consumer = _not_detected()
    all_contacts = []
    consumer_block = None
    for block in blocks:
        contacts = PHONE_PATTERN.findall(block.text) + EMAIL_PATTERN.findall(block.text)
        if contacts:
            all_contacts.extend(contacts)
            if not consumer_block:
                consumer_block = block
            mark_consumed(block)
        elif "customer care" in block.text.lower() or "feedback" in block.text.lower() or "query" in block.text.lower():
            if not consumer_block:
                consumer_block = block
            mark_consumed(block)

    if all_contacts:
        consumer = _field(list(set(all_contacts)), consumer_block if consumer_block else blocks[0])
    elif consumer_block:
        consumer = _field([consumer_block.text], consumer_block)

    # 2. MRP Extraction
    mrp = _not_detected()
    for block in blocks:
        if block.index in consumed_indices:
            continue
        
        if MRP_KEYWORD_PATTERN.search(block.text):
            match = CURRENCY_VALUE_PATTERN.search(block.text)
            if match:
                amount_str = match.group(1).replace(",", "")
                amount_float = float(amount_str)
                if amount_float <= 2000.0:
                    nearby = " ".join(b.text for b in blocks if abs(b.index - block.index) <= 1)
                    mrp = _field(
                        {"amount": amount_str, "inclusive_of_all_taxes": bool(INCLUSIVE_TAX_PATTERN.search(nearby))},
                        block,
                        status="found"
                    )
                    mark_consumed(block)
                    break
            
            same_line_neighbors = [
                b for b in blocks 
                if b.index not in consumed_indices 
                and b.min_x > block.max_x 
                and abs(b.min_y - block.min_y) < (0.6 * (block.max_y - block.min_y))
            ]
            
            if same_line_neighbors:
                neighbor = min(same_line_neighbors, key=lambda b: b.min_x)
                if (neighbor.min_x - block.max_x) < 200:
                    match_neighbor = CURRENCY_VALUE_PATTERN.search(neighbor.text)
                    if match_neighbor:
                        amount_str = match_neighbor.group(1).replace(",", "")
                        amount_float = float(amount_str)
                        if amount_float <= 2000.0:
                            mrp = _field(
                                {"amount": amount_str, "inclusive_of_all_taxes": bool(INCLUSIVE_TAX_PATTERN.search(block.text + " " + neighbor.text))},
                                neighbor,
                                status="found"
                            )
                            mark_consumed(block)
                            mark_consumed(neighbor)
                            break
            
            mrp = {
                "value": None,
                "confidence": block.confidence,
                "bbox_height_mm": block.bbox_height_mm,
                "status": "blank",
            }
            mark_consumed(block)
            break

    # 3. Net Quantity Extraction
    quantity = _not_detected()
    prohibited_practice = _not_detected()
    for block in blocks:
        match = NET_QUANTITY_PATTERN.search(block.text)
        if match and block.index not in consumed_indices:
            context = " ".join(b.text for b in blocks if abs(b.index - block.index) <= 1)
            vague = VAGUE_QUANTITY_PATTERN.search(context)
            quantity = _field({"amount": match.group(1), "unit": match.group(2).lower()}, block)
            if vague:
                prohibited_practice = _field(f"Vague net quantity qualifier: {vague.group(0)}", block)
            mark_consumed(block)
            break

    # 4. USP Extraction
    usp = _not_detected()
    for block in blocks:
        match = USP_PATTERN.search(block.text)
        if match and block.index not in consumed_indices:
            usp = _field({"amount": match.group(1).replace(",", "")}, block)
            mark_consumed(block)
            break

    # 5. Dates Extraction
    manufactured, expiry = _not_detected(), _not_detected()
    for block in blocks:
        if block.index in consumed_indices:
            continue
        date_match = DATE_PATTERN.search(block.text)
        if not date_match:
            continue
        label = block.text.lower()
        if manufactured["status"] == "not_detected" and re.search(r"\b(?:mfd|mfg|manufactur)", label):
            manufactured = _field(date_match.group(0), block)
            mark_consumed(block)
        elif expiry["status"] == "not_detected" and re.search(r"\b(?:exp|expiry|best\s+before|use\s+by)", label):
            expiry = _field(date_match.group(0), block)
            mark_consumed(block)

    # 6. Country of Origin
    country = _not_detected()
    for block in blocks:
        if block.index in consumed_indices:
            continue
        match = COUNTRY_PATTERN.search(block.text)
        if match:
            country = _field(match.group(1).strip(" .:-"), block)
            mark_consumed(block)
            break

    # 7. Manufacturer / Packer / Importer Address Block
    address_block = _not_detected()
    candidates = [(i, b) for i, b in enumerate(blocks) if b.index not in consumed_indices and ADDRESS_KEYWORD_PATTERN.search(b.text)]
    if candidates:
        _, keyword_block = candidates[0]
        lines = [keyword_block.text]
        mark_consumed(keyword_block)
        
        current_y = keyword_block.max_y
        for b in blocks:
            if b.index in consumed_indices:
                continue
            vert_dist = b.min_y - current_y
            if 0 <= vert_dist < 60 and abs(b.min_x - keyword_block.min_x) < 150:
                lines.append(b.text)
                mark_consumed(b)
                current_y = b.max_y
                
        address_block = _field(" ".join(lines), keyword_block)

    generic_name = _not_detected()

    return {
        "mrp": mrp,
        "unit_sale_price": usp,
        "net_quantity": quantity,
        "net_quantity_prohibited_practice": prohibited_practice,
        "manufacturing_date": manufactured,
        "expiry_date": expiry,
        "consumer_care": consumer,
        "manufacturer_packer_importer": address_block,
        "country_of_origin": country,
        "generic_name": generic_name,
        "detections": [
            {"text": item.text, "confidence": item.confidence, "bbox": item.bbox, "bbox_height_mm": item.bbox_height_mm}
            for item in blocks
        ],
    }