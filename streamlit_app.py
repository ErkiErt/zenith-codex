from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from recommendation_engine import load_database, quick_answer, recommend, variants_for_product


BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "Zenith_Materjalibaas.sqlite"
EXCEL_PATH = BASE / "Zenith_Materjalibaas_LOPLIK.xlsx"


st.set_page_config(page_title="Zenith materjalisoovitaja", page_icon="Z", layout="wide")


@st.cache_data(show_spinner=False)
def get_data(db_mtime: float):
    return load_database(DB_PATH)


def as_df(rows):
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def render_badges(row):
    tags = str(row.get("property_tags") or "").split(";")
    apps = str(row.get("application_categories") or "").split(";")
    values = [value for value in apps + tags if value]
    if values:
        st.caption(" · ".join(dict.fromkeys(values)))


db_mtime = DB_PATH.stat().st_mtime if DB_PATH.exists() else 0
data = get_data(db_mtime)

materials = sorted({row["material_code"] for row in data["products"] if row.get("material_code")})
intent_options = {
    "Õli / kütus": "oilfuel",
    "UV / ilmastik": "weather_uv",
    "Kulumine / sahk": "abrasion_wear",
    "Food Grade": "food_contact",
    "Kõrge temperatuur": "high_temperature",
    "Madal temperatuur": "low_temperature",
    "Keemiline vastupidavus": "chemical",
}

st.title("Zenith materjalisoovitaja")
st.caption("Otsing ja soovitused Zenithi lukustatud lähteandmete põhjal.")

with st.sidebar:
    st.header("Nõuded")
    query = st.text_input(
        "Kirjelda vajadust",
        placeholder="nt õlipaagi tihend 100 kraadi 70 Shore või lumesahk 10 mm",
    )

    examples = {
        "Õlipaagi tihend": "õlipaagi tihend 100 kraadi",
        "Lumesahk": "lumesahk kulumiskindel 10 mm",
        "Food Grade": "food grade 120 kraadi",
        "UV EPDM": "UV ilmastik EPDM",
        "FKM kuum": "FKM 200 kraadi kemikaal",
    }
    example = st.selectbox("Kiirpäring", [""] + list(examples.keys()))
    if example and not query:
        query = examples[example]

    required_materials = st.multiselect("Materjal", materials)
    selected_intent_labels = st.multiselect("Omadus / kasutus", list(intent_options.keys()))
    required_intents = [intent_options[label] for label in selected_intent_labels]

    use_temp = st.checkbox("Filtreeri töötemperatuuri järgi")
    service_temp = st.number_input("Töötemperatuur C", value=100.0, step=5.0, disabled=not use_temp)

    use_hardness = st.checkbox("Filtreeri kõvaduse järgi")
    hardness = st.number_input("Kõvadus Shore A", value=70.0, step=5.0, disabled=not use_hardness)

    use_thickness = st.checkbox("Filtreeri paksuse järgi")
    thickness = st.number_input("Paksus mm", value=5.0, step=0.5, disabled=not use_thickness)

    limit = st.slider("Tulemusi", 3, 30, 10)

tab_recommend, tab_table, tab_sources, tab_review = st.tabs(
    ["Soovita", "Andmebaas", "Lähtefailid", "Kontroll"]
)

results = recommend(
    query=query,
    data=data,
    required_materials=required_materials,
    required_intents=required_intents,
    service_temp_c=service_temp if use_temp else None,
    hardness=hardness if use_hardness else None,
    thickness_mm=thickness if use_thickness else None,
    limit=limit,
)

with tab_recommend:
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Tooteid baasis", len(data["products"]))
    col_b.metric("Variante", len(data["variants"]))
    col_c.metric("Leitud vasteid", len(results))
    col_d.metric("Sünonüüme", len(data["synonyms"]))

    if not query and not required_materials and not required_intents and not use_temp and not use_hardness and not use_thickness:
        st.info("Sisesta vasakul vajadus või vali filtrid. Näiteks: õlipaagi tihend 100 kraadi, lumesahk 10 mm, food grade 120 kraadi.")

    if not results:
        st.warning("Sobivat tulemust ei leitud. Proovi eemaldada mõni filter või kirjelda kasutuskeskkonda teisiti.")
    else:
        best = results[0]
        st.subheader("Parim soovitus")
        st.success(quick_answer(best))

        for index, row in enumerate(results, start=1):
            with st.expander(f"{index}. {row['product_name']} · {row['article_code']} · {str(row['material_code']).upper()} · skoor {row['score']}", expanded=index <= 3):
                render_badges(row)
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("Min C", row.get("min_temp_c"))
                c2.metric("Max C", row.get("max_temp_c"))
                c3.metric("Shore A", row.get("hardness_shore_a"))
                c4.metric("MPa", row.get("tensile_strength_mpa"))
                c5.metric("Venitus %", row.get("elongation_pct"))

                st.write("**Miks sobib:**", row.get("reasons") or "-")
                if row.get("warnings"):
                    st.warning(row["warnings"])
                st.write("**Eripära:**", row.get("feature_text") or "-")
                st.caption(f"Allikastaatus: {row.get('source_status')} · Kontroll: {row.get('verification_status')} · Zenith lukus: {row.get('locked_zenith')}")

                variants = variants_for_product(data, row["product_id"])
                if variants:
                    st.write("**Variandid**")
                    st.dataframe(
                        as_df(variants)[
                            [
                                "thickness_mm_text",
                                "width_m_text",
                                "length_m_text",
                                "color",
                                "hardness_text",
                                "properties_text",
                            ]
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )

with tab_table:
    st.subheader("Otsingu tulemused tabelina")
    if results:
        table = as_df(results)
        shown = [
            "score",
            "product_name",
            "article_code",
            "material_code",
            "application_categories",
            "property_tags",
            "min_temp_c",
            "max_temp_c",
            "hardness_shore_a",
            "thickness_text",
            "feature_text",
            "reasons",
            "warnings",
        ]
        st.dataframe(table[shown], use_container_width=True, hide_index=True)
    else:
        st.dataframe(as_df(data["products"]), use_container_width=True, hide_index=True)

with tab_sources:
    st.subheader("Lähtefailid ja kaitsepõhimõte")
    st.write("Zenithi tehnilised lähteandmed on lukustatud. Uue info korral tuleb lisada tõendus ja uus versioon, mitte vana rida üle kirjutada.")
    st.dataframe(as_df(data["materials"]), use_container_width=True, hide_index=True)
    st.markdown(f"- Excel: `{EXCEL_PATH.name}`")
    st.markdown(f"- SQLite: `{DB_PATH.name}`")

with tab_review:
    st.subheader("Kontrolli vajavad kohad")
    st.dataframe(as_df(data["needs_review"]), use_container_width=True, hide_index=True)
    st.subheader("Sünonüümid")
    st.dataframe(as_df(data["synonyms"]), use_container_width=True, hide_index=True)
