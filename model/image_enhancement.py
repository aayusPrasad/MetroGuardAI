%%writefile image_enhancement.py
"""
Smart Image Enhancement for MetroGuard AI.
Uses ESPCN Super-Resolution to recover low-resolution packaging images for OCR.
"""

from __future__ import annotations

import logging
import os
import urllib.request
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ESPCN x4 model from OpenCV's model zoo
MODEL_NAME = "ESPCN_x4.pb"
MODEL_URL = "https://github.com/fannymonori/TF-ESPCN/raw/master/export/ESPCN_x4.pb"


def _download_model_if_missing() -> bool:
    """Download the pretrained Super-Resolution model if not present locally."""
    if os.path.exists(MODEL_NAME):
        return True
    
    print(f"[Enhancement] Downloading Super-Resolution model ({MODEL_NAME})...")
    try:
        urllib.request.urlretrieve(MODEL_URL, MODEL_NAME)
        print("[Enhancement] Model downloaded successfully.")
        return True
    except Exception as e:
        logger.warning(f"Failed to download ESPCN model: {e}")
        return False


def _crop_to_pdp(image: np.ndarray) -> np.ndarray:
    """
    Region-First Cropping: Detects the main packaging label (PDP) and crops to it.
    Prevents wasting super-resolution processing on background clutter.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 50, 150)
    
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    closed = cv2.morphologyEx(edged, cv2.MORPH_CLOSE, kernel)
    
    contours, _ = cv2.findContours(closed.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    if not contours:
        return image
        
    largest_contour = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(largest_contour)
    
    img_area = image.shape[0] * image.shape[1]
    if (w * h) > (0.2 * img_area):
        return image[y:y+h, x:x+w]
        
    return image


def enhance_low_res_image(image: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """
    Attempt ESPCN Super-Resolution on a cropped region, falling back to bicubic.
    """
    orig_h, orig_w = image.shape[:2]
    
    cropped = _crop_to_pdp(image)
    
    enhanced = None
    enhancement_method = "none"
    
    has_model = _download_model_if_missing()
    
    try:
        if has_model and hasattr(cv2, 'dnn_superres'):
            sr = cv2.dnn_superres.DnnSuperResImpl_create()
            sr.readModel(MODEL_NAME)
            sr.setModel("espcn", 4)
            enhanced = sr.upsample(cropped)
            enhancement_method = "ESPCN_x4_SuperResolution"
            print("[Enhancement] Successfully applied ESPCN Super-Resolution.")
    except Exception as e:
        logger.warning(f"Super-Resolution failed, falling back: {e}")
        
    if enhanced is None:
        crop_h, crop_w = cropped.shape[:2]
        scale_factor = 1600.0 / max(crop_h, crop_w)
        if scale_factor > 1.0:
            new_w = int(crop_w * scale_factor)
            new_h = int(crop_h * scale_factor)
            enhanced = cv2.resize(cropped, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
            enhancement_method = "Bicubic_Interpolation_Fallback"
            print(f"[Enhancement] Applied Bicubic Fallback scale: {scale_factor:.2f}x")
        else:
            enhanced = cropped
            
    final_h, final_w = enhanced.shape[:2]
    
    metadata = {
        "image_enhancement_applied": True,
        "enhancement_method": enhancement_method,
        "original_resolution": f"{orig_w}x{orig_h}",
        "enhanced_resolution": f"{final_w}x{final_h}",
    }
    
    return enhanced, metadata