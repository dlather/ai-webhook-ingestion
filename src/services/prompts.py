import json
from collections.abc import Mapping


def build_classification_prompt(raw_json: Mapping[str, object]) -> str:
    payload_str = json.dumps(raw_json, indent=2)
    return f"""You are classifying a vendor webhook payload for a supply chain platform.

Analyze the following JSON payload and classify it as one of:
- SHIPMENT_UPDATE: Contains shipment/logistics data (tracking numbers, delivery status, transit info)
- INVOICE: Contains billing/financial data (invoice IDs, amounts, currency, payment info)
- UNCLASSIFIED: Cannot be clearly identified as either of the above

Payload to classify:
{payload_str}

Return your classification with a confidence score (0.0 to 1.0) and brief reasoning.
Only extract values that clearly exist in the payload. If uncertain, lower your confidence score.
If confidence would be below 0.5, classify as UNCLASSIFIED."""


def build_shipment_extraction_prompt(raw_json: Mapping[str, object]) -> str:
    payload_str = json.dumps(raw_json, indent=2)
    return f"""Extract shipment data from this webhook payload into our standard schema.

Target schema fields:
- vendor_id (string): the vendor or sender identifier
- tracking_number (string): the shipment tracking identifier (REQUIRED, must not be empty)
- status (enum): must be exactly one of: TRANSIT, DELIVERED, EXCEPTION
  - Map "in transit", "in_transit", "shipped" -> TRANSIT
  - Map "delivered", "complete", "completed" -> DELIVERED
  - Map "exception", "failed", "error", "delayed" -> EXCEPTION
- timestamp (ISO 8601 datetime): when this event occurred

Payload:
{payload_str}

CRITICAL RULES:
- Only extract values that clearly exist in the payload above
- Do NOT invent or infer values not present in the payload
- If a required field is missing or unclear, set confidence below 0.7
- tracking_number must not be empty"""


def build_invoice_extraction_prompt(raw_json: Mapping[str, object]) -> str:
    payload_str = json.dumps(raw_json, indent=2)
    return f"""Extract invoice data from this webhook payload into our standard schema.

Target schema fields:
- vendor_id (string): the vendor or sender identifier
- invoice_id (string): the unique invoice identifier
- amount (float): the invoice total amount (must be positive, greater than 0)
- currency (string): 3-letter ISO 4217 currency code in UPPERCASE (e.g., USD, EUR, GBP)

Payload:
{payload_str}

CRITICAL RULES:
- Only extract values that clearly exist in the payload above
- Do NOT invent or infer values not present
- currency must be exactly 3 uppercase letters
- amount must be a positive number greater than 0
- If any required field is missing, set confidence below 0.7"""
