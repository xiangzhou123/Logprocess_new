"""
Uso:
    python catalogo.py --start-date 2026-06-28 --end-date 2026-06-29 \
        --output "C:\\ruta\\catalogo.html"
"""

from __future__ import annotations
import argparse
import json
import logging
import warnings
from datetime import date, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
from tqdm.auto import tqdm
from datetime import datetime, timedelta
import sys
from src.api import getHistoricoMOW
from src.api.APIs import getCatalogue
from src.utils import (
    isValidCode,
    loadEstacionSinCTC,
    loadEstaciones,
    rellenarId,
)

# Si uploadSharepoint existe en src/api/APIs, lo usamos para subir el HTML
# generado a SharePoint además de guardarlo en local. Si no está disponible,
# el informe se sigue guardando solo en local, como hasta ahora.
try:
    from src.api.APIs import uploadSharepoint
    SHAREPOINT_DISPONIBLE = True
except ImportError:
    SHAREPOINT_DISPONIBLE = False

warnings.simplefilter(action="ignore", category=FutureWarning)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("catalogo")

if not SHAREPOINT_DISPONIBLE:
    log.warning("uploadSharepoint no encontrado en src.api.APIs; el informe solo se guardará en local.")

DEFAULT_OUTPUT = Path(
    r"C:\Users\xiangzhou.zhang\OneDrive - Ingeniería y Economía del Transporte S.A"
    r"\Backlog\Data\Informe_puntual\catalogo.html"
)
# Carpeta destino en SharePoint (relativa a ".../_Análisis Calidad Datos MSE y MIE/")
CARPETA_SHAREPOINT = "00.Rotulación-Fiabilidad-Supresiones/Catálogo"
PREFIJOS_EXCLUIDOS = ("LAV3", "LAV4", "LAV6")


# ─────────────────────────────────────────────────────────────────────────────
# Carga de históricos MOW
# ─────────────────────────────────────────────────────────────────────────────
def cargar_historico(
    start_date: str,
    end_date: str,
    estaciones: list[str],
    trenes: list[str],
    xSIV: bool,
    JCTC: bool,
    pro: bool = True,
) -> pd.DataFrame:
    """Descarga el histórico MOW y lo filtra a movimientos válidos en rango."""
    # Si la fecha de fin no es posterior a la de inicio, se asume día siguiente
    if end_date <= start_date:
        end_date = (pd.to_datetime(start_date) + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    historico = getHistoricoMOW(
        estaciones=estaciones, trenes=trenes, inicio=start_date, fin=end_date, pro=pro, xSIV=xSIV, jCTC=JCTC
    )
    historico = historico[
        (historico["Fecha"] >= pd.to_datetime(start_date)) & (historico["Fecha"] <= pd.to_datetime(end_date))
    ]
    # Usamos movimientos auditados (válidos) y con Movimiento informado
    historico = historico[historico["NTécnico"].apply(isValidCode)].dropna(subset=["Movimiento"])
    return historico


# ─────────────────────────────────────────────────────────────────────────────
# Catálogo declarado por cada CTC
# ─────────────────────────────────────────────────────────────────────────────
def cargar_catalogo_ctcs(ctcs: list[str]) -> pd.DataFrame:
    """Descarga el catálogo publicado por cada CTC y los concatena en un único DataFrame."""
    catalogos = []
    for ctc in tqdm(ctcs, desc="Descargando catálogos CTC"):
        catalogo_ctc = getCatalogue(ctc)
        if catalogo_ctc is not None:
            catalogos.append(catalogo_ctc)
        else:
            log.warning("CTC %s no devolvió catálogo", ctc)

    if not catalogos:
        raise RuntimeError("Ningún CTC devolvió catálogo: no hay datos para comparar.")

    return pd.concat(catalogos, ignore_index=True)


def cargar_estaciones() -> pd.DataFrame:
    """Carga el maestro de estaciones (con y sin CTC) unificado y sin duplicados."""
    estaciones = pd.concat(
        [loadEstaciones(), loadEstacionSinCTC()],
        ignore_index=True,
    )
    estaciones = estaciones[["Catálogo", "CTC", "NombreCTC", "Mnemónico", "Nombre"]].copy()
    estaciones = estaciones.drop_duplicates(subset=["Catálogo", "CTC", "NombreCTC", "Mnemónico"])
    return estaciones.rename(columns={"Mnemónico": "Acrónimo"})


# ─────────────────────────────────────────────────────────────────────────────
# Comparación catálogo vs. histórico recibido
# ─────────────────────────────────────────────────────────────────────────────
def comparar_catalogo_vs_historico(
    historico: pd.DataFrame, catalogo: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Devuelve dos DataFrames:
      - elementos_no_recibidos: en el catálogo CTC pero de los que no llega información.
      - elementos_no_existentes: llegan por MOW pero no están (correctos) en el catálogo.
    """
    elementos_existentes = (
        historico.drop_duplicates(subset=["CTC", "Mnemónico", "Elemento"])[["CTC", "Mnemónico", "Elemento"]]
        .rename(columns={"Mnemónico": "Acrónimo"})
        .copy()
    )

    elementos_no_recibidos = (
        catalogo.merge(elementos_existentes, on=["CTC", "Acrónimo", "Elemento"], how="left", indicator=True)
        .query('_merge == "left_only"')
        .drop(columns="_merge")
    )

    elementos_no_existentes = (
        elementos_existentes.merge(catalogo, on=["CTC", "Acrónimo", "Elemento"], how="left", indicator=True)
        .query('_merge == "left_only"')
        .drop(columns="_merge")
    )
    # Se excluyen las series de LAV que no se controlan en catálogo
    elementos_no_existentes = elementos_no_existentes[
        ~elementos_no_existentes["Elemento"].str.startswith(PREFIJOS_EXCLUIDOS, na=False)
    ]

    return elementos_no_recibidos, elementos_no_existentes



def build_color_map(dict_a: dict, dict_b: dict) -> dict:
    """Color de cada tarjeta NombreCTC en función del nº total de elementos
    en el panel A / caja izquierda (dict_a) para esa clave."""
    colors = {}
    all_keys = set(dict_a.keys()) | set(dict_b.keys())
    for key in all_keys:
        total_a = sum(len(v) for v in dict_a.get(key, {}).values())
        if total_a == 0:
            colors[key] = ['#e0e0e0', '#9e9e9e', '#424242']
        elif total_a > 10:
            colors[key] = ['#ef5350', '#b71c1c', '#424242']
        elif total_a >= 6:
            colors[key] = ['#fbc02d', '#f57f17', '#424242']
        else:
            colors[key] = ['#66bb6a', '#2e7d32', '#424242']
    return colors


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ["CTC", "Acrónimo", "Elemento", "TipoId", "Catálogo", "NombreCTC", "Nombre"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    return df


def _build_nested_dict(df: pd.DataFrame) -> dict:
    """NombreCTC -> { Nombre -> [ 'TipoId.Elemento', ... ] }"""
    result: dict[str, dict[str, list[str]]] = {}
    if df.empty:
        return result
    for ctc_name, g1 in df.groupby("NombreCTC", sort=False):
        sub: dict[str, list[str]] = {}
        for nombre, g2 in g1.groupby("Nombre", sort=False):
            items = [f"{row.TipoId}.{row.Elemento}" for row in g2.itertuples(index=False)]
            sub[nombre] = items
        result[ctc_name] = sub
    return result


def build_html(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    label_a: str,
    label_b: str,
    output_title: str,
    display_label_a: str | None = None,
    display_label_b: str | None = None,
) -> str:
    """
    Construye el HTML a partir de dos DataFrames con columnas:
        CTC | Acrónimo | Elemento | TipoId | Catálogo | NombreCTC | Nombre

    df_a -> panel izquierdo  (p.ej. elementos_no_existentes_completo)
    df_b -> panel derecho    (p.ej. elementos_no_recibidos_completo)

    Navegación en dos niveles:
      1) Tarjetas de NombreCTC (coloreadas según el volumen de elementos
         del panel izquierdo, df_a). Al hacer clic se despliega un panel
         con tarjetas de Nombre (pertenecientes a ese NombreCTC).
      2) Al hacer clic en una tarjeta de Nombre se muestra el detalle
         (panel A / panel B) con los elementos "TipoId.Elemento"
         agrupados por TipoId — sin sub-agrupar por Nombre.
    """
    df_a = _norm(df_a)
    df_b = _norm(df_b)

    display_label_a = (display_label_a or label_a).strip()
    display_label_b = (display_label_b or label_b).strip()

    dict_a = _build_nested_dict(df_a)
    dict_b = _build_nested_dict(df_b)

    if not dict_a and not dict_b:
        raise ValueError("Ambos DataFrames están vacíos o no contienen las columnas esperadas.")

    # ── Colores (según caja izquierda) y totales ────────────────────────────
    colors = build_color_map(dict_a, dict_b)
    all_keys = sorted(set(dict_a) | set(dict_b))
    total_a = sum(len(v) for sub in dict_a.values() for v in sub.values())
    total_b = sum(len(v) for sub in dict_b.values() for v in sub.values())

    colors_json   = json.dumps({k: list(v) for k, v in colors.items()}, ensure_ascii=False)
    dict_a_json   = json.dumps(dict_a, ensure_ascii=False)
    dict_b_json   = json.dumps(dict_b, ensure_ascii=False)
    all_keys_json = json.dumps(all_keys, ensure_ascii=False)

    html = rf"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{output_title}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: system-ui, sans-serif; background: #f4f4f2; color: #1a1a1a; }}
header {{ background: #1a1a2e; color: #fff; padding: 1.25rem 2rem;
          display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }}
header .header-left h1 {{ font-size: 1.3rem; font-weight: 500; }}
header .header-left p  {{ font-size: 0.85rem; opacity: 0.55; margin-top: 3px; }}
.export-btn {{ flex-shrink: 0; background: #4f46e5; color: white; border: none;
               padding: 8px 18px; border-radius: 8px; font-size: 13px; font-weight: 600;
               cursor: pointer; transition: background .15s; white-space: nowrap;
               align-self: center; }}
.export-btn:hover {{ background: #4338ca; }}
.export-btn:active {{ background: #3730a3; }}
.stats {{ display: flex; gap: 12px; flex-wrap: wrap; padding: 1.25rem 2rem 0; }}
.stat {{ background: #fff; border-radius: 8px; padding: .75rem 1.25rem;
         border: 1px solid #e8e8e8; min-width: 120px; }}
.stat .val {{ font-size: 1.6rem; font-weight: 600; }}
.stat .lbl {{ font-size: 11px; color: #888; margin-top: 2px; font-weight: 700; }}
.keys-section {{ padding: 1.25rem 2rem 0; }}
.keys-section h2 {{ font-size: 11px; text-transform: uppercase; letter-spacing: .07em;
                    color: #aaa; margin-bottom: 10px; }}
.keys-grid {{ display: flex; flex-wrap: wrap; gap: 10px; }}
.key-card {{ padding: 10px 22px; border-radius: 10px; border: 2px solid transparent;
             cursor: pointer; font-size: 14px; font-weight: 700;
             transition: transform .12s, box-shadow .12s; user-select: none; }}
.key-card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 14px rgba(0,0,0,.12); }}
.key-card.active {{ box-shadow: 0 0 0 3px rgba(0,0,0,.18); }}
.name-card {{ padding: 8px 18px; border-radius: 10px; border: 2px solid #e0e0dc;
              background: #fff; color: #333; cursor: pointer; font-size: 13px; font-weight: 600;
              transition: transform .12s, box-shadow .12s, background .12s; user-select: none; }}
.name-card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 14px rgba(0,0,0,.10); }}
.name-card.active {{ background: #e0e7ff; border-color: #4f46e5; color: #312e81; }}
.name-card .nc-count {{ font-size: 10.5px; font-weight: 400; opacity: .65; margin-left: 4px; }}
.names-section {{ padding: 1.25rem 2rem 0; }}
.names-section h2 {{ font-size: 11px; text-transform: uppercase; letter-spacing: .07em;
                     color: #aaa; margin-bottom: 10px; }}
.detail {{ padding: 1.25rem 2rem 2rem; }}
.detail-placeholder {{ display: flex; align-items: center; justify-content: center;
                        height: 140px; color: #ccc; font-size: 14px; }}
.detail-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 1.25rem;
                  padding-bottom: 10px; border-bottom: 1px solid #e8e8e8; }}
.detail-header h2 {{ font-size: 1.3rem; font-weight: 700; }}
.detail-header .breadcrumb {{ font-size: 12px; color: #999; font-weight: 500; }}
.dict-panels {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
@media (max-width: 680px) {{ .dict-panels {{ grid-template-columns: 1fr; }} }}
.dict-panel {{ background: #fff; border-radius: 10px; border: 1px solid #e8e8e8;
               padding: 14px 16px; }}
.dict-panel h3 {{ font-size: 12px; font-weight: 700; text-transform: uppercase;
                  letter-spacing: .06em; margin-bottom: 10px; padding-bottom: 8px;
                  border-bottom: 1px solid #f0f0f0; }}
.dict-panel.a h3 {{ color: #2563eb; }}
.dict-panel.b h3 {{ color: #059669; }}
.dict-panel.empty-panel {{ color: #bbb; font-size: 13px; display: flex;
                            align-items: center; justify-content: center; min-height: 80px; }}
.type-block {{ margin-bottom: 10px; }}
.type-lbl {{ font-size: 10px; text-transform: uppercase; letter-spacing: .06em;
             color: #bbb; margin-bottom: 4px; }}
.pills {{ display: flex; flex-wrap: wrap; gap: 5px; min-height: 30px;
          padding: 4px; border-radius: 6px; }}
.pills.empty {{ border: 1px dashed #d0d0d0; min-height: 50px;
                display: flex; align-items: center; justify-content: center;
                color: #999; font-size: 11px; font-style: italic; }}
.pill {{ font-size: 12px; padding: 3px 11px; border-radius: 99px;
         background: #f5f5f3; border: 1px solid #e4e4e0;
         transition: all .15s; position: relative; }}
.pill:hover {{ background: #e8e8e6; border-color: #d0d0cc; }}
.toast {{ position: fixed; bottom: 24px; right: 24px; background: #1a1a2e; color: white;
          padding: 12px 20px; border-radius: 8px; font-size: 13px; font-weight: 500;
          opacity: 0; transform: translateY(10px); transition: all .3s;
          pointer-events: none; z-index: 9999; }}
.toast.show {{ opacity: 1; transform: translateY(0); }}
</style>
</head>
<body>
<div class="toast" id="toast">✓ HTML exportado correctamente</div>
<header>
  <div class="header-left">
    <h1>{output_title}</h1>
    <p>Clic en un CTC para ver sus Enclavamientos | Clic en un Enclavamiento para ver el detalle | Color de CTC según nº de elementos en "{display_label_a}" | Gris = Sin elementos | Rojo = +10 | Naranja = 6-10 | Verde = 1-5</p>
  </div>
  <button class="export-btn" onclick="exportHTML()">⬇️ Exportar HTML</button>
</header>
<div class="stats">
  <div class="stat"><div class="val">{len(all_keys)}</div><div class="lbl">CTC totales Analizados</div></div>
  <div class="stat"><div class="val">{total_a}</div><div class="lbl">{display_label_a}</div></div>
  <div class="stat"><div class="val">{total_b}</div><div class="lbl">{display_label_b}</div></div>
</div>
<div class="keys-section">
  <h2>CTC — selecciona uno</h2>
  <div class="keys-grid" id="keysGrid"></div>
</div>
<div class="names-section" id="namesSection" style="display:none">
  <h2>Enclavamiento — selecciona uno</h2>
  <div class="keys-grid" id="namesGrid"></div>
</div>
<div class="detail" id="detail">
  <div class="detail-placeholder">Selecciona un NombreCTC y luego un Nombre para ver el detalle</div>
</div>
<script>
const DICT_A   = {dict_a_json};
const DICT_B   = {dict_b_json};
const LABEL_A  = {json.dumps(display_label_a)};
const LABEL_B  = {json.dumps(display_label_b)};
const ALL_KEYS = {all_keys_json};
const COLORS   = {colors_json};
const DEF_COLOR = ['#f1f1ef','#aaa','#444'];

function escAttr(s) {{
  return String(s)
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}}
function color(k) {{ return COLORS[k] || DEF_COLOR; }}

// "TipoId.Elemento" -> {{type: TipoId, id: Elemento, full}}
function parse(s) {{
  const idx = s.indexOf('.');
  if (idx === -1) return {{ type: '?', id: s, full: s }};
  return {{ type: s.slice(0, idx), id: s.slice(idx + 1), full: s }};
}}
function byType(items) {{
  const m = {{}};
  items.forEach(i => {{ const p = parse(i); (m[p.type] = m[p.type] || []).push(p); }});
  return m;
}}
function getAllTypes(items) {{ return [...new Set(items.map(i => parse(i).type))].sort(); }}

function nameColor(count) {{
  if (count === 0)  return ['#f1f1ef','#bbb'];
  if (count > 10)   return ['#fde8e8','#ef5350'];
  if (count >= 6)   return ['#fff6dd','#fbc02d'];
  return ['#e8f7ea','#66bb6a'];
}}

let activeKey     = null;  // NombreCTC seleccionado
let activeNombre  = null;  // Nombre seleccionado dentro del NombreCTC

// ── Nivel 1: NombreCTC ────────────────────────────────────────────────────
function renderKeys() {{
  document.getElementById('keysGrid').innerHTML = ALL_KEYS.map(k => {{
    const [bg, border, text] = color(k);
    return `<div class="key-card ${{activeKey === k ? 'active' : ''}}"
                 style="background:${{bg}};border-color:${{border}};color:${{text}}"
                 data-key="${{escAttr(k)}}"
                 onclick="selectKey(this.dataset.key)">${{k}}</div>`;
  }}).join('');
}}
function selectKey(k) {{
  activeKey     = activeKey === k ? null : k;
  activeNombre  = null;
  renderKeys(); renderNames(); renderDetail();
}}

// ── Nivel 2: Nombre (panel que se despliega al elegir un NombreCTC) ───────
function renderNames() {{
  const section = document.getElementById('namesSection');
  if (!activeKey) {{ section.style.display = 'none'; document.getElementById('namesGrid').innerHTML = ''; return; }}
  section.style.display = '';
  const nombresA = Object.keys(DICT_A[activeKey] || {{}});
  const nombresB = Object.keys(DICT_B[activeKey] || {{}});
  const nombres  = [...new Set([...nombresA, ...nombresB])].sort();
  document.getElementById('namesGrid').innerHTML = nombres.map(n => {{
    const countA = (DICT_A[activeKey] && DICT_A[activeKey][n]) ? DICT_A[activeKey][n].length : 0;
    const countB = (DICT_B[activeKey] && DICT_B[activeKey][n]) ? DICT_B[activeKey][n].length : 0;
    const [bg, border] = nameColor(countA);
    return `<div class="name-card ${{activeNombre === n ? 'active' : ''}}"
                 style="background:${{bg}};border-color:${{border}}"
                 data-nombre="${{escAttr(n)}}"
                 onclick="selectNombre(this.dataset.nombre)">${{n}}<span class="nc-count">(${{countA}} / ${{countB}})</span></div>`;
  }}).join('');
}}
function selectNombre(n) {{
  activeNombre  = activeNombre === n ? null : n;
  renderDetail();
}}

// ── Nivel 3: Detalle ────────────────────────────────────────────────────────
function buildPillHTML(e) {{
  return `<span class="pill" title="${{escAttr(e.full)}}">${{e.full}}</span>`;
}}

function buildPanel(items, cls, label) {{
  if (!items || !items.length)
    return `<div class="dict-panel ${{cls}} empty-panel">Sin datos en este diccionario</div>`;
  const allTypes = getAllTypes(items);
  const grouped  = byType(items);

  const sections = allTypes.map(t => {{
    const els = grouped[t] || [];
    const pillsHTML = els.map(e => buildPillHTML(e)).join('');
    const pillsCls  = els.length ? 'pills' : 'pills empty';
    const emptyTxt  = els.length ? '' : 'Sin elementos';
    return `
      <div class="type-block">
        <div class="type-lbl">${{t}} (${{els.length}})</div>
        <div class="${{pillsCls}}">${{pillsHTML || emptyTxt}}</div>
      </div>`;
  }}).join('');

  return `
    <div class="dict-panel ${{cls}}">
      <h3>${{label}} — ${{items.length}} elementos</h3>
      ${{sections}}
    </div>`;
}}

function renderDetail() {{
  const detail = document.getElementById('detail');
  if (!activeKey || !activeNombre) {{
    detail.innerHTML = '<div class="detail-placeholder">Selecciona un NombreCTC y luego un Nombre para ver el detalle</div>';
    return;
  }}
  const itemsA = (DICT_A[activeKey] && DICT_A[activeKey][activeNombre]) || [];
  const itemsB = (DICT_B[activeKey] && DICT_B[activeKey][activeNombre]) || [];
  detail.innerHTML = `
    <div class="detail-header">
      <h2>${{activeNombre}}</h2>
      <span class="breadcrumb">${{activeKey}} → ${{activeNombre}}</span>
    </div>
    <div class="dict-panels">
      ${{buildPanel(itemsA, 'a', LABEL_A)}}
      ${{buildPanel(itemsB, 'b', LABEL_B)}}
    </div>`;
}}

// ── Exportar HTML ────────────────────────────────────────────────────────────
function exportHTML() {{
  const src  = document.documentElement.outerHTML;
  const blob = new Blob([src], {{ type: 'text/html;charset=utf-8' }});
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url; a.download = '{output_title}.html';
  document.body.appendChild(a); a.click();
  document.body.removeChild(a); URL.revokeObjectURL(url);
  const toast = document.getElementById('toast');
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2500);
}}

renderKeys(); renderNames(); renderDetail();
</script>
</body>
</html>"""
    return html


# ─────────────────────────────────────────────────────────────────────────────
# Orquestación
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    today = datetime.today().date()
    yesterday = today - timedelta(days=1)
    parser = argparse.ArgumentParser(description="Genera el informe de catálogo virtual (HTML).")
    parser.add_argument(
    "--start-date",
    default=yesterday.strftime("%Y-%m-%d"),
    help="Fecha de inicio (YYYY-MM-DD)."
    )
    parser.add_argument(
    "--end-date",
    default=today.strftime("%Y-%m-%d"),
    help="Fecha de fin (YYYY-MM-DD)."
    )
    # parser.add_argument(
    #     "--output", type=Path, default=DEFAULT_OUTPUT, help="Ruta del HTML de salida (guardado local)."
    # )
    parser.add_argument(
        "--carpeta-sharepoint",
        default=CARPETA_SHAREPOINT,
        help="Carpeta destino en SharePoint (relativa a '.../_Análisis Calidad Datos MSE y MIE/').",
    )
    parser.add_argument(
        "--no-jctc", action="store_false", dest="jctc", help="Desactiva el filtro JCTC (activo por defecto)."
    )
    parser.add_argument("--xsiv", action="store_true", help="Activa el filtro xSIV (desactivado por defecto).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    log.info("Descargando histórico MOW (%s → %s)…", args.start_date, args.end_date)
    trenes = [rellenarId(f"{i}") for i in np.arange(100_000)]
    historico = cargar_historico(
        start_date=args.start_date,
        end_date=args.end_date,
        estaciones=[],
        trenes=trenes,
        xSIV=args.xsiv,
        JCTC=args.jctc,
        pro=True,
    )

    ctcs = historico["CTC"].unique().tolist()
    log.info("%d CTC detectados en el histórico.", len(ctcs))

    catalogo = cargar_catalogo_ctcs(ctcs)
    elementos_no_recibidos, elementos_no_existentes = comparar_catalogo_vs_historico(historico, catalogo)

    estaciones = cargar_estaciones()
    elementos_no_recibidos_completo = elementos_no_recibidos.merge(estaciones, on=["CTC", "Acrónimo"])
    elementos_no_existentes_completo = elementos_no_existentes.merge(estaciones, on=["CTC", "Acrónimo"])

    log.info(
        "No recibidos: %d | No existentes: %d",
        len(elementos_no_recibidos_completo),
        len(elementos_no_existentes_completo),
    )

    html = build_html(
        df_a=elementos_no_recibidos_completo,
        df_b=elementos_no_existentes_completo,
        label_a="no_existentes",
        label_b="no_recibidos",
        output_title="Catálogo virtual",
        display_label_a="ELEMENTOS EN CATÁLOGO CTC DE LOS QUE NO SE RECIBE INFORMACIÓN",
        display_label_b="ELEMENTOS PUBLICADOS POR CTC QUE NO ESTÁN CORRECTOS EN EL CATÁLOGO",
    )

    # args.output.parent.mkdir(parents=True, exist_ok=True)
    # args.output.write_text(html, encoding="utf-8")
    # log.info("Informe guardado en %s", args.output)
    yesterday_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    nombre_archivo = f"{yesterday_date}_Catálogo.html"
    if SHAREPOINT_DISPONIBLE:
        resultado = uploadSharepoint(
            nombre_archivo=nombre_archivo,
            contenido_archivo=html.encode("utf-8"),
            carpeta=CARPETA_SHAREPOINT,
        )
        if resultado.get("success"):
            log.info("Informe subido correctamente a SharePoint: %s", resultado["file"])
        else:
            log.error("Error al subir el informe a SharePoint: %s", resultado)
    else:
        log.warning("uploadSharepoint no disponible: el informe no se ha subido a SharePoint.")


if __name__ == "__main__":
    sys.exit(main())