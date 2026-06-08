from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DB_NAME = "Zenith_Materjalibaas.sqlite"

INTENT_RULES: dict[str, dict[str, Any]] = {
    "oilfuel": {
        "apps": {"oilfuel"},
        "tags": {"oil_fuel_resistance"},
        "materials": {"nbr", "fkm", "nbr_pvc", "cr"},
        "avoid_materials": {"epdm", "sbr", "nr"},
    },
    "weather_uv": {
        "apps": {"weather_uv"},
        "tags": {"uv_weather_resistance"},
        "materials": {"epdm", "csm", "silicone", "fkm", "cr"},
        "avoid_materials": set(),
    },
    "abrasion_wear": {
        "apps": {"abrasion_wear"},
        "tags": {"abrasion_resistance"},
        "materials": {"nr", "sbr"},
        "avoid_materials": set(),
    },
    "lumelukkamine": {
        "apps": {"abrasion_wear"},
        "tags": {"abrasion_resistance"},
        "materials": {"nr", "sbr"},
        "avoid_materials": {"epdm"},
    },
    "food_contact": {
        "apps": {"food_contact"},
        "tags": {"food_grade"},
        "materials": {"silicone", "epdm", "nbr", "cr"},
        "avoid_materials": set(),
    },
    "high_temperature": {
        "apps": {"high_temperature"},
        "tags": {"high_temperature"},
        "materials": {"silicone", "fkm", "epdm", "csm"},
        "avoid_materials": set(),
    },
    "low_temperature": {
        "apps": set(),
        "tags": set(),
        "materials": {"silicone", "epdm", "nr"},
        "avoid_materials": set(),
    },
    "chemical": {
        "apps": set(),
        "tags": {"chemical_resistance", "chemical_resistance_text"},
        "materials": {"fkm", "nbr", "epdm", "cr"},
        "avoid_materials": set(),
    },
    "seal_general": {
        "apps": set(),
        "tags": set(),
        "materials": set(),
        "avoid_materials": set(),
    },
    "hose_general": {
        "apps": set(),
        "tags": set(),
        "materials": set(),
        "avoid_materials": set(),
    },
}

MATERIAL_INTENTS = {
    "material_sbr": "sbr",
    "material_nbr": "nbr",
    "material_epdm": "epdm",
    "material_fkm": "fkm",
    "material_silicone": "silicone",
    "material_cr": "cr",
    "material_nr": "nr",
}

DIRECT_TERMS = {
    "lumesahk": "lumelukkamine",
    "sahk": "lumelukkamine",
    "lume sahk": "lumelukkamine",
    "snow plow": "lumelukkamine",
    "snow blade": "lumelukkamine",
    "oli": "oilfuel",
    "lipaagi": "oilfuel",
    "olipaagi": "oilfuel",
    "kutus": "oilfuel",
    "bensiin": "oilfuel",
    "diisel": "oilfuel",
    "uv": "weather_uv",
    "osoon": "weather_uv",
    "ilmastik": "weather_uv",
    "kulum": "abrasion_wear",
    "kulumine": "abrasion_wear",
    "kulumiskindel": "abrasion_wear",
    "food": "food_contact",
    "fda": "food_contact",
    "toiduklass": "food_contact",
    "kuum": "high_temperature",
    "korge temperatuur": "high_temperature",
    "kylm": "low_temperature",
    "kulm": "low_temperature",
    "kemikaal": "chemical",
    "keemia": "chemical",
}


@dataclass
class ParsedQuery:
    query: str
    normalized_query: str
    tokens: list[str]
    intents: set[str]
    required_materials: set[str]
    service_temp_c: float | None
    hardness: float | None
    thickness_mm: float | None


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.lower().replace(chr(176), " ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_codes(value: Any) -> set[str]:
    text = "" if value is None else str(value)
    return {part.strip() for part in re.split(r"[;,]", text) if part and part.strip()}


def load_database(db_path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    root = Path(__file__).resolve().parent
    db = db_path or root / DB_NAME
    if not db.exists():
        alt = root / "data" / DB_NAME
        if alt.exists():
            db = alt
    if not db.exists():
        raise FileNotFoundError(f"Andmebaasi ei leitud: {db}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            "products": "select * from assistant_product_index order by product_name",
            "variants": "select * from product_variants order by product_name, thickness_mm_text",
            "synonyms": "select * from search_synonyms order by length(term) desc",
            "materials": "select * from materials order by material_code",
            "needs_review": "select * from needs_review order by priority, topic",
        }
        return {name: [dict(row) for row in conn.execute(sql)] for name, sql in tables.items()}
    finally:
        conn.close()


def parse_query(query: str, synonyms: list[dict[str, Any]]) -> ParsedQuery:
    normalized = normalize_text(query)
    tokens = re.findall(r"[a-z0-9_/-]+", normalized)
    intents: set[str] = set()
    required_materials: set[str] = set()

    for item in synonyms:
        term = normalize_text(item.get("term"))
        normalized_value = str(item.get("normalized") or "").strip()
        comparable_value = normalize_text(normalized_value)
        if term and term in normalized:
            if normalized_value in MATERIAL_INTENTS:
                required_materials.add(MATERIAL_INTENTS[normalized_value])
            elif comparable_value in MATERIAL_INTENTS:
                required_materials.add(MATERIAL_INTENTS[comparable_value])
            else:
                intents.add(normalized_value)
                intents.add(comparable_value)

    for term, intent in DIRECT_TERMS.items():
        if normalize_text(term) in normalized:
            intents.add(intent)

    for material in ["sbr", "nbr", "epdm", "fkm", "cr", "nr", "silicone", "silikon", "csm", "butyl"]:
        if re.search(rf"(^|\W){re.escape(material)}($|\W)", normalized):
            required_materials.add("silicone" if material == "silikon" else material)

    service_temp = None
    for pattern in [
        r"(-?\d+(?:[\.,]\d+)?)\s*(?:c|kraadi)",
        r"(?:temp|temperatuur)[^\d-]*(-?\d+(?:[\.,]\d+)?)",
        r"\+\s*(\d+(?:[\.,]\d+)?)",
    ]:
        match = re.search(pattern, normalized)
        if match:
            service_temp = float(match.group(1).replace(",", "."))
            break

    hardness = None
    match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:shore a|shore|sh|kovad)", normalized)
    if match:
        hardness = float(match.group(1).replace(",", "."))

    thickness = None
    match = re.search(r"(\d+(?:[\.,]\d+)?)\s*mm", normalized)
    if match:
        thickness = float(match.group(1).replace(",", "."))

    return ParsedQuery(
        query=query,
        normalized_query=normalized,
        tokens=tokens,
        intents={intent for intent in intents if intent},
        required_materials=required_materials,
        service_temp_c=service_temp,
        hardness=hardness,
        thickness_mm=thickness,
    )


def thickness_matches(thickness_text: Any, requested_mm: float | None) -> bool:
    if requested_mm is None:
        return True
    text = normalize_text(thickness_text).replace(",", ".")
    numbers = [float(match) for match in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return False
    if "-" in text and len(numbers) >= 2:
        return min(numbers[0], numbers[1]) <= requested_mm <= max(numbers[0], numbers[1])
    return any(abs(number - requested_mm) <= 0.01 for number in numbers)


def variant_thickness_matches(variants: list[dict[str, Any]], product_id: str, requested_mm: float | None) -> bool:
    if requested_mm is None:
        return True
    product_variants = [row for row in variants if row.get("product_id") == product_id]
    if not product_variants:
        return False
    return any(thickness_matches(row.get("thickness_mm_text"), requested_mm) for row in product_variants)


def build_text_blob(row: dict[str, Any]) -> str:
    fields = [
        "product_name",
        "article_code",
        "material_code",
        "material_name",
        "material_group",
        "application_categories",
        "property_tags",
        "feature_text",
        "color",
    ]
    return normalize_text(" ".join(str(row.get(field) or "") for field in fields))


def add_ui_requirements(
    parsed: ParsedQuery,
    required_materials: list[str] | None = None,
    required_intents: list[str] | None = None,
    service_temp_c: float | None = None,
    hardness: float | None = None,
    thickness_mm: float | None = None,
) -> ParsedQuery:
    return ParsedQuery(
        query=parsed.query,
        normalized_query=parsed.normalized_query,
        tokens=parsed.tokens,
        intents=set(parsed.intents).union(required_intents or []),
        required_materials=set(parsed.required_materials).union(required_materials or []),
        service_temp_c=service_temp_c if service_temp_c is not None else parsed.service_temp_c,
        hardness=hardness if hardness is not None else parsed.hardness,
        thickness_mm=thickness_mm if thickness_mm is not None else parsed.thickness_mm,
    )


def recommend(
    query: str,
    data: dict[str, list[dict[str, Any]]],
    required_materials: list[str] | None = None,
    required_intents: list[str] | None = None,
    service_temp_c: float | None = None,
    hardness: float | None = None,
    thickness_mm: float | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    parsed = parse_query(query, data["synonyms"])
    parsed = add_ui_requirements(parsed, required_materials, required_intents, service_temp_c, hardness, thickness_mm)
    variants = data["variants"]
    results: list[dict[str, Any]] = []

    for row in data["products"]:
        material = str(row.get("material_code") or "")
        apps = split_codes(row.get("application_categories"))
        tags = split_codes(row.get("property_tags"))
        blob = build_text_blob(row)
        score = 0
        reasons: list[str] = []
        warnings: list[str] = []

        if query.strip():
            token_hits = [token for token in parsed.tokens if len(token) >= 3 and not token.isdigit() and token in blob]
            if token_hits:
                score += min(25, len(set(token_hits)) * 5)
                reasons.append("tekstivaste: " + ", ".join(sorted(set(token_hits))[:5]))

        for intent in parsed.intents:
            rule = INTENT_RULES.get(intent) or INTENT_RULES.get(normalize_text(intent))
            if not rule:
                continue
            app_hits = apps.intersection(rule["apps"])
            tag_hits = tags.intersection(rule["tags"])
            material_hit = material in rule["materials"]
            avoid_hit = material in rule["avoid_materials"]
            if app_hits:
                score += 35
                reasons.append(f"kasutusvaldkond sobib: {', '.join(sorted(app_hits))}")
            if tag_hits:
                score += 30
                reasons.append(f"omadus sobib: {', '.join(sorted(tag_hits))}")
            if material_hit:
                score += 15
                reasons.append(f"materjal sobib nõudele: {material}")
            if avoid_hit:
                score -= 30
                warnings.append(f"{material.upper()} võib selle kasutuse jaoks olla nõrk valik")

        if parsed.required_materials:
            if material in parsed.required_materials:
                score += 45
                reasons.append(f"nõutud materjal: {material}")
            else:
                score -= 80
                warnings.append("ei vasta valitud materjalile")

        if parsed.service_temp_c is not None:
            min_temp = row.get("min_temp_c")
            max_temp = row.get("max_temp_c")
            if min_temp is not None and max_temp is not None and float(min_temp) <= parsed.service_temp_c <= float(max_temp):
                score += 35
                reasons.append(f"temperatuurivahemik katab {parsed.service_temp_c:g} C")
            else:
                score -= 90
                warnings.append(f"temperatuur {parsed.service_temp_c:g} C jääb vahemikust välja")

        if parsed.hardness is not None:
            row_hardness = row.get("hardness_shore_a")
            if row_hardness is not None:
                delta = abs(float(row_hardness) - parsed.hardness)
                if delta <= 5:
                    score += 20
                    reasons.append(f"kõvadus lähedal: {float(row_hardness):g} Shore A")
                elif delta <= 10:
                    score += 5
                    reasons.append(f"kõvadus osaliselt lähedal: {float(row_hardness):g} Shore A")
                else:
                    score -= 20
                    warnings.append("kõvadus erineb märgatavalt")

        if parsed.thickness_mm is not None:
            product_match = thickness_matches(row.get("thickness_text"), parsed.thickness_mm)
            variant_match = variant_thickness_matches(variants, str(row.get("product_id")), parsed.thickness_mm)
            if product_match or variant_match:
                score += 25
                reasons.append(f"paksus {parsed.thickness_mm:g} mm on vahemikus/variandis olemas")
            else:
                score -= 60
                warnings.append(f"paksust {parsed.thickness_mm:g} mm ei leitud")

        if not query.strip() and not parsed.intents and not parsed.required_materials:
            score += 1

        if "needs_classification" in apps:
            warnings.append("kasutusvaldkond vajab ülevaatust")
        if "needs" in normalize_text(row.get("verification_status")):
            warnings.append("puudub täpne PDF lehe viide")

        if score > 0:
            result = dict(row)
            result["score"] = score
            result["reasons"] = "; ".join(dict.fromkeys(reasons)) or "üldine vaste"
            result["warnings"] = "; ".join(dict.fromkeys(warnings))
            results.append(result)

    results.sort(key=lambda item: (item["score"], item.get("max_temp_c") or -999), reverse=True)
    return results[:limit]


def variants_for_product(data: dict[str, list[dict[str, Any]]], product_id: str) -> list[dict[str, Any]]:
    return [row for row in data["variants"] if row.get("product_id") == product_id]


def quick_answer(result: dict[str, Any]) -> str:
    warnings = f" Hoiatus: {result['warnings']}." if result.get("warnings") else ""
    return (
        f"{result.get('product_name')} ({result.get('article_code')}, {str(result.get('material_code')).upper()}) "
        f"- skoor {result.get('score')}. Põhjus: {result.get('reasons')}.{warnings}"
    )
