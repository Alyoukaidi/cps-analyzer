import streamlit as st
import re
import html
import io
import zipfile

# ==================================================================================
# 1. CONFIGURATION & LOGIQUE CPS (Identique à ton script)
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

# ==================================================================================
# 2. PARSING SRT (Adapté pour lire depuis la mémoire)
# ==================================================================================

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
# 3. GÉNÉRATION HTML (Retourne le string HTML au lieu d'écrire)
# ==================================================================================

def generate_html_string(cues, source_filename: str) -> str:
    total_raw = len(cues)
    if total_raw == 0: return f"<h3>⚠️ {source_filename} : Vide ou mal formé.</h3>"

    real_cues = [c for c in cues if not is_indicator(c["text_display"])]
    excluded_count = total_raw - len(real_cues)
    total = len(real_cues)
    
    if total == 0: return f"<h3>⚠️ {source_filename} : Aucun dialogue analysable.</h3>"

    # Stats
    counts = {i: 0 for i in range(1, 8)}
    for c in real_cues: counts[c["category"]] += 1

    cps_values = [c["cps"] for c in real_cues if c["duration"] > 0]
    avg_cps = sum(cps_values) / len(cps_values) if cps_values else 0.0
    
    median_cps = 0.0
    if cps_values:
        sorted_cps = sorted(cps_values)
        n = len(sorted_cps)
        median_cps = (sorted_cps[n//2 - 1] + sorted_cps[n//2])/2 if n % 2 == 0 else sorted_cps[n//2]

    # Tri des cues
    cues_by_cat = {i: [] for i in range(1, 8)}
    sorted_global = sorted(real_cues, key=lambda c: c["cps"], reverse=True)
    for c in sorted_global: cues_by_cat[c["category"]].append(c)
    for cat in range(1, 8): cues_by_cat[cat].sort(key=lambda c: c["cps"])

    # Pourcentages
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

    # --- HTML HEADER & CSS ---
    html_parts = []
    # Note: On retire <html> et <body> pour l'intégration propre dans Streamlit si besoin,
    # mais pour le téléchargement on garde le full doctype.
    html_parts.append(f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>CPS - {html.escape(source_filename)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 1100px; margin: 20px auto; padding: 0 10px 40px; background: #f5f5f5; color: #333; }}
  h1 {{ font-size: 1.6rem; word-break: break-all; margin-bottom: 20px; }}
  .summary {{ background: #fff; border-radius: 6px; padding: 12px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); margin-bottom: 20px; }}
  .summary-header {{ display: flex; align-items: center; justify-content: space-between; gap: 40px; margin-bottom: 12px; }}
  .pie-wrapper {{ flex-shrink: 0; flex-grow: 1; position: relative; display: flex; justify-content: center; min-width: 200px; }}
  .pie {{ width: 100%; height: 60px; border-radius: 4px; }}
  .barcode-wrapper {{ margin-top: 4px; }}
  .barcode {{ width: 100%; height: 16px; border-radius: 4px; }}
  .caption {{ font-size: 0.85em; color: #666; margin-top: 4px; }}
  .group-container {{ display: flex; margin-bottom: 8px; }}
  .group-list {{ flex-grow: 1; margin-right: 10px; }}
  .group-bracket {{ width: 90px; display: flex; align-items: center; flex-shrink: 0; }}
  .bracket-visual {{
      width: 15px; align-self: stretch;
      border-right-width: 3px; border-right-style: solid; border-left: none;
      border-top-width: 3px; border-top-style: solid;
      border-bottom-width: 3px; border-bottom-style: solid;
      border-top-right-radius: 8px; border-bottom-right-radius: 8px;
      border-top-left-radius: 0; border-bottom-left-radius: 0;
      margin-right: 10px; margin-top: 2px; margin-bottom: 2px; opacity: 0.8;
  }}
  .bracket-label {{ font-weight: 700; font-size: 1.1rem; }}
  .group-green .bracket-visual {{ border-color: #00d000; }}
  .group-green .bracket-label {{ color: #00a000; }}
  .group-orange .bracket-visual {{ border-color: #ff8c00; }}
  .group-orange .bracket-label {{ color: #d07000; }}
  .group-red .bracket-visual {{ border-color: #ff0000; }}
  .group-red .bracket-label {{ color: #c00000; }}
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

    # Pie & Barcode
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

    html_parts.append(f"""<div class='summary'>
    <div class='summary-header'>
        <div class='summary-text'>
            <p style='margin:2px 0;'><strong>Total :</strong> {total_raw} ST <span style='font-size:0.85em; color:#777;'>(dont {excluded_count} exclus)</span></p>
            <p style='margin:2px 0;'><strong>Moyenne :</strong> {avg_cps:.2f} CPS</p>
        </div>
        <div class='pie-wrapper'>
            <div class='pie' style='background:{pie_bg};'></div>
            <div style='position:absolute; top:-12px; left:{arrow_pos:.1f}%; transform:translateX(-50%); border-left:8px solid transparent; border-right:8px solid transparent; border-top:14px solid #333;'></div>
            <div style='position:absolute; top:-32px; left:{arrow_pos:.1f}%; transform:translateX(-50%); font-size:0.75em; font-weight:700;'>Médiane: {median_cps:.2f}</div>
        </div>
    </div>
    <div class='barcode-wrapper'>
        <div class='barcode' style='background:{barcode_bg};'></div>
        <p class='caption'>Progression chronologique</p>
    </div>
    </div>""")

    html_parts.append("<div class='cat-section'><h2>Répartition</h2>")

    for grp in groups:
        has_content = any(counts[c] > 0 for c in grp["cats"])
        if not has_content: continue
            
        html_parts.append(f"<div class='group-container group-{grp['name']}'>")
        html_parts.append("<div class='group-list'>")
        for cat in grp["cats"]:
            count = counts[cat]
            if count == 0: continue
            
            pct = (count / total * 100)
            c_start = interpolate_color(cps_limits[cat][0])
            c_end = interpolate_color(cps_limits[cat][1])
            bg_grad = f"linear-gradient(to right, {c_start} 0%, {c_end if cat > 1 else c_start} {pct:.1f}%, #e0e0e0 {pct:.1f}%, #e0e0e0 100%)"
            
            html_parts.append(f"<details class='cat-details' style='background:{bg_grad}'>")
            html_parts.append(f"<summary><span class='label'><span class='arrow'>▶</span>{cat}. {label_map[cat]}</span><span class='count'>{count} ST ({pct:.1f}%)</span></summary>")
            
            html_parts.append("<div class='cat-content'><table><tr><th>#</th><th>Timecode</th><th>Durée</th><th>CPS</th><th>Texte</th></tr>")
            for c in cues_by_cat[cat]:
                txt = html.escape(c["text_display"][:300]).replace("&lt;br&gt;", "<br>")
                html_parts.append(f"<tr><td>{c['index']}</td><td>{c['start']} → {c['end']}</td><td>{c['duration']:.2f}s</td><td>{c['cps']:.2f}</td><td>{txt}</td></tr>")
            html_parts.append("</table></div></details>")
            
        html_parts.append("</div>") # Fin group-list
        
        html_parts.append("<div class='group-bracket'>")
        html_parts.append("<div class='bracket-visual'></div>")
        html_parts.append(f"<div class='bracket-label'>{grp['pct']:.1f}%</div>")
        html_parts.append("</div>") # Fin group-bracket
        
        html_parts.append("</div>") # Fin group-container

    html_parts.append("</div></body></html>")
    return "".join(html_parts)

# ==================================================================================
# 4. INTERFACE STREAMLIT
# ==================================================================================

def main():
    st.set_page_config(page_title="SRT CPS Analyzer", page_icon="📊", layout="wide")
    
    st.title("📊 Analyseur CPS pour Sous-titres")
    st.markdown("""
    Déposez vos fichiers **.srt** ci-dessous pour générer un rapport visuel de la vitesse de lecture (CPS).
    Le rapport inclut le regroupement par catégories (Vert/Orange/Rouge) et la visualisation par accolade.
    """)

    uploaded_files = st.file_uploader("Choisissez des fichiers .srt", type=["srt"], accept_multiple_files=True)

    if uploaded_files:
        st.divider()
        results = []
        
        # Traitement
        for uploaded_file in uploaded_files:
            # Lire le contenu (tentative UTF-8 puis Latin-1)
            bytes_data = uploaded_file.getvalue()
            try:
                content = bytes_data.decode("utf-8")
            except UnicodeDecodeError:
                content = bytes_data.decode("latin-1")
            
            cues = parse_srt_content(content)
            html_report = generate_html_string(cues, uploaded_file.name)
            
            results.append({
                "filename": uploaded_file.name,
                "html": html_report,
                "stem": uploaded_file.name.rsplit('.', 1)[0]
            })

        # Affichage et Téléchargement
        if len(results) == 1:
            # Un seul fichier : Prévisualisation + Bouton simple
            res = results[0]
            st.success(f"Analyse terminée pour : {res['filename']}")
            
            st.download_button(
                label="📥 Télécharger le rapport HTML",
                data=res["html"],
                file_name=f"{res['stem']}_CPS.html",
                mime="text/html"
            )
            
            # Prévisualisation dans une iframe
            with st.expander("👁️ Voir l'aperçu du rapport ici"):
                st.components.v1.html(res["html"], height=800, scrolling=True)

        else:
            # Plusieurs fichiers : Bouton ZIP
            st.success(f"{len(results)} fichiers analysés avec succès.")
            
            # Création du ZIP en mémoire
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for res in results:
                    zf.writestr(f"{res['stem']}_CPS.html", res["html"])
            
            st.download_button(
                label="📦 Télécharger tous les rapports (ZIP)",
                data=zip_buffer.getvalue(),
                file_name="Rapports_CPS.zip",
                mime="application/zip"
            )

if __name__ == "__main__":
    main()
