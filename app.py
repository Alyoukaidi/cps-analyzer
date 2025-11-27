import streamlit as st
import streamlit.components.v1 as components
import re
import html
import io
import zipfile
import pandas as pd # Nécessaire pour le nouveau graphique comparatif

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
# 1. & 2. LOGIQUE MÉTIER CPS
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
    # CSS AVEC WARNING STYLES
    html_parts.append(f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><title>CPS - {html.escape(source_filename)}</title>
    <style>
    body {{ font-family: system-ui, sans-serif; max-width: 1100px; margin: 20px auto; padding: 0 10px 40px; background: #f5f5f5; color: #333; }} 
    h1 {{ font-size: 1.6rem; margin-bottom: 20px; }} 
    .summary {{ background: #fff; border-radius: 6px; padding: 12px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); margin-bottom: 20px; }} 
    .summary-header {{ display: flex; align-items: center; justify-content: space-between; gap: 40px; margin-bottom: 12px; }} 
    .pie-wrapper {{ flex-shrink: 0; flex-grow: 1; position: relative; display: flex; justify-content: center; min-width: 200px; }} 
    .pie {{ width: 100%; height: 60px; border-radius: 4px; }} 
    .barcode-wrapper {{ margin-top: 4px; }} 
    .barcode {{ width: 100%; height: 16px; border-radius: 4px; }} 
    .caption {{ font-size: 0.85em; color: #666; margin-top: 4px; }} 
    .group-container {{ display: flex; margin-bottom: 8px; }} 
    .group-list {{ flex-grow: 1; margin-right: 10px; }} 
    .group-bracket {{ width: 90px; display: flex; align-items: center; flex-shrink: 0; position: relative; }} 
    
    .bracket-visual {{ width: 15px; align-self: stretch; border-right: 3px solid; border-top: 3px solid; border-bottom: 3px solid; border-top-right-radius: 8px; border-bottom-right-radius: 8px; margin-right: 10px; margin-top: 2px; margin-bottom: 2px; opacity: 0.8; }} 
    .bracket-label-container {{ display: flex; flex-direction: column; align-items: center; justify-content: center; }}
    .bracket-label {{ font-weight: 700; font-size: 1.1rem; line-height: 1.1; }} 
    .warning-icon {{ font-size: 1.2rem; cursor: help; margin-top: 4px; animation: pulse 2s infinite; }}
    @keyframes pulse {{ 0% {{ transform: scale(1); opacity: 1; }} 50% {{ transform: scale(1.1); opacity: 0.8; }} 100% {{ transform: scale(1); opacity: 1; }} }}
    
    .group-green .bracket-visual {{ border-color: #00d000; }} .group-green .bracket-label {{ color: #00a000; }} 
    .group-orange .bracket-visual {{ border-color: #ff8c00; }} .group-orange .bracket-label {{ color: #d07000; }} 
    .group-red .bracket-visual {{ border-color: #ff0000; }} .group-red .bracket-label {{ color: #c00000; }} 
    
    details.cat-details {{ margin-bottom: 5px; border-radius: 4px; overflow: hidden; }} 
    details.cat-details summary {{ cursor: pointer; padding: 6px 10px; font-weight: 600; display: flex; justify-content: space-between; list-style: none; outline: none; }} 
    details.cat-details[open] summary .arrow {{ transform: rotate(90deg); }} 
    .arrow {{ display: inline-block; margin-right: 6px; transition: transform 0.15s; }} 
    .cat-content {{ padding: 6px 8px 10px; background: #fff; border-top: 1px solid rgba(0,0,0,0.05); }} 
    table {{ width: 100%; border-collapse: collapse; margin-top: 4px; font-size: 0.9rem; background: #fff; }} 
    th, td {{ border-bottom: 1px solid #eee; padding: 4px 6px; text-align: left; vertical-align: top; }} 
    th {{ background: #f9f9f9; position: sticky; top: 0; font-size: 0.85rem; color: #555; }} 
    td:nth-child(5) {{ color: #444; }}
    </style></head><body>""")
    
    html_parts.append(f"<h1>Analyse CPS : {html.escape(source_filename)}</h1>")
    
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
    
    html_parts.append(f"""<div class='summary'><div class='summary-header'><div class='summary-text'><p style='margin:2px 0;'><strong>Total :</strong> {total_raw} ST <span style='font-size:0.85em; color:#777;'>(dont {excluded_count} exclus)</span></p><p style='margin:2px 0;'><strong>Moyenne :</strong> {avg_cps:.2f} CPS</p></div><div class='pie-wrapper'><div class='pie' style='background:{pie_bg};'></div><div style='position:absolute; top:-12px; left:{arrow_pos:.1f}%; transform:translateX(-50%); border-left:8px solid transparent; border-right:8px solid transparent; border-top:14px solid #333;'></div><div style='position:absolute; top:-32px; left:{arrow_pos:.1f}%; transform:translateX(-50%); font-size:0.75em; font-weight:700;'>Médiane: {median_cps:.2f}</div></div></div><div class='barcode-wrapper'><div class='barcode' style='background:{barcode_bg};'></div><p class='caption'>Progression chronologique</p></div></div>""")
    html_parts.append("<div class='cat-section'><h2>Répartition</h2>")
    
    for grp in groups:
        has_content = any(counts[c] > 0 for c in grp["cats"])
        if not has_content: continue
        
        warning_html = ""
        if grp["name"] == "red" and grp["pct"] > 10:
            warning_html = "<div class='warning-icon' title='Attention : Les sous-titres très rapides (> 19 CPS) dépassent 10% du total.'>⚠️</div>"
        elif grp["name"] == "green" and grp["pct"] < 70:
            warning_html = "<div class='warning-icon' title='Attention : Moins de 70% des sous-titres sont dans la zone de confort (Vert).'>⚠️</div>"

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
# APP STREAMLIT (Version Expert V7)
# ==================================================================================

def main():
    st.set_page_config(page_title="Audit Lisibilité Charte 2011", page_icon="⚖️", layout="centered")
    
    # --- GTM ---
    GTM_ID = "GTM-W972MJXS"
    inject_gtm(GTM_ID)
    
    # --- SIDEBAR EXPERTISE ---
    with st.sidebar:
        st.markdown("### ⚖️ Outil de contrôle")
        st.info("""
        **Cet outil vérifie la conformité des fichiers SME avec les exigences de lisibilité (Charte Arcom 2011).**
        
        Il vise à différencier une adaptation humaine soignée d'une transcription littérale automatisée.
        """)
        st.markdown("---")
        st.markdown("#### 👤 À propos")
        st.caption("""
        **Développé par un expert accessibilité :**
        * Co-auteur de la Charte Qualité Arcom 2011
        * Ex-Président du CAASEM
        * Membre fondateur AVA
        """)
        st.markdown("---")
        st.caption("v1.0 - Usage libre pour le contrôle qualité.")

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
        st.header("Le CPS : Un outil, pas un verdict")
        st.write("""
        Le CPS (Caractères Par Seconde) n'est pas une vérité absolue. Il existe des cas où le dépasser est justifié (formules figées, contexte visuel évident).
        
        Cependant, **l'analyse statistique** permet de révéler la méthode de fabrication d'un fichier :
        """)
        
        col1, col2 = st.columns(2)
        with col1:
            st.success("**✅ L'Adaptation Humaine**")
            st.caption("L'adaptateur reformule pour respecter le temps de lecture.")
            st.markdown("- **Vert (>70%)** : Majoritaire")
            st.markdown("- **Rouge (<10%)** : Exceptionnel")
        with col2:
            st.error("**❌ La Transcription Brute**")
            st.caption("L'IA ou le low-cost transcrit tout ce qui est dit, sans adapter.")
            st.markdown("- **Vert (<60%)** : Insuffisant")
            st.markdown("- **Rouge (>15%)** : Illisible")

        st.markdown("---")
        st.subheader("Les 3 constats de l'expert")
        with st.expander("1. Le CPS ne mesure pas la charge cognitive réelle"):
            st.write("Le CPS traite tous les caractères de la même façon (lettres, chiffres, ponctuation). Or, l'œil lit des mots, pas des signes. Une phrase bien ponctuée est plus facile à lire, même si elle contient plus de caractères.")
        with st.expander("2. Quand satisfaire le CPS dégrade la lisibilité"):
            st.write("Reformuler à l'extrême pour 'passer au vert' peut nuire à la compréhension (synonymes rares) ou à la confiance du spectateur (décalage avec la lecture labiale). L'accessibilité, c'est l'équilibre.")
        with st.expander("3. L'industrialisation menace l'accessibilité"):
            st.write("Les outils de transcription automatique (IA) excellent dans l'audio-to-text mais échouent dans le text-to-subtitle. Ils produisent des découpages 'au kilomètre' qui saturent la lecture.")

    # --- ONGLET AUDIT (APP) ---
    with tab_audit:
        st.info("👇 Déposez vos fichiers **.srt** pour vérification.", icon="📂")

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
                    
                    # Calcul des pourcentages pour le verdict
                    real_cues = [c for c in cues if not is_indicator(c["text_display"])]
                    total = len(real_cues)
                    stats = {i: 0 for i in range(1, 8)}
                    for c in real_cues: stats[c["category"]] += 1
                    
                    pct_green = (sum(stats[i] for i in range(1, 4)) / total * 100) if total else 0
                    pct_red = (sum(stats[i] for i in range(5, 8)) / total * 100) if total else 0
                    
                    # Verdict Logic
                    is_compliant = (pct_green >= 70) and (pct_red <= 10)
                    
                    results.append({
                        "filename": uploaded_file.name,
                        "html": html_report,
                        "stem": uploaded_file.name.rsplit('.', 1)[0],
                        "pct_green": pct_green,
                        "pct_orange": (stats[4]/total*100) if total else 0,
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
                            st.success("✅ **PROFIL STANDARD DÉTECTÉ** (Adaptation probable)")
                        else:
                            st.error("⚠️ **ALERTE DENSITÉ** (Risque de transcription littérale)")
                            st.caption("Le fichier dépasse les seuils de tolérance (Rouge > 10% ou Vert < 70%).")
                    
                    with c2:
                        st.download_button(
                            label="⬇️ Rapport HTML",
                            data=res["html"],
                            file_name=f"{res['stem']}_Audit.html",
                            mime="text/html",
                            key=f"dl_{res['filename']}"
                        )

                    st.divider()

                    # GRAPHIQUE COMPARATIF (Votre Fichier vs Standard)
                    # Création d'un petit dataframe pour le chart
                    data = pd.DataFrame({
                        'Catégorie': ['Vert (Confort)', 'Vert (Confort)', 'Orange (Rapide)', 'Orange (Rapide)', 'Rouge (Excessif)', 'Rouge (Excessif)'],
                        'Source': ['Votre Fichier', 'Cible Qualité', 'Votre Fichier', 'Cible Qualité', 'Votre Fichier', 'Cible Qualité'],
                        'Pourcentage': [res['pct_green'], 80, res['pct_orange'], 15, res['pct_red'], 5],
                        'Color': ['#4CAF50', '#81C784', '#FF9800', '#FFB74D', '#F44336', '#E57373']
                    })
                    
                    # On utilise Altair (natif Streamlit) pour un chart horizontal empilé ou côte à côte
                    # Ici simple bar chart groupé
                    st.markdown("**Comparatif avec le standard qualité (Charte 2011)**")
                    
                    # Affichage simplifié via colonnes pour éviter lourdeur altair si pas nécessaire
                    col_g, col_o, col_r = st.columns(3)
                    with col_g:
                        st.metric("Zone Verte", f"{res['pct_green']:.1f}%", delta=f"{res['pct_green']-80:.1f}% vs Cible", delta_color="normal")
                    with col_o:
                        st.metric("Zone Orange", f"{res['pct_orange']:.1f}%", delta=f"{res['pct_orange']-15:.1f}% vs Cible", delta_color="inverse")
                    with col_r:
                        st.metric("Zone Rouge", f"{res['pct_red']:.1f}%", delta=f"{res['pct_red']-5:.1f}% vs Cible", delta_color="inverse")
                    
                    # Prévisualisation HTML (Accordéon fermé par défaut pour clarté)
                    with st.expander("👁️ Voir le détail des sous-titres"):
                        components.html(res["html"], height=500, scrolling=True)

if __name__ == "__main__":
    main()
