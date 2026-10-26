"""
Transaction scope for fuel KPIs: one partition applied by every analytics endpoint and AI tool.

Every fetched transaction lands in exactly one bucket:
  - matched              resolves to exactly one Wialon unit  -> included in KPIs
  - unmatched            resolves to no Wialon unit            -> excluded, reported
  - ambiguous            resolves to several Wialon units      -> excluded, reported (never guessed)
  - suspected_duplicate  identical fingerprint to a row from a DIFFERENT import -> excluded, reported

Reconciliation invariant (count, litres and cost separately, refunds signed):
  kpi + unmatched + ambiguous + suspected_duplicates == all fetched rows.

Matching rules are those of match_fuel_vehicle_to_wialon; this module applies them consistently, once per
identifier, and groups by Wialon unit id so a unit is never counted twice. A match on the registration number
alone (the unit's plate or name is just the number) cannot verify the emirate/code: it is ambiguous whenever
identifiers with different codes resolve to that unit that way (plate_code_conflict), checked against every
identifier ever imported (registry_fn) so the outcome does not depend on the selected period.
Nothing is deleted: suspected duplicates are only excluded from KPIs and listed for review.
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from app.fuel.db import build_fingerprint
from app.fuel.wialon_distance import (
    MATCH_NUMBER_ONLY,
    match_fuel_vehicle_to_wialon,
    match_fuel_vehicle_with_basis,
    normalize_alphanumeric,
    plate_code_prefix,
    unit_match_keys,
)
from app.utils.datetime_helpers import parse_tx_wallclock

REASON_UNMATCHED = "unmatched"
REASON_AMBIGUOUS = "ambiguous"
REASON_DUPLICATE = "suspected_duplicate"
AMBIGUITY_PLATE_CODE_CONFLICT = "plate_code_conflict"
EXCLUDED_REASONS = (REASON_UNMATCHED, REASON_AMBIGUOUS, REASON_DUPLICATE)

NO_TRANSACTIONS_WARNING = (
    "No fuel transactions in the selected scope; KPIs are empty and Wialon was not queried."
)
NO_MATCHED_WARNING = "No fuel transactions resolve to a single Wialon unit; KPIs are empty."
EMPTY_UNIT_LIST_WARNING = "Wialon returned no units; every transaction is reported as unmatched."

MatchFn = Callable[[str, List[Dict[str, Any]]], Optional[Dict[str, Any]]]


def _dec(val: Any) -> Decimal:
    try:
        return Decimal(str(val)) if val is not None and str(val).strip() != "" else Decimal(0)
    except Exception:
        return Decimal(0)


def _q2(val: Decimal) -> float:
    return float(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _identifier(tx: Dict[str, Any]) -> str:
    return str(tx.get("vehicle_identifier") or "").strip()


class UnitResolver:
    """
    Memoized identifier -> Wialon unit resolution using the matching rules of match_fuel_vehicle_to_wialon.

    A number-only match is accepted only when no other known identifier with a different, non-empty plate code
    resolves number-only to the same unit. Known identifiers are `known_identifiers` (the rows at hand) plus
    `registry_fn()` (every identifier ever imported), read lazily: only the first time a number-only match
    occurs, so fleets matched by exact plate or name never read it. A failing registry_fn propagates.
    """

    def __init__(self, wialon_units: List[Dict[str, Any]], match_fn: Optional[MatchFn] = None,
                 registry_fn: Optional[Callable[[], Iterable[str]]] = None,
                 known_identifiers: Optional[Iterable[str]] = None):
        self.units = wialon_units or []
        self._match = match_fn or match_fuel_vehicle_to_wialon
        self._memo: Dict[str, Dict[str, Any]] = {}
        self._raw_memo: Dict[str, Tuple[Optional[Dict[str, Any]], Optional[str]]] = {}
        self._match_keys = None  # the units' plate/name keys, computed once for the default matcher
        self._registry_fn = registry_fn
        self._known = {str(i).strip() for i in known_identifiers or [] if i and str(i).strip()}
        self._registry: Optional[set] = None  # registry_fn() result, read at most once
        self._conflicts: Optional[Dict[str, List[str]]] = None  # identifier -> conflicting identifiers

    def add_known_identifiers(self, identifiers: Iterable[str]) -> None:
        """Identifiers to check number-only matches against; resolutions are recomputed if the set grows."""
        new = {str(i).strip() for i in identifiers or [] if i and str(i).strip()} - self._known
        if new:
            self._known |= new
            if self._conflicts is not None:
                self._conflicts = None
                self._memo.clear()

    def _raw(self, key: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if key not in self._raw_memo:
            m, basis = None, None
            if key and self.units:
                if self._match is match_fuel_vehicle_to_wialon:
                    if self._match_keys is None:
                        self._match_keys = unit_match_keys(self.units)
                    m, basis = match_fuel_vehicle_with_basis(key, self.units, match_keys=self._match_keys)
                else:
                    m = self._match(key, self.units)
            self._raw_memo[key] = (m, basis)
        return self._raw_memo[key]

    def _conflict_map(self) -> Dict[str, List[str]]:
        """Number-only identifiers whose unit is also reached number-only under a different plate code."""
        if self._conflicts is None:
            if self._registry is None:
                registry = self._registry_fn() if self._registry_fn is not None else []
                self._registry = {str(i).strip() for i in registry or [] if i and str(i).strip()}
            ids = self._known | self._registry
            by_unit: Dict[int, List[str]] = defaultdict(list)
            for ident in ids:
                m, basis = self._raw(ident)
                if basis == MATCH_NUMBER_ONLY and m and m.get("id") is not None:
                    by_unit[int(m["id"])].append(ident)
            conflicts: Dict[str, List[str]] = {}
            for idents in by_unit.values():
                prefixes = {plate_code_prefix(i) for i in idents} - {""}
                if len(prefixes) > 1:
                    for ident in idents:
                        if plate_code_prefix(ident):
                            conflicts[ident] = sorted(idents)
            self._conflicts = conflicts
        return self._conflicts

    def resolve(self, identifier: str) -> Dict[str, Any]:
        """{"status": matched|unmatched|ambiguous, "unit_id", "unit_name", "unit", "candidates", "ambiguity", "basis"}."""
        key = str(identifier or "").strip()
        if key in self._memo:
            return self._memo[key]
        res: Dict[str, Any] = {"status": REASON_UNMATCHED, "unit_id": None, "unit_name": None, "unit": None,
                               "candidates": [], "ambiguity": None, "basis": None}
        m, basis = self._raw(key)
        if m and m.get("ambiguous"):
            res["status"] = REASON_AMBIGUOUS
            res["candidates"] = sorted({str(c.get("nm", "")).strip() for c in m.get("candidates") or [] if c})
        elif m and m.get("id") is not None:
            if basis == MATCH_NUMBER_ONLY:
                self.add_known_identifiers([key])
                conflicting = self._conflict_map().get(key)
                if conflicting:
                    res.update(status=REASON_AMBIGUOUS, candidates=[str(m.get("nm", "")).strip()],
                               ambiguity=AMBIGUITY_PLATE_CODE_CONFLICT, conflicting_identifiers=conflicting)
                    self._memo[key] = res
                    return res
            res.update(status="matched", unit_id=int(m["id"]), unit_name=str(m.get("nm", "")).strip(), unit=m,
                       basis=basis)
        self._memo[key] = res
        return res


@dataclass
class ScopePartition:
    matched: List[Dict[str, Any]] = field(default_factory=list)
    unmatched: List[Dict[str, Any]] = field(default_factory=list)
    ambiguous: List[Dict[str, Any]] = field(default_factory=list)
    suspected_duplicates: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def excluded(self) -> List[Dict[str, Any]]:
        return self.unmatched + self.ambiguous + self.suspected_duplicates

    def all_rows(self) -> List[Dict[str, Any]]:
        return self.matched + self.excluded()


def _row_fingerprint(tx: Dict[str, Any]) -> str:
    return build_fingerprint(
        tx.get("vehicle_identifier"), tx.get("transaction_date"), tx.get("litres"),
        tx.get("total_cost"), tx.get("invoice_number"), tx.get("odometer_km"),
    )


def _suspected_duplicate_flags(transactions: List[Dict[str, Any]]) -> List[bool]:
    """
    True for rows whose fingerprint equals a row from a different import_id. Per fingerprint the
    lowest id (else first fetched) is kept; rows from the keeper's own import are kept as well,
    because the importer already de-duplicates within one file.
    """
    groups: Dict[str, List[int]] = {}
    for i, tx in enumerate(transactions):
        groups.setdefault(_row_fingerprint(tx), []).append(i)
    flags = [False] * len(transactions)
    for idxs in groups.values():
        if len(idxs) < 2:
            continue

        def order(i: int) -> Tuple[int, Any, int]:
            tid = transactions[i].get("id")
            return (0, int(tid), i) if isinstance(tid, (int, float)) or str(tid or "").isdigit() else (1, 0, i)

        keeper = min(idxs, key=order)
        keep_import = transactions[keeper].get("import_id")
        for i in idxs:
            if i != keeper and transactions[i].get("import_id") != keep_import:
                flags[i] = True
    return flags


def partition_transactions(transactions: List[Dict[str, Any]], resolver: UnitResolver) -> ScopePartition:
    """Split rows into exactly one bucket each. Matched rows are copies tagged with _unit_id/_unit_name."""
    part = ScopePartition()
    if transactions and not resolver.units:
        part.warnings.append(EMPTY_UNIT_LIST_WARNING)
    resolver.add_known_identifiers(_identifier(tx) for tx in transactions or [])
    dup_flags = _suspected_duplicate_flags(transactions or [])
    for tx, is_dup in zip(transactions or [], dup_flags):
        res = resolver.resolve(_identifier(tx))
        if is_dup:
            part.suspected_duplicates.append(dict(tx, _exclusion_reason=REASON_DUPLICATE))
        elif res["status"] == "matched":
            part.matched.append(dict(tx, _unit_id=res["unit_id"], _unit_name=res["unit_name"]))
        elif res["status"] == REASON_AMBIGUOUS:
            part.ambiguous.append(dict(tx, _exclusion_reason=REASON_AMBIGUOUS, _candidates=res["candidates"],
                                       _ambiguity=res.get("ambiguity")))
        else:
            part.unmatched.append(dict(tx, _exclusion_reason=REASON_UNMATCHED))
    if transactions and not part.matched:
        part.warnings.append(NO_MATCHED_WARNING)
    return part


def _totals(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    litres = sum((_dec(r.get("litres")) for r in rows), Decimal(0))
    cost = sum((_dec(r.get("total_cost")) for r in rows), Decimal(0))
    refunds = [r for r in rows if _dec(r.get("litres")) < 0]
    return {
        "transaction_count": len(rows),
        "litres": _q2(litres),
        "cost": _q2(cost),
        "refund_count": len(refunds),
        "refund_litres": _q2(sum((_dec(r.get("litres")) for r in refunds), Decimal(0))),
        "refund_cost": _q2(sum((_dec(r.get("total_cost")) for r in refunds), Decimal(0))),
        "identifier_count": len({_identifier(r) for r in rows}),
    }


def merged_unit_identifiers(matched: List[Dict[str, Any]]) -> Dict[int, List[str]]:
    """Wialon units that two or more distinct fuel identifiers resolve to."""
    by_unit: Dict[int, set] = {}
    for r in matched:
        if r.get("_unit_id") is not None:
            by_unit.setdefault(int(r["_unit_id"]), set()).add(_identifier(r))
    return {uid: sorted(ids) for uid, ids in by_unit.items() if len(ids) > 1}


def build_excluded_scope(part: ScopePartition, extra_warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    """Reconciliation block attached to every KPI response."""
    kpi = _totals(part.matched)
    kpi["unit_count"] = len({r["_unit_id"] for r in part.matched if r.get("_unit_id") is not None})
    unmatched = _totals(part.unmatched)
    ambiguous = _totals(part.ambiguous)
    dups = _totals(part.suspected_duplicates)
    every = _totals(part.all_rows())
    excluded = _totals(part.excluded())
    names = {r["_unit_id"]: r.get("_unit_name") for r in part.matched}
    merged = [
        {"wialon_unit_id": uid, "wialon_unit_name": names.get(uid), "identifiers": ids}
        for uid, ids in sorted(merged_unit_identifiers(part.matched).items())
    ]
    warnings = list(part.warnings) + list(extra_warnings or [])
    if merged:
        warnings.append(
            f"{len(merged)} Wialon unit(s) have fuel under more than one identifier; their fuel is combined "
            "and their distance counted once."
        )
    return {
        "kpi": kpi,
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "suspected_duplicates": dups,
        "excluded_transaction_count": excluded["transaction_count"],
        "excluded_litres": excluded["litres"],
        "excluded_cost": excluded["cost"],
        "all_transaction_count": every["transaction_count"],
        "all_litres": every["litres"],
        "all_cost": every["cost"],
        "merged_units": merged,
        "warnings": warnings,
    }


# ─── Exact vehicle filter ──────────────────────────────────────────────────────

def coarse_vehicle_prefilter(value: Optional[str]) -> Optional[str]:
    """
    Value for the database ilike pre-filter. The trailing plate digits are used so every identifier
    format of the same plate is fetched ('7-AUH-55943', '7 AUH 55943'); exactness is enforced in memory.
    """
    if not value or not str(value).strip():
        return None
    v = str(value).strip()
    digits = re.search(r"(\d{3,})$", v)
    # Without >= 3 trailing digits a substring would miss other spellings of the same unit ("Bus 7" vs
    # "BUS7"), so nothing is pre-filtered and the exact filter runs in memory over every row.
    return digits.group(1) if digits else None


def filter_by_vehicle(
    transactions: List[Dict[str, Any]], value: Optional[str], resolver: UnitResolver
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Keep rows for exactly the requested vehicle: rows resolving to the same Wialon unit as the filter,
    plus rows whose normalized identifier equals the filter (so an unmatched/ambiguous plate is still
    reported in the excluded scope). Substrings never match: '7-AUH-5594' does not select '17-AUH-55943'.
    """
    if not value or not str(value).strip():
        return list(transactions or []), []
    target = resolver.resolve(str(value).strip())
    target_norm = normalize_alphanumeric(value)
    warnings: List[str] = []
    if target.get("ambiguity") == AMBIGUITY_PLATE_CODE_CONFLICT:
        warnings.append(
            f"Vehicle filter '{value}' matches Wialon unit {', '.join(target['candidates'])} by registration number "
            "only, and other plate codes share that number; only rows with exactly this identifier are shown, as ambiguous."
        )
    elif target["status"] == REASON_AMBIGUOUS:
        warnings.append(
            f"Vehicle filter '{value}' matches several Wialon units ({', '.join(target['candidates'])}); "
            "only rows with exactly this identifier are shown, as ambiguous."
        )
    kept = []
    for tx in transactions or []:
        ident = _identifier(tx)
        if target["status"] == "matched":
            res = resolver.resolve(ident)
            if res["status"] == "matched" and res["unit_id"] == target["unit_id"]:
                kept.append(tx)
                continue
        if target_norm and normalize_alphanumeric(ident) == target_norm:
            kept.append(tx)
    return kept, warnings


# ─── Effective dates ───────────────────────────────────────────────────────────

def tx_local_date(value: Any) -> Optional[str]:
    parsed = parse_tx_wallclock(value)
    return parsed[0].strftime("%Y-%m-%d") if parsed else None


def resolve_effective_dates(
    transactions: List[Dict[str, Any]], date_from: Optional[str], date_to: Optional[str]
) -> Optional[Tuple[str, str]]:
    """
    Requested dates, completed from the transactions' own dates. None when the range cannot be known
    (no requested bound and no dated transactions): callers return empty KPIs and skip Wialon.
    There is no hardcoded fallback period.
    """
    if date_from and date_to:
        return date_from, date_to
    dates = sorted(d for d in (tx_local_date(t.get("transaction_date")) for t in transactions or []) if d)
    if not dates:
        return None
    return date_from or dates[0], date_to or dates[-1]


# ─── Scope context (shared by routes and AI tools) ─────────────────────────────

def _fuel_type_matches(tx: Dict[str, Any], fuel_type: str) -> bool:
    # Same semantics as the database filter (case-insensitive substring)
    return fuel_type.strip().lower() in str(tx.get("fuel_type") or "").lower()


def mixed_fuel_units(matched: List[Dict[str, Any]]) -> set:
    """Units that bought more than one fuel type in the scope (their distance cannot be split by fuel type)."""
    types: Dict[int, set] = {}
    for r in matched:
        if r.get("_unit_id") is not None:
            types.setdefault(int(r["_unit_id"]), set()).add(str(r.get("fuel_type") or "UNKNOWN").strip().upper())
    return {uid for uid, t in types.items() if len(t) > 1}


def apply_fuel_type(part: ScopePartition, fuel_type: Optional[str]) -> Tuple[ScopePartition, set]:
    """
    Filter every bucket by fuel type in memory. Returns the filtered partition and, when a filter is set, the
    units that also bought other fuel types in the period (mixed): efficiency must not attribute their full
    distance to the filtered fuel.
    """
    if not fuel_type or not str(fuel_type).strip():
        return part, set()
    mixed = mixed_fuel_units(part.matched)
    keep = lambda rows: [r for r in rows if _fuel_type_matches(r, fuel_type)]
    filtered = ScopePartition(keep(part.matched), keep(part.unmatched), keep(part.ambiguous),
                              keep(part.suspected_duplicates), list(part.warnings))
    in_scope = {r["_unit_id"] for r in filtered.matched}
    return filtered, mixed & in_scope


@dataclass
class ScopeContext:
    partition: ScopePartition
    resolver: UnitResolver
    sid: Optional[str]
    excluded_scope: Dict[str, Any]
    warnings: List[str]
    fetched_count: int
    mixed_fuel_unit_ids: set = field(default_factory=set)

    @property
    def matched(self) -> List[Dict[str, Any]]:
        return self.partition.matched

    @property
    def units(self) -> List[Dict[str, Any]]:
        return self.resolver.units

    @property
    def is_empty(self) -> bool:
        return not self.partition.matched


def build_scope(
    filters: Dict[str, Any],
    fetch_fn: Callable[[Dict[str, Any]], List[Dict[str, Any]]],
    login_fn: Callable[[], str],
    units_fn: Callable[[str], List[Dict[str, Any]]],
    registry_fn: Optional[Callable[[], Iterable[str]]] = None,
) -> ScopeContext:
    """
    fetch -> exact vehicle filter -> partition -> fuel-type filter. Wialon (login + unit list) is only
    contacted when there are fetched transactions to classify. Callers inject their fetch/login/unit
    functions so each module's own imports (and test patches) are used. The fuel-type filter is applied in
    memory (all fuel types are fetched) so mixed-fuel units can be detected. `registry_fn` (every identifier ever
    imported) is read only if a number-only match occurs; without it, conflicts are checked among fetched rows.
    """
    vehicle_value = filters.get("vehicle_identifier")
    fuel_type = filters.get("fuel_type")
    db_filters = dict(filters)
    db_filters["vehicle_identifier"] = coarse_vehicle_prefilter(vehicle_value)
    db_filters["fuel_type"] = None
    raw = fetch_fn(db_filters) or []
    if not raw:
        empty = ScopePartition()
        return ScopeContext(empty, UnitResolver([]), None, build_excluded_scope(empty, [NO_TRANSACTIONS_WARNING]),
                            [NO_TRANSACTIONS_WARNING], 0)
    sid = login_fn()
    resolver = UnitResolver(units_fn(sid), registry_fn=registry_fn, known_identifiers=(_identifier(t) for t in raw))
    rows, filter_warnings = filter_by_vehicle(raw, vehicle_value, resolver)
    if not rows:
        empty = ScopePartition()
        warn = [f"No fuel transactions match vehicle '{vehicle_value}' exactly."] + filter_warnings
        return ScopeContext(empty, resolver, sid, build_excluded_scope(empty, warn), warn, len(raw))
    part, mixed = apply_fuel_type(partition_transactions(rows, resolver), fuel_type)
    if fuel_type and not part.all_rows():
        part.warnings.append(f"No fuel transactions of type '{fuel_type}' in the selected scope.")
    scope = build_excluded_scope(part, filter_warnings)
    return ScopeContext(part, resolver, sid, scope, list(scope["warnings"]), len(raw), mixed)


def summarize_excluded(
    part: ScopePartition, reason: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Excluded rows grouped by (reason, identifier) for the excluded-transactions list."""
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in part.excluded():
        rsn = r.get("_exclusion_reason") or REASON_UNMATCHED
        if reason and rsn != reason:
            continue
        key = (rsn, _identifier(r))
        g = groups.setdefault(key, {
            "vehicle_identifier": key[1], "reason": rsn, "candidate_unit_names": list(r.get("_candidates") or []),
            "ambiguity_reason": r.get("_ambiguity"),
            "transaction_count": 0, "_l": Decimal(0), "_c": Decimal(0), "first_date": None, "last_date": None,
            "transaction_ids": [],
        })
        g["transaction_count"] += 1
        g["_l"] += _dec(r.get("litres"))
        g["_c"] += _dec(r.get("total_cost"))
        d = tx_local_date(r.get("transaction_date"))
        if d:
            g["first_date"] = min(filter(None, [g["first_date"], d]))
            g["last_date"] = max(filter(None, [g["last_date"], d]))
        if r.get("id") is not None and len(g["transaction_ids"]) < 50:
            g["transaction_ids"].append(r.get("id"))
    out = []
    for g in groups.values():
        g["litres"] = _q2(g.pop("_l"))
        g["cost"] = _q2(g.pop("_c"))
        out.append(g)
    out.sort(key=lambda g: (-abs(g["litres"]), g["reason"], g["vehicle_identifier"]))
    return out


def unit_key(tx: Dict[str, Any], resolver: Optional[UnitResolver] = None) -> Tuple[str, Any]:
    """Grouping key: the Wialon unit id when the row is (or resolves as) matched, else its identifier."""
    if tx.get("_unit_id") is not None:
        return ("unit", int(tx["_unit_id"]))
    if tx.get("_exclusion_reason"):
        # Excluded by the scope partition (e.g. a plate-code conflict): never re-resolved into a unit
        return ("id", _identifier(tx))
    if resolver is not None:
        res = resolver.resolve(_identifier(tx))
        if res["status"] == "matched":
            return ("unit", res["unit_id"])
    return ("id", _identifier(tx))
