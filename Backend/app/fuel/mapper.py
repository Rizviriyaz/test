"""Header normalization and dynamic column mapping for fuel imports."""

import re
from typing import Dict, List, Optional, Set, Tuple
from app.fuel.schemas import HeaderMappingItem

# Canonical field definitions for fuel_transactions
CANONICAL_FIELDS: Dict[str, Dict[str, any]] = {
    "transaction_date": {
        "required": True,
        "description": "Date and time when the fuel transaction occurred",
        "aliases": [
            "date", "transaction date", "transaction_date", "fuel date", "purchase date",
            "date of transaction", "txn date", "tx date", "filling date", "datetime",
            "date time", "date/time", "trans date", "entry date", "invoice date"
        ],
    },
    "vehicle_identifier": {
        "required": True,
        "description": "Vehicle registration plate, unit name, or vehicle number",
        "aliases": [
            "vehicle", "vehicle no", "vehicle number", "vehicle id", "vehicle_id",
            "plate", "plate no", "plate number", "registration", "registration no",
            "registration number", "reg no", "reg number", "asset", "asset id",
            "unit", "unit name", "unit id", "truck", "car", "vehicle name", "equipment"
        ],
    },
    "litres": {
        "required": True,
        "description": "Fuel volume in litres",
        "aliases": [
            "litre", "litres", "liter", "liters", "quantity", "qty", "fuel quantity",
            "fuel qty", "volume", "fuel volume", "litres (l)", "qty (ltr)", "qty ltr",
            "liters (l)", "dispensed qty", "fuel amount (litres)"
        ],
    },
    "fuel_type": {
        "required": False,
        "description": "Type of fuel (e.g. Diesel, Special 95, Super 98)",
        "aliases": [
            "fuel type", "fuel", "product", "product name", "fuel category",
            "item", "grade", "fuel grade", "diesel / petrol", "fuel description", "fuel name"
        ],
    },
    "unit_price": {
        "required": False,
        "description": "Price per litre",
        "aliases": [
            "price", "unit price", "price per litre", "rate", "fuel rate",
            "rate per litre", "price/ltr", "unit rate", "cost per litre",
            "rate/ltr", "price/l", "unit cost"
        ],
    },
    "total_cost": {
        "required": False,
        "description": "Total amount / transaction cost",
        "aliases": [
            "total", "total cost", "amount", "total amount", "net amount",
            "grand total", "cost", "total price", "fuel cost", "value",
            "total value", "gross amount", "amt", "total (aed)", "amount (aed)"
        ],
    },
    "odometer_km": {
        "required": False,
        "description": "Odometer reading in kilometers at time of refuel",
        "aliases": [
            "odometer", "odometer km", "mileage", "km", "odometer reading",
            "curr odometer", "current odometer", "odo", "odo reading", "odo km",
            "distance km", "current km", "meter reading"
        ],
    },
    "station_name": {
        "required": False,
        "description": "Fuel station name or outlet location",
        "aliases": [
            "station", "station name", "fuel station", "outlet", "location",
            "gas station", "petrol station", "site", "site name", "merchant",
            "vendor", "service station", "station / location"
        ],
    },
    "invoice_number": {
        "required": False,
        "description": "Invoice, bill, or receipt number",
        "aliases": [
            "invoice", "invoice no", "invoice number", "bill no", "receipt no",
            "receipt number", "voucher no", "invoice #", "bill number", "receipt #"
        ],
    },
    "card_number": {
        "required": False,
        "description": "Fuel card or RFID tag number",
        "aliases": [
            "card", "card no", "card number", "fuel card", "card id", "card #",
            "tag no", "tag number", "card code", "fuel card no"
        ],
    },
    "driver_name": {
        "required": False,
        "description": "Driver or operator name",
        "aliases": [
            "driver", "driver name", "operator", "driver id", "driver full name",
            "personnel", "employee name", "user"
        ],
    },
    "reference_number": {
        "required": False,
        "description": "Transaction reference or authorization code",
        "aliases": [
            "reference", "reference no", "reference number", "transaction reference",
            "ref no", "ref #", "txn id", "transaction id", "trans id", "auth code",
            "auth no", "approval code"
        ],
    },
    "notes": {
        "required": False,
        "description": "Additional remarks or comments",
        "aliases": [
            "notes", "note", "remarks", "remark", "comments", "comment",
            "description", "memo"
        ],
    },
    "transaction_type": {
        "required": False,
        "description": "Statement entry type (e.g. Purchase, Payment, ServiceCharge); used to skip non-fuel entries, not stored",
        "aliases": [
            "transaction type", "txn type", "trans type", "entry type", "transaction category"
        ],
    },
}

REQUIRED_FIELDS = [k for k, v in CANONICAL_FIELDS.items() if v["required"]]


def normalize_header(header: str) -> str:
    """
    Normalize header text for comparison:
    - Lowercase and trim
    - Replace punctuation, underscores, dashes, and slashes with spaces
    - Remove common unit qualifiers like (aed), (ltr), (km)
    - Collapse repeated whitespace
    """
    if not header:
        return ""
    text = str(header).strip().lower()
    # Remove unit annotations in parentheses e.g. (ltr), (aed), (km), [l]
    text = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", text)
    # Replace symbols and delimiters with space
    text = re.sub(r"[_\-\/\.,:;*#\\]+", " ", text)
    # Collapse multiple spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


def map_headers(detected_headers: List[str]) -> Tuple[Dict[str, Optional[str]], List[HeaderMappingItem], List[str], List[str]]:
    """
    Map detected Excel headers to canonical database fields.
    Returns:
      - suggested_mapping: Dict[canonical_field -> original_excel_header]
      - mapping_details: List[HeaderMappingItem]
      - unmapped_columns: List[str]
      - missing_required_fields: List[str]
    """
    suggested_mapping: Dict[str, Optional[str]] = {k: None for k in CANONICAL_FIELDS}
    mapping_details: List[HeaderMappingItem] = []
    used_headers: Set[str] = set()

    # Pre-normalize detected headers
    header_lookup: List[Tuple[str, str]] = [
        (orig, normalize_header(orig))
        for orig in detected_headers
        if orig and orig.strip()
    ]

    # Pass 1: Exact alias match
    for canonical, info in CANONICAL_FIELDS.items():
        if suggested_mapping[canonical] is not None:
            continue
        canonical_norm = normalize_header(canonical)
        aliases = [normalize_header(a) for a in info["aliases"]]

        for orig_header, norm_header in header_lookup:
            if orig_header in used_headers:
                continue
            if norm_header == canonical_norm or norm_header in aliases:
                suggested_mapping[canonical] = orig_header
                used_headers.add(orig_header)
                mapping_details.append(
                    HeaderMappingItem(
                        canonical_field=canonical,
                        excel_header=orig_header,
                        confidence="exact" if norm_header == canonical_norm else "alias",
                        required=info["required"],
                    )
                )
                break

    # Pass 2: Conservative containment match for unmapped fields
    for canonical, info in CANONICAL_FIELDS.items():
        if suggested_mapping[canonical] is not None:
            continue
        canonical_norm = normalize_header(canonical)
        aliases = [normalize_header(a) for a in info["aliases"]]

        for orig_header, norm_header in header_lookup:
            if orig_header in used_headers:
                continue
            # Conservative check: whole token matching
            matched = False
            for alias in aliases:
                # Check if alias appears as a distinct word/phrase
                pattern = r"\b" + re.escape(alias) + r"\b"
                if re.search(pattern, norm_header):
                    suggested_mapping[canonical] = orig_header
                    used_headers.add(orig_header)
                    mapping_details.append(
                        HeaderMappingItem(
                            canonical_field=canonical,
                            excel_header=orig_header,
                            confidence="fuzzy",
                            required=info["required"],
                        )
                    )
                    matched = True
                    break
            if matched:
                break

    # Identify unmapped fields
    for canonical, info in CANONICAL_FIELDS.items():
        if suggested_mapping[canonical] is None:
            mapping_details.append(
                HeaderMappingItem(
                    canonical_field=canonical,
                    excel_header=None,
                    confidence="none",
                    required=info["required"],
                )
            )

    unmapped_columns = [orig for orig, _ in header_lookup if orig not in used_headers]
    missing_required_fields = [
        canonical
        for canonical in REQUIRED_FIELDS
        if suggested_mapping[canonical] is None
    ]

    return suggested_mapping, mapping_details, unmapped_columns, missing_required_fields


# Headers that name a price without saying whether it is per litre or the transaction total ('Rate' is per litre
# and 'Amount' the total on fuel statements, so they are not listed)
GENERIC_PRICE_HEADERS: Dict[str, Set[str]] = {
    "unit_price": {"price"},
    "total_cost": {"cost", "value", "total"},
}
PRICE_FIELDS = ("unit_price", "total_cost")


def date_header_candidates(detected_headers: List[str]) -> List[str]:
    """Headers that could hold the transaction date: a transaction_date alias, or any header with the word 'date'."""
    aliases = {normalize_header(a) for a in CANONICAL_FIELDS["transaction_date"]["aliases"]}
    out = []
    for h in detected_headers:
        norm = normalize_header(h)
        if norm and (norm in aliases or re.search(r"\bdate\b", norm)):
            out.append(h)
    return out


def ambiguous_mapping_fields(
    detected_headers: List[str],
    mapping: Dict[str, Optional[str]],
    mapping_details: List[HeaderMappingItem],
) -> List[str]:
    """
    Fields whose mapping must be confirmed explicitly before an import:
      transaction_date  mapped by the fuzzy pass, several headers could be the date, or no date is mapped although a
                        header looks like one (the user must confirm the statement has no date column)
      unit_price,       mapped by the fuzzy pass, or mapped to a generic header ('Price', 'Amount', ...) that could be
      total_cost        either the per-litre price or the total (the importer clears this when both are mapped and
                        every row agrees: total = litres x unit price)
    """
    fuzzy = {d.canonical_field: d.excel_header for d in mapping_details if d.confidence == "fuzzy" and d.excel_header}
    need: List[str] = []
    date_hdr = mapping.get("transaction_date")
    candidates = date_header_candidates(detected_headers)
    if date_hdr:
        if fuzzy.get("transaction_date") == date_hdr or len(candidates) > 1:
            need.append("transaction_date")
    elif candidates:
        need.append("transaction_date")
    for field in PRICE_FIELDS:
        hdr = mapping.get(field)
        if hdr and (fuzzy.get(field) == hdr or normalize_header(hdr) in GENERIC_PRICE_HEADERS[field]):
            need.append(field)
    return need


# Fields an import cannot do without: a wrong column among repeated ones would misstate fuel, cost or vehicle
REPEATED_GROUP_FIELDS = ("transaction_date", "vehicle_identifier", "litres", "unit_price", "total_cost")


def repeated_header_fields(detected_headers: List[str], mapping: Dict[str, Optional[str]]) -> List[str]:
    """
    Fields mapped to a column whose title is shared with another column once units and column labels are ignored
    ('Amount (col F)' / 'Amount (col J)', 'Litres' / 'Litres (L)'): the statement repeats a column group, so the
    user must confirm which column is meant instead of the first one being taken silently.
    """
    counts: Dict[str, int] = {}
    for h in detected_headers:
        norm = normalize_header(h)
        if norm:
            counts[norm] = counts.get(norm, 0) + 1
    return [f for f in REPEATED_GROUP_FIELDS if mapping.get(f) and counts.get(normalize_header(mapping[f]), 0) > 1]
