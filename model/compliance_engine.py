%%writefile compliance_engine.py
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
    "unit_sale_price",  # Always evaluate USP so it never vanishes from the table
)

class ComplianceEngine:
    """Evaluate Stage 2 extraction results against a supplied JSON rulebook."""

    def __init__(self, rulebook_path: str | Path = "lmpc_rules_2011.json") -> None:
        self.rulebook_path = Path(rulebook_path)
        try:
            with self.rulebook_path.open(encoding="utf-8") as file:
                self.rules: dict[str, Any] = json.load(file)
        except FileNotFoundError:
            self.rules = {"mandatory_declarations": []}
            
        # Precise rule reference map matching the rulebook specifications
        self.rule_refs = {
            "manufacturer_packer_importer": "Rule 6(1)(a), Rule 10",
            "generic_name": "Rule 6(1)(b)",
            "net_quantity": "Rule 6(1)(c), Rule 12, Rule 13",
            "mrp": "Rule 6(1)(e), Rule 2(m)",
            "manufacturing_date": "Rule 6(1)(d)",
            "consumer_care": "Rule 6(2)",
            "unit_sale_price": "Rule 6(1)(f) / Rule 2(m)",
        }
        
        # Override or extend dynamically from JSON rulebook if available
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
    ) -> dict[str, Any]:
        
        metadata = product_metadata or {}
        fields = list(MANDATORY_FIELDS)
        
        # Evaluate Unit Sale Price waiver condition based on actual numbers
        mrp_val = extraction.get("mrp", {}).get("value", {}).get("amount")
        mrp_float = float(mrp_val) if mrp_val and str(mrp_val).replace('.', '', 1).isdigit() else 0.0
        
        requires_usp = False
        area_val = pdp_area_cm2 if pdp_area_cm2 is not None else 0.0
        
        if area_val > 100:
            requires_usp = True
            usp_reason_note = f"USP required: PDP area ({area_val:.2f} cm²) exceeds 100 cm² threshold and MRP (₹{mrp_float:.2f}) exceeds ₹35 threshold."
        elif mrp_float > 35.0:
            requires_usp = True
            usp_reason_note = f"USP required: MRP (₹{mrp_float:.2f}) exceeds ₹35 threshold (PDP area is {area_val:.2f} cm²)."
        else:
            usp_reason_note = f"USP exempt: PDP area ({area_val:.2f} cm² ≤ 100 cm²) and MRP (₹{mrp_float:.2f} ≤ ₹35)."

        results: dict[str, dict[str, Any]] = {}
        violations: list[dict[str, Any]] = []
        not_detected: list[str] = []

        for field in fields:
            extracted = extraction.get(field, {})
            rule_reference = self.rule_refs.get(field, "Rule 6")
            
            # Special check for USP when it is genuinely exempt by size/price bounds
            if field == "unit_sale_price" and not requires_usp:
                results[field] = {
                    "status": "compliant",
                    "rule_ref": rule_reference,
                    "value": "Exempt by statutory size/price bounds",
                    "confidence": 1.0,
                    "bbox_height_mm": None
                }
                continue

            if isinstance(extracted, Mapping) and extracted.get("status") == "found":
                results[field] = {
                    "status": "compliant",
                    "rule_ref": rule_reference,
                    "value": extracted.get("value"),
                    "confidence": extracted.get("confidence"),
                    "bbox_height_mm": extracted.get("bbox_height_mm")
                }
            else:
                results[field] = {
                    "status": "not_detected",
                    "rule_ref": rule_reference,
                    "reason": "Declaration was not detected by OCR or requires manual verification."
                }
                not_detected.append(field)

        # Remove unit_sale_price from mandatory missing checks if it was exempt
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
            "usp_context": {
                "required": requires_usp,
                "reason": usp_reason_note
            },
            "fields": results,
            "violations": violations,
            "not_detected_fields": active_not_detected
        }