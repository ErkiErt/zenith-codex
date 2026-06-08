from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DB_NAME = "Zenith_Materjalibaas.sqlite"

# ── Paranduste ajalugu ─────────────────────────────────────────────────────────
# FIX A : DIRECT_TERMS otsing kasutab nüüd sõnapiiri (re.search \W) mitte
#         substringi — välistab "uv" tabamise "tuvik"-us, "food" tabamise
#         "foods"-is jne.
# FIX B : "uv" asendatud täpsema "uv-kindlus"/"uv kindlus" vastu;
#         lisatud sõnapiiri kaitse kõigile lühikestele terminitele.
# FIX C : Külma temp harus (service_temp_c < 0) eemaldatud nõue
#         max_temp is not None — külma puhul on max_temp ebaoluline.
#         Toode mille max_temp puudub läbib külma kontrolli kui min_temp sobib.
# ──────────────────────────────────────────────────────────────────────────────

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
    # lumelukkamine kasutab abrasion_wear apps/tags-e, kuid on eraldi intent
    # et vältida topeltskoorimist kui mõlemad on aktiivsed (FIX 10)
    "lumelukkamine": {
        "apps": {"abrasion_wear"},
        "tags": {"abrasion_resistance"},
        "materials": {"nr", "sbr"},
        "avoid_materials": {"epdm"},
    },
    # gate — app/tag peab olema olemas
    "food_contact": {
        "apps": {"food_contact"},
        "tags": {"food_grade"},
        "materials": {"silicone", "epdm", "nbr", "cr"},
        "avoid_materials": set(),
        "require_app_or_tag": True,
    },
    "high_temperature": {
        "apps": {"high_temperature"},
        "tags": {"high_temperature"},
        "materials": {"silicone", "fkm", "epdm", "csm"},
        "avoid_materials": set(),
    },
    # eksplitsiitsed apps/tags; sbr penaliseeritud
    "low_temperature": {
        "apps": {"low_temperature"},
        "tags": {"low_temperature"},
        "materials": {"silicone", "epdm", "nr", "butyl"},
        "avoid_materials": {"sbr"},
    },
    # gate — app/tag peab olema olemas
    "chemical": {
        "apps": {"chemical"},
        "tags": {"chemical_resistance"},
        "materials": {"fkm", "nbr", "epdm", "cr"},
        "avoid_materials": set(),
        "require_app_or_tag": True,
    },
    # uus gated intent
    "construction_fire": {
        "apps": {"construction_fire"},
        "tags": {"flame_retardant", "fire_resistance"},
        "materials": {"cr", "csm", "nbr_pvc"},
        "avoid_materials": set(),
        "require_app_or_tag": True,
    },
    # Reservintendid tuleviku kasutusvaldkondade jaoks — ei lisa praegu punkte.
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

# FIX A+B: "uv" asendatud täpsemate vastetega; eemaldatud "kutuse" (duplikaat),
# "kulumine" ja "kulumiskindel" ("kulum" katab mõlemaid substringina).
# Kõik terminid kontrollitakse nüüd sõnapiirriga (vt parse_query).
DIRECT_TERMS = {
    # lumesahk / abrasion
    "lumesahk": "lumelukkamine",
    "sahk": "lumelukkamine",
    "lume sahk": "lumelukkamine",
    "snow plow": "lumelukkamine",
    "snow blade": "lumelukkamine",
    "kulum": "abrasion_wear",
    # õli / kütus
    "olipaagi": "oilfuel",
    "olipaak": "oilfuel",
    "kutus": "oilfuel",
    "kytuse": "oilfuel",
    "bensiin": "oilfuel",
    "diisel": "oilfuel",
    # UV / ilmastik — FIX B: "uv" asendatud pikemate vastetega
    "uv-kindlus": "weather_uv",
    "uv kindlus": "weather_uv",
    "uv-kaitse": "weather_uv",
    "uv kaitse": "weather_uv",
    "uvkindlus": "weather_uv",
    "osoon": "weather_uv",
    "ilmastik": "weather_uv",
    # food grade
    "food grade": "food_contact",
    "food": "food_contact",
    "fda": "food_contact",
    "toiduklass": "food_contact",
    # temperatuur
    "kuum": "high_temperature",
    "korge temperatuur": "high_temperature",
    "kylm": "low_temperature",
    # keemia
    "kemikaal": "chemical",
    "keemia": "chemical",
    # tulekindlus
    "tulekindlus": "construction_fire",
    "tulekaitse": "construction_fire",
}

# Terminid mis vajavad täpset sõnapiiri (mitte substringi)
# kuna on lühikesed või esinevad muudes sõnades:
_EXACT_TERMS = {"uv-kindlus", "uv kindlus", "uv-kaitse", "uv kaitse",
                "uvkindlus", "food", "fda", "kuum", "kylm"}


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
    text = "" .join(ch for ch in text if not unicodedata.combining(ch))
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


def _term_in_text(term: str, normalized: str) -> bool:
    """Kontrolli termin normalized tekstis s\u00f5napiiri arvestades.

    Lühikesed / t\u00e4psust n\u00f5udvad terminid (_EXACT_TERMS) kontrollitakse
    re.search s\u00f5napiiri (\\W) abil. Pikemad terminid kasutavad substringi
    (kiirem, piisavalt t\u00e4pne).
    """
    if term in _EXACT_TERMS or len(term) <= 4:
        return bool(re.search(rf"(^|\W){re.escape(term)}($|\W)", normalized))
    return term in normalized


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
                # FIX A: lisa ainult comparable_value (lowercase, diacritics eemaldatud)
                # et vältida suurtähtede/alakriipsude müra intents hulgas
                intents.add(comparable_value)

    # FIX A: DIRECT_TERMS kasutab _term_in_text() mis kontrollib sõnapiiri
    # lühikeste/täpsust nõudvate terminite puhul
    for term, intent in DIRECT_TERMS.items():
        if _term_in_text(normalize_text(term), normalized):
            intents.add(intent)

    for material in ["sbr", "nbr", "epdm", "fkm", "cr", "nr", "silicone", "silikon", "csm", "butyl"]:
        if re.search(rf"(^|\W){re.escape(material)}($|\W)", normalized):
            required_materials.add("silicone" if material == "silikon" else material)

    service_temp = None
    # sõnapiir \b välistab "cr", "csm" jm materjalikoodi valevasted;
    # Pattern 3 negatiivne lookahead välistab "+50mm" paksuse konflikt
    for pattern in [
        r"(-?\d+(?:[\.,]\d+)?)\s*(?:\bc\b|kraadi)",
        r"(?:temp|temperatuur)[^\d-]*(-?\d+(?:[\.,]\d+)?)",
        r"\+\s*(\d+(?:[\.,]\d+)?)(?!\s*mm)",
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

    # Kui lumelukkamine on aktiivsete intentide hulgas, eemalda abrasion_wear
    # et vältida topeltskoorimist (mõlemal on identsed apps/tags)
    active_intents = set(parsed.intents)
    if "lumelukkamine" in active_intents:
        active_intents.discard("abrasion_wear")

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

        for intent in active_intents:
            rule = INTENT_RULES.get(intent) or INTENT_RULES.get(normalize_text(intent))
            if not rule:
                continue

            app_hits = apps.intersection(rule["apps"])
            tag_hits = tags.intersection(rule["tags"])
            material_hit = material in rule["materials"]
            avoid_hit = material in rule["avoid_materials"]
            requires_gate = rule.get("require_app_or_tag", False)

            if requires_gate:
                if app_hits:
                    score += 35
                    reasons.append(f"kasutusvaldkond sobib: {', '.join(sorted(app_hits))}")
                if tag_hits:
                    score += 30
                    reasons.append(f"omadus sobib: {', '.join(sorted(tag_hits))}")
                if not app_hits and not tag_hits:
                    score -= 20
                    warnings.append(
                        f"toode ei oma kinnitatud '{intent}' vastet — kasuta ainult selge sertifikaadi korral"
                    )
                elif material_hit:
                    score += 10
                    reasons.append(f"materjal toetab nõuet: {material}")
            else:
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

        # low_temperature — boonus kinnitatud külmareitingä eest min_temp_c kaudu
        if "low_temperature" in active_intents:
            min_temp = row.get("min_temp_c")
            if min_temp is not None and float(min_temp) <= -40:
                score += 15
                reasons.append(f"miinimumtemperatuur {float(min_temp):g} C — sügavkülm")
            elif min_temp is not None and float(min_temp) <= -30:
                score += 5
                reasons.append(f"miinimumtemperatuur {float(min_temp):g} C — külmakindel")

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
            t = parsed.service_temp_c
            if t < 0:
                # FIX C: külma puhul on ainult min_temp_c oluline;
                # max_temp puudumine ei diskvalifitseeri toodet
                if min_temp is not None:
                    if float(min_temp) <= t:
                        score += 35
                        reasons.append(f"min temperatuur {float(min_temp):g} C katab nõutud {t:g} C")
                    else:
                        score -= 90
                        warnings.append(f"toode ei talu {t:g} C (min {float(min_temp):g} C)")
            else:
                # Soe/kuum: kontrollime täielikku vahemikku
                if min_temp is not None and max_temp is not None:
                    if float(min_temp) <= t <= float(max_temp):
                        score += 35
                        reasons.append(f"temperatuurivahemik katab {t:g} C")
                    else:
                        score -= 90
                        warnings.append(f"temperatuur {t:g} C jääb vahemikust välja")

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

        if "needs_classification" in apps:
            warnings.append("kasutusvaldkond vajab ülevaatust")
        if "needs" in normalize_text(row.get("verification_status")):
            warnings.append("puudub täpne PDF lehe viide")

        is_empty_search = not query.strip() and not parsed.intents and not parsed.required_materials
        if score > 0 or is_empty_search:
            result = dict(row)
            result["score"] = score
            result["reasons"] = "; ".join(dict.fromkeys(reasons)) or ("üldine loend" if is_empty_search else "üldine vaste")
            result["warnings"] = "; ".join(dict.fromkeys(warnings))
            results.append(result)

    if not query.strip() and not parsed.intents and not parsed.required_materials:
        results.sort(key=lambda item: (item.get("product_name") or "").lower())
    else:
        results.sort(key=lambda item: (item["score"], item.get("max_temp_c") or -999), reverse=True)

    return results[:limit]


def variants_for_product(data: dict[str, list[dict[str, Any]]], product_id: str) -> list[dict[str, Any]]:
    return [row for row in data["variants"] if row.get("product_id") == product_id]


def quick_answer(result: dict[str, Any]) -> str:
    warnings = f" Hoiatus: {result['warnings']}." if result.get("warnings") else ""
    return (
        f"{result.get('product_name')} ({result.get('article_code')}, {str(result.get('material_code')).upper()}) "
        f"sobib: {result.get('reasons')}.{warnings}"
    )
