import json
from typing import Any, Mapping, Sequence
from pathlib import Path

MANDATORY_FIELDS = (
    "manufacturer_packer_importer",
    "generic_name",
    "net_quantity",
    "mrp",
    "manufacturing_date",
    "consumer_care",
    "unit_sale_price",
)

class ComplianceEngine:
    """Evaluate Stage 2 extraction results against a supplied JSON rulebook."""

    def __init__(self, rulebook_path: str | Path = "lmpc_rules_2011.json") -> None:
        self.rulebook_path = Path(rulebook_path)
        try:
            with self.rulebook_path.open(encoding="utf-8-sig") as file:
                self.rules: dict[str, Any] = json.load(file)
        except FileNotFoundError:
            self.rules = {"mandatory_declarations": []}

        self.rule_refs = {
            "manufacturer_packer_importer": "Rule 6(1)(a), Rule 10",
            "generic_name": "Rule 6(1)(b)",
            "net_quantity": "Rule 6(1)(c), Rule 12, Rule 13",
            "mrp": "Rule 6(1)(e), Rule 2(m)",
            "manufacturing_date": "Rule 6(1)(d)",
            "consumer_care": "Rule 6(2)",
            "unit_sale_price": "Rule 6(1)(f) / Rule 2(m)",
        }

        for rule in self.rules.get("mandatory_declarations", []):
            rule_id = rule.get("id")
            ref = rule.get("rule_ref")
            if rule_id and ref:
                self.rule_refs[rule_id] = ref

    def evaluate(
        self,
        extraction: Mapping[str, Any],
        pdp_area_cm2: float | None,
        *,
        product_metadata: Mapping[str, Any] | None = None,
        is_ecommerce: bool = False,
        is_molded: bool = False,
    ) -> dict[str, Any]:

        fields = list(MANDATORY_FIELDS)

        mrp_field = extraction.get("mrp") or {}
        mrp_val = mrp_field.get("value")
        if isinstance(mrp_val, dict):
            mrp_val = mrp_val.get("amount")
        mrp_float = float(mrp_val) if mrp_val and str(mrp_val).replace('.', '', 1).isdigit() else 0.0

        # USP requirement is a physical-package concept (PDP area threshold);
        # for e-commerce scans there is no physical PDP area, so treat USP
        # purely as a declaration-presence check based on MRP alone.
        requires_usp = False
        area_val = pdp_area_cm2 if pdp_area_cm2 is not None else 0.0

        if not is_ecommerce and area_val > 100:
            requires_usp = True
            usp_reason_note = f"USP required: PDP area ({area_val:.2f} cm²) exceeds 100 cm² threshold and MRP (₹{mrp_float:.2f}) exceeds ₹35 threshold."
        elif mrp_float > 35.0:
            requires_usp = True
            usp_reason_note = f"USP required: MRP (₹{mrp_float:.2f}) exceeds ₹35 threshold."
        else:
            usp_reason_note = f"USP exempt: MRP (₹{mrp_float:.2f} ≤ ₹35)" + (
                "." if is_ecommerce else f" and PDP area ({area_val:.2f} cm² ≤ 100 cm²)."
            )

        results: dict[str, dict[str, Any]] = {}
        violations: list[dict[str, Any]] = []
        not_detected: list[str] = []

        for field in fields:
            extracted = extraction.get(field) or {}
            rule_reference = self.rule_refs.get(field, "Rule 6")
            extracted_status = extracted.get("status") if isinstance(extracted, Mapping) else None

            if field == "unit_sale_price" and not requires_usp:
                results[field] = {
                    "status": "compliant",
                    "rule_ref": rule_reference,
                    "value": "Exempt by statutory size/price bounds",
                    "confidence": 1.0,
                    "bbox_height_mm": None,
                    "is_molded": is_molded,
                }
                continue

            # MRP found the keyword but no numeric value followed it — this
            # is a confirmed violation (declaration present but blank), not
            # an OCR-uncertainty case. Distinguish it explicitly.
            if field == "mrp" and extracted_status == "blank":
                results[field] = {
                    "status": "non_compliant",
                    "rule_ref": rule_reference,
                    "value": None,
                    "confidence": extracted.get("confidence"),
                    "bbox_height_mm": extracted.get("bbox_height_mm"),
                    "is_molded": is_molded,
                }
                violations.append({
                    "field": field,
                    "rule_ref": rule_reference,
                    "severity": "critical",
                    "description": "MRP declaration is present on the label but no price value was printed (blank MRP field).",
                })
                continue

            if extracted_status == "found":
                bbox_height_mm = extracted.get("bbox_height_mm")
                field_status = "compliant"

                # Font-size/readability checks (Rule 7) apply only to
                # controlled physical-package captures with a real scale
                # reference — never to e-commerce listing images, which
                # have no trustworthy physical scale.
                if not is_ecommerce:
                    min_h = 2.0 if is_molded else 1.0
                    if bbox_height_mm is not None and bbox_height_mm < min_h:
                        field_status = "non_compliant"
                        violations.append({
                            "field": field,
                            "rule_ref": "Rule 7",
                            "severity": "major",
                            "description": f"Font height {bbox_height_mm:.2f}mm is below required minimum of {min_h}mm (is_molded={is_molded}).",
                        })

                results[field] = {
                    "status": field_status,
                    "rule_ref": rule_reference,
                    "value": extracted.get("value"),
                    "confidence": extracted.get("confidence"),
                    "bbox_height_mm": bbox_height_mm if not is_ecommerce else None,
                    "is_molded": is_molded,
                }
            else:
                results[field] = {
                    "status": "not_detected",
                    "rule_ref": rule_reference,
                    "reason": "Declaration was not detected by OCR or requires manual verification.",
                }
                not_detected.append(field)

        active_not_detected = [f for f in not_detected if f != "unit_sale_price" or requires_usp]

        if violations:
            overall_status = "non_compliant"
        elif active_not_detected:
            overall_status = "review_required"
        else:
            overall_status = "compliant"

        return {
            "overall_status": overall_status,
            "is_ecommerce": is_ecommerce,
            "is_molded": is_molded,
            "usp_context": {
                "required": requires_usp,
                "reason": usp_reason_note,
            },
            "fields": results,
            "violations": violations,
            "not_detected_fields": active_not_detected,
        }
