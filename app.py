import streamlit as st
import streamlit.components.v1 as components
import re
import html
import io
import zipfile
import pandas as pd
import altair as alt

# ==================================================================================
# 0. GOOGLE TAG MANAGER (GTM)
# ==================================================================================

def inject_gtm(gtm_id):
    if not gtm_id: return
    gtm_code = f"""
    <script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{'gtm.start':
    new Date().getTime(),event:'gtm.js'}});var f=d.getElementsByTagName(s)[0],
    j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
    'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
    }})(window,document,'script','dataLayer','{gtm_id}');</script>
    <noscript><iframe src="https://www.googletagmanager.com/ns.html?id={gtm_id}"
    height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>
    """
    components.html(gtm_code, height=0, width=0)

# ==================================================================================
# LOGIQUE MÉTIER (Inchangée)
# ==================================================================================

cps_timecode_re = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")

def parse_timecode_cps(tc: str) -> float:
    m = cps_timecode_re.match(tc.strip())
    if not m: return 0.0
    h, m_, s, ms = map(int, m.groups())
    return h * 3600 + m_ * 60 + s + ms / 1000.0

def prepare_text_for_cps(text_lines):
    cleaned_lines = []
    for line in text_lines:
        line = re.sub(r"<[^>\n]+>", "", line)
        cleaned_lines.append(line)
    text = "".join(line.strip() for line in cleaned_lines)
    text = html.unescape(text)
    text = "".join(ch for ch in text if ch.isprintable() or ch == " ")
    text = re.sub(r" +", " ", text)
    return text.strip()

def is_indicator(text: str) -> bool:
    cleaned = text.strip()
    if re.fullmatch(r"[♪*. ]{1,4}", cleaned): return True
    indicators = ["...", "*...", "♪...", "♪ ...", "* ...", "♪", "*"]
    return cleaned in indicators

def classify_cps(cps: float) -> int:
    if cps <= 12: return 1
    elif cps <= 15: return 2
    elif cps <= 17: return 3
    elif cps <= 19: return 4
    elif cps <= 23: return 5
    elif cps <= 28: return 6
    else: return 7

def interpolate_color(cps: float) -> str:
    def lerp_color(c1, c2, t):
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        return f"#{r:02x}{g:02x}{b:02x}"
    
    colors = {
        'green_vif': (0, 208, 0), 'green_pale': (160, 240, 160),
        'yellow': (255, 230, 100), 'orange': (255, 140, 0),
        'red_vif': (255, 0, 0), 'red_dark': (150, 0, 0),
    }

    if cps <= 12: return "#00d000"
    elif cps <= 15: return lerp_color(colors['green_vif'], colors['green_pale'], (cps - 12) / 3)
    elif cps <= 17: return lerp_color(colors['green_pale'], colors['yellow'], (cps - 15) / 2)
    elif cps <= 19: return lerp_color(colors['yellow'], colors['orange'], (cps - 17) / 2)
    elif cps <= 23: return lerp_color(colors['orange'], colors['red_vif'], (cps - 19) / 4)
    elif cps <= 28: return lerp_color(colors['red_vif'], colors['red_dark'], (cps - 23) / 5)
    else: return "#3c0000"

def parse_srt_content(content_str: str):
    blocks = re.split(r"\n\s*\n", content_str.strip(), flags=re.MULTILINE)
    cues = []
    index_counter = 1
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 2: continue
        try:
            _ = int(lines[0].strip())
            line_offset = 1
        except ValueError:
            line_offset = 0
        if len(lines) <= line_offset: continue
        tc_line = lines[line_offset].strip()
        if "-->" not in tc_line: continue
        parts = tc_line.split("-->")
        if len(parts) != 2: continue
        start_tc, end_tc = [p.strip() for p in parts]
        start_sec = parse_timecode_cps(start_tc)
        end_sec = parse_timecode_cps(end_tc)
        duration = max(0.0, end_sec - start_sec)
        text_lines = lines[line_offset + 1 :]
        text_raw = prepare_text_for_cps(text_lines)
        text_display = "<br>".join(text_lines)
        cps = len(text_raw) / duration if duration > 0 else 0.0
        cues.append({
            "index": index_counter,
            "start": start_tc, "end": end_tc, "duration": duration,
            "text_display": text_display, "cps": cps,
            "category": classify_cps(cps)
        })
        index_counter += 1
    return cues

# ==================================================================================
# GÉNÉRATION HTML (V8 - Focus Accessibilité)
# ==================================================================================

def generate_html_string(cues, source_filename: str) -> str:
    total_raw = len(cues)
    if total_raw == 0: return f"<h3>⚠️ {source_filename} : Vide ou mal formé.</h3>"
    real_cues = [c for c in cues if not is_indicator(c["text_display"])]
    excluded_count = total_raw - len(real_cues)
    total = len(real_cues)
    if total == 0: return f"<h3>⚠️ {source_filename} : Aucun dialogue analysable.</h3>"
    
    counts = {i: 0 for i in range(1, 8)}
    for c in real_cues: counts[c["category"]] += 1
    
    cps_values = [c["cps"] for c in real_cues if c["duration"] > 0]
    avg_cps = sum(cps_values) / len(cps_values) if cps_values else 0.0
    median_cps = 0.0
    if cps_values:
        sorted_cps = sorted(cps_values)
        n = len(sorted_cps)
        median_cps = (sorted_cps[n//2 - 1] + sorted_cps[n//2])/2 if n % 2 == 0 else sorted_cps[n//2]
    
    cues_by_cat = {i: [] for i in range(1, 8)}
    sorted_global = sorted(real_cues, key=lambda c: c["cps"], reverse=True)
    for c in sorted_global: cues_by_cat[c["category"]].append(c)
    for cat in range(1, 8): cues_by_cat[cat].sort(key=lambda c: c["cps"])
    
    count_green = sum(counts[i] for i in range(1, 4))
    count_orange = counts[4]
    count_red = sum(counts[i] for i in range(5, 8))
    
    pct_green = (count_green / total * 100) if total else 0.0
    pct_orange = (count_orange / total * 100) if total else 0.0
    pct_red = (count_red / total * 100) if total else 0.0
    
    label_map = {
        1: "Optimal ≤ 12 CPS", 2: "Très lisible > 12 à 15 CPS", 3: "Lisible > 15 à 17 CPS",
        4: "Rapide > 17 à 19 CPS", 5: "Très rapide > 19 à 23 CPS", 6: "Trop rapide > 23 à 28 CPS",
        7: "Beaucoup trop rapide > 28 CPS"
    }
    cps_limits = {
        1: (0, 12), 2: (12, 15), 3: (15, 17), 4: (17, 19),
        5: (19, 23), 6: (23, 28), 7: (28, 40)
    }
    groups = [
        {"name": "green",  "cats": [1, 2, 3], "pct": pct_green},
        {"name": "orange", "cats": [4],       "pct": pct_orange},
        {"name": "red",    "cats": [5, 6, 7], "pct": pct_red},
    ]

    html_parts = []
    
    # CSS Clean & Pro
    html_parts.append(f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><title>Audit CPS - {html.escape(source_filename)}</title>
    <style>
    body {{ font-family: 'Segoe UI', system-ui, sans-serif; max-width: 1100px; margin: 20px auto; padding: 0 10px 40px; background: #f5f5f5; color: #333; }} 
    h1 {{ font-size: 1.6rem; margin-bottom: 5px; color: #111; }} 
    h2 {{ font-size: 1.3rem; margin-top: 30px; border-bottom: 2px solid #ddd; padding-bottom: 10px; }}
    .subtitle {{ color: #666; font-size: 0.95rem; margin-bottom: 25px; }}
    
    .summary {{ background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.08); margin-bottom: 25px; }} 
    .summary-header {{ display: flex; align-items: center; justify-content: space-between; gap: 40px; margin-bottom: 15px; }} 
    
    .pie-wrapper {{ flex-shrink: 0; flex-grow: 1; position: relative; display: flex; justify-content: center; min-width: 250px; }} 
    .pie {{ width: 100%; height: 50px; border-radius: 6px; }} 
    
    .barcode-wrapper {{ margin-top: 10px; }} 
    .barcode {{ width: 100%; height: 12px; border-radius: 4px; opacity: 0.9; }} 
    .caption {{ font-size: 0.8rem; color: #777; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }} 
    
    .group-container {{ display: flex; margin-bottom: 15px; }} 
    .group-list {{ flex-grow: 1; margin-right: 15px; }} 
    .group-bracket {{ width: 100px; display: flex; align-items: center; flex-shrink: 0; position: relative; }} 
    
    .bracket-visual {{ width: 15px; align-self: stretch; border-right: 4px solid; border-top: 4px solid; border-bottom: 4px solid; border-top-right-radius: 10px; border-bottom-right-radius: 10px; margin-right: 12px; margin-top: 4px; margin-bottom: 4px; opacity: 0.7; }} 
    .bracket-label-container {{ display: flex; flex-direction: column; align-items: center; justify-content: center; }}
    .bracket-label {{ font-weight: 800; font-size: 1.3rem; line-height: 1.1; }} 
    
    .warning-icon {{ font-size: 1.4rem; cursor: help; margin-top: 6px; color: #D32F2F; }}
    
    .group-green .bracket-visual {{ border-color: #2E7D32; }} .group-green .bracket-label {{ color: #2E7D32; }} 
    .group-orange .bracket-visual {{ border-color: #EF6C00; }} .group-orange .bracket-label {{ color: #EF6C00; }} 
    .group-red .bracket-visual {{ border-color: #C62828; }} .group-red .bracket-label {{ color: #C62828; }} 
    
    details.cat-details {{ margin-bottom: 6px; border-radius: 4px; overflow: hidden; border: 1px solid rgba(0,0,0,0.05); }} 
    details.cat-details summary {{ cursor: pointer; padding: 8px 12px; font-weight: 600; display: flex; justify-content: space-between; list-style: none; outline: none; transition: background 0.2s; }} 
    details.cat-details[open] summary .arrow {{ transform: rotate(90deg); }} 
    .arrow {{ display: inline-block; margin-right: 8px; transition: transform 0.15s; color: #555; }} 
    
    .cat-content {{ padding: 0; background: #fff; }} 
    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }} 
    th, td {{ border-bottom: 1px solid #f0f0f0; padding: 6px 10px; text-align: left; vertical-align: top; }} 
    th {{ background: #fafafa; position: sticky; top: 0; font-size: 0.8rem; text-transform: uppercase; color: #777; font-weight: 600; }} 
    tr:hover td {{ background: #f9f9f9; }}
    </style></head><body>""")
    
    html_parts.append(f"<h1>Audit CPS : {html.escape(source_filename)}</h1>")
    html_parts.append(f"<div class='subtitle'>Analyse de conformité Charte 2011 • Densité & Accessibilité</div>")
    
    # VISUALIZATIONS
    sorted_by_cps = sorted(real_cues, key=lambda c: c["cps"])
    if not sorted_by_cps: pie_bg = "#ddd"
    else:
        parts = []
        pct_per_st = 100.0 / len(sorted_by_cps)
        for i, cue in enumerate(sorted_by_cps):
            color = interpolate_color(cue["cps"])
            parts.append(f"{color} {i * pct_per_st:.3f}%")
            parts.append(f"{color} {(i+1) * pct_per_st:.3f}%")
        pie_bg = "linear-gradient(to right, " + ", ".join(parts) + ")"
    
    barcode_bg = "#ddd"
    if real_cues:
        b_parts = []
        total_real = len(real_cues)
        pct_step = 100.0 / total_real
        chronological_cues = sorted(real_cues, key=lambda c: c["index"])
        for i, cue in enumerate(chronological_cues):
            color = interpolate_color(cue["cps"])
            b_parts.append(f"{color} {i * pct_step:.3f}%")
            b_parts.append(f"{color} {(i+1) * pct_step:.3f}%")
        barcode_bg = "linear-gradient(to right, " + ", ".join(b_parts) + ")"
    
    median_clamped = min(median_cps, 30)
    arrow_pos = (median_clamped / 30) * 100
    
    html_parts.append(f"""<div class='summary'><div class='summary-header'><div class='summary-text'><p style='margin:2px 0; font-size:1.1rem;'><strong>Moyenne :</strong> {avg_cps:.2f} CPS</p><p style='margin:2px 0; color:#666;'><strong>Total :</strong> {total_raw} ST <span style='font-size:0.85em;'>(dont {excluded_count} exclus)</span></p></div><div class='pie-wrapper'><div class='pie' style='background:{pie_bg};'></div><div style='position:absolute; top:-10px; left:{arrow_pos:.1f}%; transform:translateX(-50%); border-left:8px solid transparent; border-right:8px solid transparent; border-top:12px solid #333;'></div><div style='position:absolute; top:-28px; left:{arrow_pos:.1f}%; transform:translateX(-50%); font-size:0.75em; font-weight:700; color:#333;'>Médiane {median_cps:.1f}</div></div></div><div class='barcode-wrapper'><div class='barcode' style='background:{barcode_bg};'></div><p class='caption'>Densité chronologique (Début &rarr; Fin)</p></div></div>""")
    
    html_parts.append("<div class='cat-section'><h2>Répartition par catégories</h2>")
    
    for grp in groups:
        has_content = any(counts[c] > 0 for c in grp["cats"])
        if not has_content: continue
        
        warning_html = ""
        # LOGIQUE D'ALERTE CHARTE 2011
        title_attr = ""
        if grp["name"] == "red" and grp["pct"] > 10:
            title_attr = "Ce volume de sous-titres très rapides (> 19 CPS) compromet l'accessibilité pour les sourds profonds."
            warning_html = f"<div class='warning-icon' title='{title_attr}'>⚠️</div>"
        elif grp["name"] == "green" and grp["pct"] < 70:
            title_attr = "La part de sous-titres confortables est inférieure au standard recommandé (70%)."
            warning_html = f"<div class='warning-icon' title='{title_attr}'>⚠️</div>"

        html_parts.append(f"<div class='group-container group-{grp['name']}'><div class='group-list'>")
        for cat in grp["cats"]:
            count = counts[cat]
            if count == 0: continue
            pct = (count / total * 100)
            c_start = interpolate_color(cps_limits[cat][0])
            c_end = interpolate_color(cps_limits[cat][1])
            bg_grad = f"linear-gradient(to right, {c_start} 0%, {c_end if cat > 1 else c_start} {pct:.1f}%, #e0e0e0 {pct:.1f}%, #e0e0e0 100%)"
            html_parts.append(f"<details class='cat-details' style='background:{bg_grad}'><summary><span class='label'><span class='arrow'>▶</span>{cat}. {label_map[cat]}</span><span class='count'>{count} ST ({pct:.1f}%)</span></summary><div class='cat-content'><table><tr><th>#</th><th>Timecode</th><th>Durée</th><th>CPS</th><th>Texte</th></tr>")
            for c in cues_by_cat[cat]:
                txt = html.escape(c["text_display"][:300]).replace("&lt;br&gt;", "<br>")
                html_parts.append(f"<tr><td>{c['index']}</td><td>{c['start']} → {c['end']}</td><td>{c['duration']:.2f}s</td><td>{c['cps']:.2f}</td><td>{txt}</td></tr>")
            html_parts.append("</table></div></details>")
        html_parts.append(f"</div><div class='group-bracket'><div class='bracket-visual'></div><div class='bracket-label-container'><div class='bracket-label'>{grp['pct']:.1f}%</div>{warning_html}</div></div></div>")
    html_parts.append("</div></body></html>")
    return "".join(html_parts)

# ==================================================================================
# APP STREAMLIT (Version Expert V8)
# ==================================================================================

def main():
    st.set_page_config(page_title="Audit Lisibilité Charte 2011", page_icon="⚖️", layout="centered")
    
    # --- GTM ---
    GTM_ID = "GTM-W972MJXS"
    inject_gtm(GTM_ID)
    
    # --- SIDEBAR EXPERTISE (CV / LEGITIMITÉ) ---
    with st.sidebar:
        st.markdown("### ⚖️ Audit de Conformité")
        st.caption("v1.0 • Outil de contrôle")
        st.info("""
        Cet outil analyse la densité des fichiers SME pour vérifier leur compatibilité avec les exigences d'accessibilité (Loi 2005 / Charte Arcom).
        """)
        
        st.markdown("---")
        st.markdown("#### 👤 Expertise")
        st.markdown("**Thierry Jullien**")
        st.caption("""
        * Co-auteur Charte Qualité 2011 (CSA/Arcom)
        * Ex-Président du CAASEM
        * Membre fondateur AVA
        * Expert accessibilité depuis 2005
        """)
        st.markdown("---")
        st.markdown("#### 🎯 Objectif")
        st.caption("""
        Distinguer le sous-titrage adapté (accessible) de la transcription littérale (excluante).
        """)

    # --- HEADER ---
    st.title("⚖️ Audit de Lisibilité SME")
    st.markdown("**Contrôle de conformité / Charte Arcom 2011**")
    st.markdown("---")

    if 'uploader_key' not in st.session_state:
        st.session_state.uploader_key = 0

    def reset_uploader():
        st.session_state.uploader_key += 1

    tab_audit, tab_context = st.tabs(["📊 Lancer l'Audit", "ℹ️ Comprendre l'enjeu"])

    # --- ONGLET CONTEXTE (MANIFESTE) ---
    with tab_context:
        st.header("Accessibilité vs Affichage")
        st.markdown("""
        La loi handicap de 2005 impose l'accessibilité, pas seulement la présence de texte à l'écran.
        Un sous-titrage dense, calqué sur le débit oral, est lisible pour un entendant (lecteur fluent), 
        mais devient une barrière infranchissable pour de nombreux sourds de naissance ou malentendants âgés.
        """)
        
        st.markdown("### Le Diagnostic par la Densité")
        st.info("""
        L'analyse statistique de la densité (CPS) permet de révéler la méthode de fabrication d'un fichier.
        Elle différencie le travail d'adaptation (Text-to-Subtitle) de la simple transcription (Text-to-Text).
        """)

        col1, col2 = st.columns(2)
        with col1:
            st.success("**✅ L'Adaptation (SME)**")
            st.caption("Le texte est resserré pour libérer du temps de lecture.")
            st.markdown("- **Vert (>70%)** : Confort de lecture")
            st.markdown("- **Rouge (<10%)** : Pics exceptionnels")
        with col2:
            st.error("**❌ La Transcription**")
            st.caption("Le texte suit l'audio sans filtre, saturant la cognition visuelle.")
            st.markdown("- **Vert (<60%)** : Insuffisant")
            st.markdown("- **Rouge (>15%)** : Exclusion du public")

    # --- ONGLET AUDIT (APP) ---
    with tab_audit:
        st.info("👇 Déposez vos fichiers **.srt** pour audit de conformité.", icon="📂")

        uploaded_files = st.file_uploader(
            label="Upload SRT files", 
            type=["srt"], 
            accept_multiple_files=True,
            label_visibility="collapsed",
            key=f"uploader_{st.session_state.uploader_key}"
        )

        if uploaded_files:
            col1, col2 = st.columns([0.85, 0.15])
            with col2:
                st.button("🗑️ Reset", on_click=reset_uploader, help="Tout effacer")

            with st.status("Audit en cours...", expanded=True) as status:
                results = []
                for i, uploaded_file in enumerate(uploaded_files):
                    status.write(f"Analyse de `{uploaded_file.name}`...")
                    
                    # Lecture
                    bytes_data = uploaded_file.getvalue()
                    try:
                        content = bytes_data.decode("utf-8")
                    except UnicodeDecodeError:
                        content = bytes_data.decode("latin-1")
                    
                    # Parsing
                    cues = parse_srt_content(content)
                    html_report = generate_html_string(cues, uploaded_file.name)
                    
                    # Calcul stats
                    real_cues = [c for c in cues if not is_indicator(c["text_display"])]
                    total = len(real_cues)
                    stats = {i: 0 for i in range(1, 8)}
                    for c in real_cues: stats[c["category"]] += 1
                    
                    pct_green = (sum(stats[i] for i in range(1, 4)) / total * 100) if total else 0
                    pct_orange = (stats[4] / total * 100) if total else 0
                    pct_red = (sum(stats[i] for i in range(5, 8)) / total * 100) if total else 0
                    
                    # Verdict Logic (Basé sur tes seuils)
                    is_compliant = (pct_green >= 70) and (pct_red <= 10)
                    
                    results.append({
                        "filename": uploaded_file.name,
                        "html": html_report,
                        "stem": uploaded_file.name.rsplit('.', 1)[0],
                        "pct_green": pct_green,
                        "pct_orange": pct_orange,
                        "pct_red": pct_red,
                        "compliant": is_compliant
                    })
                
                status.update(label="✅ Audit terminé", state="complete", expanded=False)

            st.markdown("### 📄 Résultats de l'audit")
            
            for res in results:
                with st.container(border=True):
                    # EN-TÊTE AVEC VERDICT
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.subheader(f"`{res['filename']}`")
                        if res['compliant']:
                            st.success("✅ **PROFIL STANDARD DÉTECTÉ (Conforme)**")
                            st.caption("Distribution statistique cohérente avec une adaptation humaine.")
                        else:
                            st.error("⚠️ **ALERTE DENSITÉ (Non Conforme)**")
                            st.caption("Profil statistique caractéristique d'une transcription littérale non adaptée.")
                    
                    with c2:
                        st.download_button(
                            label="⬇️ Rapport HTML",
                            data=res["html"],
                            file_name=f"{res['stem']}_Audit.html",
                            mime="text/html",
                            key=f"dl_{res['filename']}"
                        )

                    st.divider()

                    # GRAPHIQUE COMPARATIF (Altair)
                    # Données pour le chart
                    source_data = pd.DataFrame([
                        {'Zone': '1. Confort (Vert)', 'Pourcentage': res['pct_green'], 'Type': 'Votre Fichier', 'Color': '#2E7D32'},
                        {'Zone': '2. Rapide (Orange)', 'Pourcentage': res['pct_orange'], 'Type': 'Votre Fichier', 'Color': '#EF6C00'},
                        {'Zone': '3. Excessif (Rouge)', 'Pourcentage': res['pct_red'], 'Type': 'Votre Fichier', 'Color': '#C62828'},
                        
                        {'Zone': '1. Confort (Vert)', 'Pourcentage': 80, 'Type': 'Référence (Charte 2011)', 'Color': '#81C784'},
                        {'Zone': '2. Rapide (Orange)', 'Pourcentage': 15, 'Type': 'Référence (Charte 2011)', 'Color': '#FFB74D'},
                        {'Zone': '3. Excessif (Rouge)', 'Pourcentage': 5, 'Type': 'Référence (Charte 2011)', 'Color': '#E57373'},
                    ])
                    
                    # Chart Altair
                    chart = alt.Chart(source_data).mark_bar().encode(
                        x=alt.X('Pourcentage:Q', scale=alt.Scale(domain=[0, 100]), title=None),
                        y=alt.Y('Type:N', title=None, axis=alt.Axis(labels=True)),
                        color=alt.Color('Color:N', scale=None, legend=None),
                        order=alt.Order('Zone', sort='ascending'), # Ordre d'empilement
                        column=alt.Column('Zone:N', header=alt.Header(title=None, labelFontSize=12, labelFontWeight='bold'))
                    ).properties(
                        height=60,
                        width=180 # Largeur de chaque colonne
                    ).configure_axis(
                        grid=False
                    ).configure_view(
                        strokeWidth=0
                    )
                    
                    st.altair_chart(chart, use_container_width=False)
                    
                    # Détails
                    with st.expander("👁️ Voir le détail des sous-titres"):
                        components.html(res["html"], height=500, scrolling=True)

if __name__ == "__main__":
    main()
