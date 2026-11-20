"""Heuristic billing-metadata extraction used to pre-fill the fields that the
validation rules V2 (overlap) and V3 (near duplicate) need. The user can
correct every value on the Upload step; corrections are audit-logged."""

import datetime
import re
from typing import Any, Dict, List, Optional

from dateutil import parser as date_parser

_DATE = r"(\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"


def _date(text: str) -> Optional[datetime.date]:
    try:
        return date_parser.parse(text, dayfirst=not re.match(r"\d{4}-", text)).date()
    except (ValueError, OverflowError):
        return None


def extract_billing_metadata(pages: List[Dict[str, Any]], extracted_categories: List[str]) -> Dict[str, Any]:
    text = "\n".join(page.get("text", "") for page in pages)
    meta: Dict[str, Any] = {}

    period = re.search(rf"(?:billing\s*period|bill\s*period|period)\s*[:\-]?\s*{_DATE}\s*(?:to|-|–|until)\s*{_DATE}",
                       text, re.IGNORECASE)
    if period:
        meta["period_start"] = _date(period.group(1))
        meta["period_end"] = _date(period.group(2))

    bill_no = re.search(r"(?:bill|invoice)\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{2,})", text, re.IGNORECASE)
    if bill_no:
        meta["bill_number"] = bill_no.group(1).strip()

    bill_date = re.search(rf"(?:bill|invoice)\s*date\s*[:\-]?\s*{_DATE}", text, re.IGNORECASE)
    if bill_date:
        meta["bill_date"] = _date(bill_date.group(1))

    account = re.search(r"(?:consumer|account|meter|ca)\s*(?:no\.?|number|id)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{3,})",
                        text, re.IGNORECASE)
    if account:
        meta["account_number"] = account.group(1).strip()

    supplier = re.search(r"(?:supplier|vendor|discom|utility|sold\s*by)\s*[:\-]\s*([^\n]{3,80})", text, re.IGNORECASE)
    if supplier:
        meta["supplier"] = supplier.group(1).strip()

    if "scope2_electricity" in extracted_categories:
        meta["utility_type"] = "electricity"
    elif "scope1_fuel" in extracted_categories:
        meta["utility_type"] = "fuel"
    elif "precursor" in extracted_categories:
        meta["utility_type"] = "precursor"
    elif "production" in extracted_categories:
        meta["utility_type"] = "production"
    return {k: v for k, v in meta.items() if v is not None}
