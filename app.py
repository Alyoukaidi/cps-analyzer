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
# 1. & 2. LOGIQUE MÉTIER CPS (Inchangée)
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
# GÉNÉRATION HTML (Harmonisée avec le site)
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
    
    # CSS harmonisé avec lisibilite-sme.fr
    html_parts.append(f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
    <title>Analyse CPS : {html.escape(source_filename)}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style>
    body {{ font-family: 'Inter', system-ui, sans-serif; max-width: 1100px; margin: 20px auto; padding: 0 10px 40px; background: #f8fafc; color: #334155; }} 
    h1 {{ font-size: 1.6rem; margin-bottom: 5px; color: #1a365d; font-weight: 700; }} 
    h2 {{ font-size: 1.3rem; margin-top: 30px; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; color: #1a365d; }}
    .subtitle {{ color: #64748b; font-size: 0.95rem; margin-bottom: 25px; }}
    
    .summary {{ background: #fff; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); margin-bottom: 25px; }} 
    .summary-header {{ display: flex; align-items: center; justify-content: space-between; gap: 40px; margin-bottom: 15px; }} 
    
    .pie-wrapper {{ flex-shrink: 0; flex-grow: 1; position: relative; display: flex; justify-content: center; min-width: 250px; }} 
    .pie {{ width: 100%; height: 50px; border-radius: 6px; }} 
    
    .barcode-wrapper {{ margin-top: 10px; }} 
    .barcode {{ width: 100%; height: 12px; border-radius: 4px; opacity: 0.9; }} 
    .caption {{ font-size: 0.8rem; color: #64748b; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }} 
    
    .group-container {{ display: flex; margin-bottom: 15px; }} 
    .group-list {{ flex-grow: 1; margin-right: 15px; }} 
    .group-bracket {{ width: 100px; display: flex; align-items: center; flex-shrink: 0; position: relative; }} 
    
    .bracket-visual {{ width: 15px; align-self: stretch; border-right: 4px solid; border-top: 4px solid; border-bottom: 4px solid; border-top-right-radius: 10px; border-bottom-right-radius: 10px; margin-right: 12px; margin-top: 4px; margin-bottom: 4px; opacity: 0.7; }} 
    .bracket-label-container {{ display: flex; flex-direction: column; align-items: center; justify-content: center; }}
    .bracket-label {{ font-weight: 800; font-size: 1.3rem; line-height: 1.1; }} 
    
    .warning-icon {{ font-size: 1.4rem; cursor: help; margin-top: 6px; color: #ea580c; }}
    
    .group-green .bracket-visual {{ border-color: #2E7D32; }} .group-green .bracket-label {{ color: #2E7D32; }} 
    .group-orange .bracket-visual {{ border-color: #EF6C00; }} .group-orange .bracket-label {{ color: #EF6C00; }} 
    .group-red .bracket-visual {{ border-color: #C62828; }} .group-red .bracket-label {{ color: #C62828; }} 
    
    details.cat-details {{ margin-bottom: 6px; border-radius: 8px; overflow: hidden; border: 1px solid #e2e8f0; }} 
    details.cat-details summary {{ cursor: pointer; padding: 8px 12px; font-weight: 600; display: flex; justify-content: space-between; list-style: none; outline: none; transition: background 0.2s; }} 
    details.cat-details[open] summary .arrow {{ transform: rotate(90deg); }} 
    .arrow {{ display: inline-block; margin-right: 8px; transition: transform 0.15s; color: #64748b; }} 
    
    .cat-content {{ padding: 0; background: #fff; }} 
    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }} 
    th, td {{ border-bottom: 1px solid #f1f5f9; padding: 6px 10px; text-align: left; vertical-align: top; }} 
    th {{ background: #f8fafc; position: sticky; top: 0; font-size: 0.8rem; text-transform: uppercase; color: #64748b; font-weight: 600; }} 
    tr:hover td {{ background: #f8fafc; }}
    </style></head><body>""")
    
    html_parts.append(f"<h1>Analyse CPS : {html.escape(source_filename)}</h1>")
    html_parts.append(f"<div class='subtitle'>Médiane: {median_cps:.2f}</div>")
    
    sorted_by_cps = sorted(real_cues, key=lambda c: c["cps"])
    if not sorted_by_cps: pie_bg = "#e2e8f0"
    else:
        parts = []
        pct_per_st = 100.0 / len(sorted_by_cps)
        for i, cue in enumerate(sorted_by_cps):
            color = interpolate_color(cue["cps"])
            parts.append(f"{color} {i * pct_per_st:.3f}%")
            parts.append(f"{color} {(i+1) * pct_per_st:.3f}%")
        pie_bg = "linear-gradient(to right, " + ", ".join(parts) + ")"
    
    barcode_bg = "#e2e8f0"
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
    
    html_parts.append(f"""<div class='summary'><div class='summary-header'><div class='summary-text'><p style='margin:2px 0; font-size:1.1rem;'><strong>Total :</strong> {total_raw} ST <span style='font-size:0.85em; color:#64748b;'>(dont {excluded_count} exclus : ... / ♪ / ↑)</span></p><p style='margin:2px 0; color:#64748b;'><strong>Moyenne :</strong> {avg_cps:.2f} CPS</p></div><div class='pie-wrapper'><div class='pie' style='background:{pie_bg};'></div><div style='position:absolute; top:-10px; left:{arrow_pos:.1f}%; transform:translateX(-50%); border-left:8px solid transparent; border-right:8px solid transparent; border-top:12px solid #1a365d;'></div><div style='position:absolute; top:-28px; left:{arrow_pos:.1f}%; transform:translateX(-50%); font-size:0.75em; font-weight:700; color:#1a365d;'>Médiane: {median_cps:.2f}</div></div></div><div class='barcode-wrapper'><div class='barcode' style='background:{barcode_bg};'></div><p class='caption'>Progression chronologique dans le fichier</p></div></div>""")
    
    html_parts.append("<div class='cat-section'><h2>Répartition</h2>")
    
    for grp in groups:
        has_content = any(counts[c] > 0 for c in grp["cats"])
        if not has_content: continue
        
        warning_html = ""
        title_attr = ""
        if grp["name"] == "red" and grp["pct"] > 10:
            title_attr = "Seuil critique dépassé (>10% en zone rouge)."
            warning_html = f"<div class='warning-icon' title='{title_attr}'>⚠️</div>"
        elif grp["name"] == "green" and grp["pct"] < 70:
            title_attr = "Taux de confort insuffisant (<70% en zone verte)."
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
# APP STREAMLIT (Version refonte complète)
# ==================================================================================

def main():
    st.set_page_config(
        page_title="Audit de Conformité SME", 
        page_icon="📊", 
        layout="wide",  # Wide mode par défaut
        initial_sidebar_state="collapsed"
    )
    
    # --- GTM ---
    GTM_ID = "GTM-W972MJXS"
    inject_gtm(GTM_ID)
    
    # --- CSS PERSONNALISÉ (minimal pour éviter les conflits) ---
    st.markdown("""
    <style>
    /* Police plus grande */
    html, body, [class*="css"] {
        font-size: 16px !important;
    }
    p, div, span, li {
        font-size: 1.05rem !important;
    }
    /* Harmonisation légère avec lisibilite-sme.fr */
    h1 {
        color: #1a365d !important;
    }
    .stButton>button {
        background-color: #2E7D32;
        color: white;
        border-radius: 8px;
        font-weight: 600;
        border: none;
        padding: 0.5rem 1.5rem;
        transition: all 0.2s;
    }
    .stButton>button:hover {
        background-color: #1b5e20;
    }
    .stDownloadButton>button {
        background-color: #2E7D32;
        color: white;
        border-radius: 8px;
        font-weight: 600;
    }
    .stDownloadButton>button:hover {
        background-color: #1b5e20;
    }
    /* Liens verts */
    a {
        color: #2E7D32;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # --- HEADER avec lien retour ---
    st.markdown("""
    <div style='margin-bottom: 20px;'>
        <a href='https://lisibilite-sme.fr' style='color: #64748b; text-decoration: none; font-size: 0.9rem;'>
            ← Retour au site
        </a>
    </div>
    """, unsafe_allow_html=True)
    
    st.title("AUDIT DE CONFORMITÉ SME")
    st.markdown("**Analyse du temps de lecture selon la Charte Arcom 2011**")
    
    # Laïus explicatif
    st.markdown("""
    Cet outil analyse la **densité de lecture** (CPS - Caractères Par Seconde) des fichiers de sous-titres SME. 
    Il produit un rapport visuel qui permet de vérifier si le sous-titrage respecte les seuils de lisibilité définis 
    par la *Charte relative à la qualité du sous-titrage à destination des personnes sourdes ou malentendantes*.
    
    **Téléversez ou déposez un ou plusieurs fichiers .srt ci-dessous, le résultat s'affichera instantanément.**
    """)
    
    st.markdown("---")

    # Gestion de l'état de l'uploader
    if 'uploader_key' not in st.session_state:
        st.session_state.uploader_key = 0

    def reset_uploader():
        st.session_state.uploader_key += 1

    # --- ZONE D'UPLOAD ---
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
            st.button("🗑️ Reset", on_click=reset_uploader, help="Effacer tous les fichiers")

        with st.status("Analyse en cours...", expanded=True) as status:
            results = []
            for i, uploaded_file in enumerate(uploaded_files):
                status.write(f"Traitement de `{uploaded_file.name}`...")
                
                bytes_data = uploaded_file.getvalue()
                try:
                    content = bytes_data.decode("utf-8")
                except UnicodeDecodeError:
                    content = bytes_data.decode("latin-1")
                
                cues = parse_srt_content(content)
                html_report = generate_html_string(cues, uploaded_file.name)
                
                real_cues = [c for c in cues if not is_indicator(c["text_display"])]
                total = len(real_cues)
                stats = {i: 0 for i in range(1, 8)}
                for c in real_cues: stats[c["category"]] += 1
                
                pct_green = (sum(stats[i] for i in range(1, 4)) / total * 100) if total else 0
                pct_orange = (stats[4] / total * 100) if total else 0
                pct_red = (sum(stats[i] for i in range(5, 8)) / total * 100) if total else 0
                
                # Verdict
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
            
            status.update(label="✅ Analyse terminée", state="complete", expanded=False)

        st.markdown("### 📄 Résultats")
        
        # Bouton ZIP si plusieurs fichiers
        if len(results) > 1:
            # Créer le fichier ZIP en mémoire
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for res in results:
                    zip_file.writestr(f"{res['stem']}_Analyse.html", res["html"])
            zip_buffer.seek(0)
            
            st.download_button(
                label=f"📦 Télécharger tous les rapports ({len(results)} fichiers)",
                data=zip_buffer.getvalue(),
                file_name="Rapports_CPS.zip",
                mime="application/zip",
                key="download_all_zip"
            )
            st.markdown("---")
        
        for res in results:
            with st.container(border=True):
                # Titre et bouton de téléchargement
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.subheader(f"`{res['filename']}`")
                
                with c2:
                    st.download_button(
                        label="⬇️ Télécharger",
                        data=res["html"],
                        file_name=f"{res['stem']}_Analyse.html",
                        mime="text/html",
                        key=f"dl_{res['filename']}"
                    )
                
                # Rapport détaillé
                with st.expander("👁️ Voir le rapport détaillé"):
                    components.html(res["html"], height=500, scrolling=True)
    
    # --- FOOTER ---
    st.markdown("---")
    st.markdown("""
    <div style='text-align: center; color: #64748b; font-size: 0.85rem;'>
        <p>Outil open source · <a href='https://lisibilite-sme.fr/methodologie.html' style='color: #2E7D32;'>Méthodologie</a> · 
        <a href='https://github.com/Alyoukaidi/cps-analyzer' style='color: #2E7D32;'>Code source</a></p>
    </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
