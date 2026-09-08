
"""Image preprocessing primitives for controlled MetroGuard image capture.

This module deliberately does *not* attempt to infer physical dimensions from
an arbitrary product photograph.  Metric results are only meaningful when an
ArUco marker of a known size has been deliberately placed next to the product,
or when the caller supplies the dimensions of a tightly framed pack image.
"""


from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence

import cv2
import numpy as np


CalibrationMethod = Literal["marker", "manual_input", "failed"]


def _aruco_detector(dictionary_id: int) -> tuple[Any, Any]:
    """Create an ArUco detector while supporting OpenCV 4.x API variants."""
    if not hasattr(cv2, "aruco"):
        raise RuntimeError(
            "ArUco support is unavailable. Install opencv-contrib-python to use "
            "marker calibration."
        )

    dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
    parameters = cv2.aruco.DetectorParameters()
    if hasattr(cv2.aruco, "ArucoDetector"):
        return cv2.aruco.ArucoDetector(dictionary, parameters), None
    return dictionary, parameters


def _detect_marker(
    image: np.ndarray, dictionary_id: int
) -> tuple[np.ndarray | None, float | None]:
    """Return the largest marker's corners and average side length in pixels."""
    try:
        detector_or_dictionary, parameters = _aruco_detector(dictionary_id)
    except RuntimeError:
        return None, None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    if parameters is None:
        corners, _ids, _rejected = detector_or_dictionary.detectMarkers(gray)
    else:
        corners, _ids, _rejected = cv2.aruco.detectMarkers(
            gray, detector_or_dictionary, parameters=parameters
        )
    if not corners:
        return None, None

    marker = max(corners, key=lambda value: abs(cv2.contourArea(value.reshape(-1, 2))))
    points = marker.reshape(4, 2).astype(np.float32)
    side_lengths = np.linalg.norm(points - np.roll(points, -1, axis=0), axis=1)
    return points, float(np.mean(side_lengths))


def _manual_pixels_per_cm(
    image: np.ndarray, pack_dimensions_cm: Sequence[float] | Mapping[str, float] | None
) -> float | None:
    """Calculate scale for an image tightly cropped to the known pack boundary."""
    if pack_dimensions_cm is None:
        return None
    if isinstance(pack_dimensions_cm, Mapping):
        width_cm = pack_dimensions_cm.get("width")
        height_cm = pack_dimensions_cm.get("height")
    else:
        if len(pack_dimensions_cm) != 2:
            raise ValueError("pack_dimensions_cm must contain (width_cm, height_cm).")
        width_cm, height_cm = pack_dimensions_cm

    ratios = []
    if width_cm is not None and float(width_cm) > 0:
        ratios.append(image.shape[1] / float(width_cm))
    if height_cm is not None and float(height_cm) > 0:
        ratios.append(image.shape[0] / float(height_cm))
    if not ratios:
        raise ValueError("At least one positive manual pack dimension is required.")
    return float(np.median(ratios))


def _find_pdp_contour(image: np.ndarray, marker_corners: np.ndarray | None) -> np.ndarray | None:
    """Find a likely label/PDP contour, excluding the calibration marker.

    PDP detection is intentionally conservative: ``None`` is returned rather
    than inventing an area when no sufficiently rectangular panel is visible.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    edges = cv2.morphologyEx(
        edges, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    )
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    image_area = image.shape[0] * image.shape[1]
    marker_center = marker_corners.mean(axis=0) if marker_corners is not None else None
    candidates: list[tuple[float, np.ndarray]] = []

    for contour in contours:
        area = abs(cv2.contourArea(contour))
        if area < image_area * 0.03 or area > image_area * 0.92:
            continue
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        if marker_center is not None and cv2.pointPolygonTest(
            polygon, tuple(marker_center), False
        ) >= 0:
            continue
        rectangle_area = cv2.contourArea(cv2.boxPoints(cv2.minAreaRect(contour)))
        rectangularity = area / rectangle_area if rectangle_area else 0.0
        if rectangularity >= 0.70:
            candidates.append((area * rectangularity, contour))

    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _deskew(image: np.ndarray, contour: np.ndarray | None) -> np.ndarray:
    """Rotate the complete image so the detected PDP's long edge is horizontal."""
    if contour is None:
        return image.copy()
    _center, (width, height), angle = cv2.minAreaRect(contour)
    if width < height:
        angle += 90.0
    matrix = cv2.getRotationMatrix2D(
        (image.shape[1] / 2, image.shape[0] / 2), angle, 1.0
    )
    return cv2.warpAffine(
        image, matrix, (image.shape[1], image.shape[0]),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def preprocess_image(
    image: np.ndarray,
    *,
    marker_side_cm: float = 2.0,
    marker_dictionary_id: int | None = None,
    pack_dimensions_cm: Sequence[float] | Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Calibrate, locate a PDP, and deskew an image for downstream OCR.

    Args:
        image: BGR, grayscale, or BGRA OpenCV image.
        marker_side_cm: Real side length of the deliberately placed square marker.
        marker_dictionary_id: OpenCV ArUco dictionary; defaults to DICT_4X4_50.
        pack_dimensions_cm: ``(width_cm, height_cm)`` or a mapping with ``width``
            and/or ``height``.  This fallback assumes the pack fills the frame.

    Returns:
        A dictionary with ``pixels_per_cm``, ``pdp_area_cm2``, ``deskewed_image``,
        and ``calibration_method``. Physical measurements are ``None`` when
        calibration or PDP detection could not be performed safely.
    """
    if not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("image must be a non-empty numpy array.")
    if image.ndim == 3 and image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    if image.ndim not in (2, 3):
        raise ValueError("image must be grayscale, BGR, or BGRA.")
    if marker_side_cm <= 0:
        raise ValueError("marker_side_cm must be positive.")

    dictionary_id = marker_dictionary_id
    if dictionary_id is None:
        dictionary_id = cv2.aruco.DICT_4X4_50 if hasattr(cv2, "aruco") else 0
    marker_corners, marker_side_px = _detect_marker(image, dictionary_id)
    pixels_per_cm: float | None = None
    method: CalibrationMethod = "failed"
    if marker_side_px is not None:
        pixels_per_cm, method = marker_side_px / marker_side_cm, "marker"
    else:
        pixels_per_cm = _manual_pixels_per_cm(image, pack_dimensions_cm)
        if pixels_per_cm is not None:
            method = "manual_input"

    pdp_contour = _find_pdp_contour(image, marker_corners)
    if pdp_contour is not None and pixels_per_cm is not None:
        pdp_area_cm2 = float(abs(cv2.contourArea(pdp_contour)) / (pixels_per_cm**2))
    elif pixels_per_cm is not None:
        # Fallback: If contour detection fails on complex backgrounds, 
        # use the total frame dimensions as a sensible packaging area estimate.
        pdp_area_cm2 = float((image.shape[0] * image.shape[1]) / (pixels_per_cm**2))
    else:
        pdp_area_cm2 = None
    return {
        "pixels_per_cm": pixels_per_cm,
        "pdp_area_cm2": pdp_area_cm2,
        "deskewed_image": _deskew(image, pdp_contour),
        "calibration_method": method,
    }
