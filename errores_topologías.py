import argparse
import hashlib
import json
import os
import pickle
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import orjson
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# Si uploadSharepoint existe en src/api/APIs, la usamos para subir el HTML
# generado a SharePoint además de guardarlo en local. Si no está disponible,
# el informe se sigue guardando solo en local, como hasta ahora.
try:
    from src.api.APIs import uploadSharepoint
    SHAREPOINT_DISPONIBLE = True
except ImportError:
    SHAREPOINT_DISPONIBLE = False


load_dotenv()
GRAYLOG_URL = os.getenv("GRAYLOG_URL")
GRAYLOG_USER = os.getenv("GRAYLOG_USER")
GRAYLOG_PASSWORD = os.getenv("GRAYLOG_PASSWORD")

if not SHAREPOINT_DISPONIBLE:
    print("Aviso: 'uploadSharepoint' no se encontró en src.api.APIs; el informe solo se guardará en local.")


TOPO_URL = "http://topo.rail.api.elcano.operaciones.adif/msetopo/download/filesInterlock"
DEFAULT_STREAM_ID = "68fb73bc6456d79315e70710"

INTERLOCK_EXCLUSIONS = {
    "BCN": {"XC"},
    "MAC": {"DU", "GO", "AI"},
    "ZAR": {"SAC"},
    "COR": {"LV", "ML"},
}

DEFAULT_OUTPUT_BASE = Path(r"C:\Users\xiangzhou.zhang\ADIF\MSE - 00-CALIDAD DATO\_Análisis Calidad Datos MSE y MIE\16. Error topología")

# Carpeta base destino en SharePoint (relativa a ".../_Análisis Calidad Datos MSE y MIE/")
CARPETA_SHAREPOINT_BASE = "16. Error topología"


def graylog_get(session, url, params, headers, max_retries=5):
    for attempt in range(max_retries):
        try:
            r = session.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 500:
                raise requests.exceptions.HTTPError("500", response=r)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 500:
                raise
            raise
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            wait = 2 ** attempt
            print(f"\n⏱️  Error red — reintento {attempt+1}/{max_retries} en {wait}s...")
            time.sleep(wait)
    raise RuntimeError(f"❌ Fallaron {max_retries} reintentos")


def fetch_window(session, query, from_str, to_str, stream_id, headers, batch_size=5000, _depth=0):
    if _depth > 8:
        print(f"\n⛔ Ventana demasiado densa incluso dividida: {from_str} → {to_str}")
        return []

    messages = []
    offset = 0

    while True:
        params = {
            "query": query, "from": from_str, "to": to_str,
            "limit": batch_size, "offset": offset,
            "fields": "timestamp,source,message,level,contentType",
        }
        if stream_id:
            params["filter"] = f"streams:{stream_id}"

        try:
            data = graylog_get(session, f"{GRAYLOG_URL}/api/search/universal/absolute", params, headers)
            batch = data.get("messages", [])
            total = data.get("total_results", 0)
            messages.extend(batch)
            offset += len(batch)
            print(f"  {from_str} → {to_str} | {offset}/{total}", end="\r")
            if offset >= total or not batch:
                break
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 500:
                t_from = datetime.fromisoformat(from_str.replace("Z", "+00:00"))
                t_to = datetime.fromisoformat(to_str.replace("Z", "+00:00"))
                mid = t_from + (t_to - t_from) / 2
                mid_str = mid.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                print(f"\n✂️  Dividiendo [{from_str} → {to_str}] (offset={offset}, depth={_depth})")
                left = fetch_window(session, query, from_str, mid_str, stream_id, headers, batch_size, _depth + 1)
                right = fetch_window(session, query, mid_str, to_str, stream_id, headers, batch_size, _depth + 1)
                return messages[:offset - len(batch)] + left + right
            raise

    return messages


def get_total_results(session, query, from_str, to_str, stream_id, headers):
    params = {"query": query, "from": from_str, "to": to_str, "limit": 1, "offset": 0, "fields": "timestamp"}
    if stream_id:
        params["filter"] = f"streams:{stream_id}"
    data = graylog_get(session, f"{GRAYLOG_URL}/api/search/universal/absolute", params, headers)
    return data.get("total_results", 0)


def search_logs(session, query, from_date, to_date, stream_id=None, limit=None,
                 max_per_window=5000, checkpoint_file="checkpoint.pkl"):
    headers = {"Accept": "application/json"}
    all_messages = []
    range_start = datetime.fromisoformat(from_date.replace("Z", "+00:00"))
    range_end = datetime.fromisoformat(to_date.replace("Z", "+00:00"))
    total_seconds = (range_end - range_start).total_seconds()

    resume_from = range_start
    if os.path.exists(checkpoint_file):
        print(f"♻️  Reanudando desde checkpoint...")
        with open(checkpoint_file, "rb") as f:
            ckpt = pickle.load(f)
        all_messages = ckpt["messages"]
        resume_from = ckpt["last_window_end"]
        print(f"   {len(all_messages):,} msgs ya descargados, continuando desde {resume_from}\n")

    print("🔍 Calculando total de mensajes...")
    total_global = get_total_results(
        session, query,
        range_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        range_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        stream_id, headers,
    )
    print(f"📊 Total: {total_global:,}")

    if total_global == 0:
        return []

    density = total_global / total_seconds
    window_seconds = max(int((max_per_window / density) * 0.85), 5)
    remaining = (range_end - resume_from).total_seconds()
    print(f"⚙️  Ventana: {window_seconds}s | Estimadas: {int(remaining/window_seconds)+1}\n")

    current = resume_from
    window_count = 0

    while current < range_end:
        window_end = min(current + timedelta(seconds=window_seconds), range_end)
        from_str = current.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        to_str = window_end.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        try:
            batch = fetch_window(session, query, from_str, to_str, stream_id, headers)
            all_messages.extend(batch)
            window_count += 1
            print(f"✅ {from_str} → {to_str} | +{len(batch):,} | Total: {len(all_messages):,}")
        except RuntimeError as e:
            with open(checkpoint_file, "wb") as f:
                pickle.dump({"messages": all_messages, "last_window_end": current}, f)
            print(f"\n💾 Guardado emergencia: {len(all_messages):,} msgs")
            raise e

        if window_count % 50 == 0:
            with open(checkpoint_file, "wb") as f:
                pickle.dump({"messages": all_messages, "last_window_end": window_end}, f)
            print(f"💾 Checkpoint: {len(all_messages):,} msgs")

        current = window_end

        if limit and len(all_messages) >= limit:
            all_messages = all_messages[:limit]
            print(f"\n🛑 Límite alcanzado: {limit:,} msgs")
            break

    if os.path.exists(checkpoint_file):
        os.remove(checkpoint_file)

    print(f"\n✅ Descarga completa: {len(all_messages):,} mensajes")
    return all_messages


COLS_MAP = {
    "element.name": [
        "messageType.ChangeState.trackCircuit.element.name",
        "messageType.ChangeState.block.element.name",
        "messageType.ChangeState.signal.element.name",
        "messageType.ChangeState.levelCrossing.element.name",
    ],
    "element.type": [
        "messageType.ChangeState.trackCircuit.element.type",
        "messageType.ChangeState.block.element.type",
        "messageType.ChangeState.signal.element.type",
        "messageType.ChangeState.levelCrossing.element.type",
    ],
    "element.interlock": [
        "messageType.ChangeState.trackCircuit.element.interlock",
        "messageType.ChangeState.block.element.interlock",
        "messageType.ChangeState.signal.element.interlock",
        "messageType.ChangeState.levelCrossing.element.interlock",
    ],
}


def messages_to_dataframe(messages, chunk_size=10_000):
    lista_messages = [item["message"]["message"] for item in messages]
    dict_list = [orjson.loads(x) for x in lista_messages]

    chunks = [dict_list[i:i + chunk_size] for i in range(0, len(dict_list), chunk_size)]
    dfs = []
    for i, chunk in enumerate(chunks):
        dfs.append(pd.json_normalize(chunk))
        print(f"Chunk {i+1}/{len(chunks)} procesado")

    df = pd.concat(dfs, ignore_index=True)
    df["header.timestampMSG"] = pd.to_datetime(df["header.timestampMSG"], unit="ms")
    return df


def to_camel_case(s):
    if pd.isna(s):
        return ""
    parts = str(s).split()
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])


def build_elementos_publicados(df):
    df_filter = df[["header.ctc", "header.ContentType"]].copy()

    for target_col, source_cols in COLS_MAP.items():
        for col in source_cols:
            if col not in df.columns:
                df[col] = None
        result = df[source_cols[0]]
        for col in source_cols[1:]:
            result = result.combine_first(df[col])
        df_filter[target_col] = result

    df_filter = df_filter.drop_duplicates(keep="first")

    df_filter["ElementoID"] = (
        df_filter["header.ctc"] + "." +
        df_filter["element.interlock"] + "." +
        df_filter["header.ContentType"].apply(to_camel_case) + "." +
        df_filter["element.name"]
    )
    return df_filter


def get_elements(session, interlock, ctc):
    payload = {"interlock": interlock, "ctc": ctc}
    response = session.post(TOPO_URL, json=payload)

    if response.status_code == 200:
        content_type = response.headers.get("Content-Type", "")
    else:
        print(f"Error {response.status_code}: {response.text}")
        return None

    data = response.json()
    config_string = data["topoResource"]["configFileJson"]
    config_object = json.loads(config_string)
    topo_info = {
        "id": config_object["id"],
        "IdCatalogo": config_object["version"]["mieCatalogue"]["id"],
        "VersionCatalogo": config_object["version"]["mieCatalogue"]["version"],
        "IdCTC": config_object["info"]["ctc"]["id"],
        "Mnemónico": config_object["info"]["interlocking"],
        "NombreEnclavamiento": config_object["info"]["name"],
        "Dependencias": [d["code"] for d in config_object["info"]["dependencies"]],
    }

    data_rows = []
    for e in config_object["viewCtc"]["elements"]:
        row = topo_info.copy()
        row.update({
            "ElementoID": e["id"],
            "NombreElemento": e["name"],
            "TipoElemento": e["type"],
            "SubtipoElemento": e["subtype"],
            "NombreCircuito": e["trackCircuitName"],
        })
        data_rows.append(row)

    return pd.DataFrame(data_rows)


def fetch_topologia(session, ctc, interlocking, sleep_seconds=2):
    exclusiones = INTERLOCK_EXCLUSIONS.get(ctc, set())
    interlocking = [i for i in interlocking if i not in exclusiones]

    dfs = []
    for interlock in interlocking:
        try:
            df_topo = get_elements(session, interlock, ctc)
            if df_topo is None or df_topo.empty:
                print(f"{interlock} returned empty")
            else:
                dfs.append(df_topo)
            time.sleep(sleep_seconds)
        except Exception as e:
            print(f"{interlock} failed: {e}")

    return pd.concat(dfs, ignore_index=True)


def merge_dependencias(elementos, df_topos):
    dependencias = df_topos[["Mnemónico", "NombreEnclavamiento"]].copy()
    dependencias.drop_duplicates(subset="Mnemónico", inplace=True)
    return pd.merge(elementos, dependencias, left_on="element.interlock", right_on="Mnemónico", how="left")


def compute_diffs(df_topos, elementos):
    topos_enclavamientos = {i: g for i, g in df_topos.groupby("NombreEnclavamiento")}
    enclavamientos_recibidos = {i: g for i, g in elementos.groupby("NombreEnclavamiento")}

    Topo_sin_recibir = {}
    recibir_sin_topo = {}

    for i in topos_enclavamientos.keys() | enclavamientos_recibidos.keys():
        topo_group = topos_enclavamientos.get(i)
        recibido_group = enclavamientos_recibidos.get(i)

        if topo_group is not None:
            df1_filtrado = topo_group[
                (~topo_group["ElementoID"].str.lower().str.contains("alarm|undefined|operationcontrol", na=False)) &
                (topo_group["SubtipoElemento"] != "trackCircuitNotSignalized")
            ]
            nombres1_dict = {x.lower(): x for x in df1_filtrado["ElementoID"]}
            nombres1_pre_set = set(x.lower() for x in df1_filtrado["NombreCircuito"])
        else:
            nombres1_dict = {}
            nombres1_pre_set = set()

        nombres2_dict = {x.lower(): x for x in recibido_group["ElementoID"]} if recibido_group is not None else {}

        diff1 = set(nombres1_dict.keys()) - set(nombres2_dict.keys())
        diff2 = set(nombres2_dict.keys()) - set(nombres1_dict.keys())

        if diff1:
            Topo_sin_recibir[i] = [nombres1_dict[n] for n in diff1]

        diff2_filtrado = []
        for n in diff2:
            if n not in diff1 and n not in nombres1_pre_set:
                diff2_filtrado.append(nombres2_dict[n])
        if diff2_filtrado:
            recibir_sin_topo[i] = diff2_filtrado

    return Topo_sin_recibir, recibir_sin_topo


def dicts_to_dataframe(topo_sin_recibir, recibir_sin_topo):
    rows = []
    for enclavamiento, elems in topo_sin_recibir.items():
        for elem in elems:
            rows.append({"enclavamiento": enclavamiento, "panel": "Topo_sin_recibir", "elem": elem})
    for enclavamiento, elems in recibir_sin_topo.items():
        for elem in elems:
            rows.append({"enclavamiento": enclavamiento, "panel": "recibir_sin_topo", "elem": elem})
    return pd.DataFrame(rows).sort_values(["enclavamiento", "panel", "elem"]).reset_index(drop=True)


import json
from datetime import date
import pandas as pd


def build_color_map(dict_a: dict, dict_b: dict) -> dict:
    colors = {}
    all_keys = set(dict_a.keys()) | set(dict_b.keys())
    for key in all_keys:
        errors_b = len(dict_b.get(key, []))
        if errors_b == 0:
            colors[key] = ['#e0e0e0', '#9e9e9e', '#424242']
        elif errors_b > 10:
            colors[key] = ['#ef5350', '#b71c1c', '#424242']
        elif errors_b >= 6:
            colors[key] = ['#fbc02d', '#f57f17', '#424242']
        else:
            colors[key] = ['#66bb6a', '#2e7d32', '#424242']
    return colors


def build_html(
    df: pd.DataFrame,
    label_a: str,
    label_b: str,
    output_title: str,
    display_label_a: str | None = None,
    display_label_b: str | None = None,
) -> str:
    """
    Construye el HTML a partir de un DataFrame con columnas:
        enclavamiento | panel | elem

    Parameters
    ----------
    df           : DataFrame con los datos.
    label_a      : Valor de 'panel' que corresponde al diccionario A (columna izquierda).
    label_b      : Valor de 'panel' que corresponde al diccionario B (columna derecha).
    output_title : Título del HTML generado.
    """

    # ── Normalizar ───────────────────────────────────────────────────────────
    df = df.copy()
    df["panel"]         = df["panel"].astype(str).str.strip()
    df["elem"]          = df["elem"].astype(str).str.strip()
    df["enclavamiento"] = df["enclavamiento"].astype(str).str.strip()

    label_a = label_a.strip()
    label_b = label_b.strip()
    display_label_a = (display_label_a or label_a).strip()
    display_label_b = (display_label_b or label_b).strip()

    # ── Validación rápida ────────────────────────────────────────────────────
    # Permite que uno de los dos paneles esté ausente en el DataFrame
    unique_panels = df["panel"].unique().tolist()
    has_a = label_a in unique_panels
    has_b = label_b in unique_panels

    if not has_a and not has_b:
        raise ValueError(
            f"Ninguno de los paneles encontrado en columna 'panel'. "
            f"Esperados: '{label_a}', '{label_b}'. "
            f"Disponibles: {unique_panels}"
        )

    # ── Diccionarios A y B ───────────────────────────────────────────────────
    def build_dict(panel_label: str) -> dict:
        if panel_label not in unique_panels:
            return {}   # panel ausente → dict vacío, columna se mostrará vacía
        sub = df[df["panel"] == panel_label]
        result: dict[str, list[str]] = {}
        for encl, group in sub.groupby("enclavamiento", sort=False):
            result[encl] = group["elem"].tolist()
        return result

    dict_a = build_dict(label_a)
    dict_b = build_dict(label_b)

    # ── Colores y totales ────────────────────────────────────────────────────
    colors   = build_color_map(dict_a, dict_b)
    all_keys = sorted(set(dict_a) | set(dict_b))
    total_a  = sum(len(v) for v in dict_a.values())
    total_b  = sum(len(v) for v in dict_b.values())

    colors_json   = json.dumps({k: list(v) for k, v in colors.items()}, ensure_ascii=False)
    dict_a_json   = json.dumps(dict_a,  ensure_ascii=False)
    dict_b_json   = json.dumps(dict_b,  ensure_ascii=False)
    all_keys_json = json.dumps(all_keys, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
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

.detail {{ padding: 1.25rem 2rem 2rem; }}
.detail-placeholder {{ display: flex; align-items: center; justify-content: center;
                        height: 140px; color: #ccc; font-size: 14px; }}

.detail-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 1.25rem;
                  padding-bottom: 10px; border-bottom: 1px solid #e8e8e8; }}
.detail-header h2 {{ font-size: 1.4rem; font-weight: 700; }}

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
.pills {{ display: flex; flex-wrap: wrap; gap: 5px; min-height: 30px; padding: 4px; }}
.pill {{ font-size: 12px; padding: 3px 11px; border-radius: 99px;
         background: #f5f5f3; border: 1px solid #e4e4e0; }}
</style>
</head>
<body>

<header>
  <div class="header-left">
    <h1>{output_title}</h1>
    <p>Gris = Sin errores en Topología MSE | Rojo = +10 errores | Naranja = 6-10 errores | Verde = 1-5 errores</p>
  </div>
</header>

<div class="stats">
  <div class="stat"><div class="val">{len(all_keys)}</div><div class="lbl">Enclavamientos totales</div></div>
  <div class="stat"><div class="val">{total_a}</div><div class="lbl">{display_label_a}</div></div>
  <div class="stat"><div class="val">{total_b}</div><div class="lbl">{display_label_b}</div></div>
</div>

<div class="keys-section">
  <h2>Claves — selecciona una</h2>
  <div class="keys-grid" id="keysGrid"></div>
</div>

<div class="detail" id="detail">
  <div class="detail-placeholder">Selecciona una clave para ver su detalle</div>
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

function parse(s) {{
  const p = s.split('.');
  return {{ type: p[2] || '?', id: p.slice(3).join('.') || s, full: s }};
}}
function byType(items) {{
  const m = {{}};
  items.forEach(i => {{ const p = parse(i); (m[p.type] = m[p.type] || []).push(p); }});
  return m;
}}
function getAllTypes(items) {{
  return [...new Set(items.map(i => parse(i).type))].sort();
}}

let activeKey = null;

// ── Render keys ──────────────────────────────────────────────────────────────
function renderKeys() {{
  document.getElementById('keysGrid').innerHTML = ALL_KEYS.map(k => {{
    const [bg, border, text] = color(k);
    return '<div class="key-card ' + (activeKey === k ? 'active' : '') + '"' +
                 ' style="background:' + bg + ';border-color:' + border + ';color:' + text + '"' +
                 ' data-key="' + escAttr(k) + '"' +
                 ' onclick="selectKey(this.dataset.key)">' + escAttr(k) + '</div>';
  }}).join('');
}}

// ── Render detail ─────────────────────────────────────────────────────────────
function renderDetail() {{
  const detail = document.getElementById('detail');
  if (!activeKey) {{
    detail.innerHTML = '<div class="detail-placeholder">Selecciona una clave para ver su detalle</div>';
    return;
  }}
  const k = activeKey;

  function buildPillHTML(e) {{
    return '<span class="pill" title="' + escAttr(e.full) + '">' + escAttr(e.id) + '</span>';
  }}

  function buildPanel(items, cls, label) {{
    if (!items || !items.length)
      return '<div class="dict-panel ' + cls + ' empty-panel">Sin datos en este diccionario</div>';

    const allTypes = getAllTypes(items);
    const grouped   = byType(items);

    const sections = allTypes.map(t => {{
      const els = grouped[t] || [];
      return `
        <div class="type-block">
          <div class="type-lbl">${{t}} (${{els.length}})</div>
          <div class="pills">${{els.map(e => buildPillHTML(e)).join('')}}</div>
        </div>`;
    }}).join('');

    return `
      <div class="dict-panel ${{cls}}">
        <h3>${{label}} — ${{items.length}} elementos</h3>
        ${{sections}}
      </div>`;
  }}

  detail.innerHTML = `
    <div class="detail-header"><h2>${{escAttr(k)}}</h2></div>
    <div class="dict-panels">
      ${{buildPanel(DICT_A[k], 'a', LABEL_A)}}
      ${{buildPanel(DICT_B[k], 'b', LABEL_B)}}
    </div>`;
}}

function selectKey(k) {{
  activeKey = activeKey === k ? null : k;
  renderKeys(); renderDetail();
}}

try {{
  renderKeys();
}} catch (e) {{
  console.error('Error al renderizar claves:', e);
  document.getElementById('keysGrid').innerHTML =
    '<div style="color:#dc2626;font-size:13px;">Error al renderizar: ' + escAttr(e.message) + ' (ver consola del navegador, F12)</div>';
}}
</script>
</body>
</html>"""
    return html


def run(ctc, dias, stream_id, output_dir, limit, sleep_interlock, carpeta_sharepoint_base=CARPETA_SHAREPOINT_BASE):
    session = requests.Session()
    session.auth = HTTPBasicAuth(GRAYLOG_USER, GRAYLOG_PASSWORD)

    ctc = ctc.upper()

    hoy = datetime.now(timezone.utc)
    desde = hoy - timedelta(days=dias)

    query = f"contentType:(Block OR Signal OR TrackCircuit OR LevelCrossing) AND ctc:{ctc}"
    messages = search_logs(
        session,
        query=query,
        from_date=desde.strftime("%Y-%m-%dT00:00:00.000Z"),
        to_date=hoy.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        stream_id=stream_id,
        limit=limit,
    )

    df_raw = messages_to_dataframe(messages)
    elementos = build_elementos_publicados(df_raw)

    ctc_detectado = elementos["header.ctc"].unique()[0]

    interlocking = elementos["element.interlock"].unique()
    df_topos = fetch_topologia(session, ctc_detectado, interlocking, sleep_seconds=sleep_interlock)

    elementos = merge_dependencias(elementos, df_topos)
    topo_sin_recibir, recibir_sin_topo = compute_diffs(df_topos, elementos)

    df_st = dicts_to_dataframe(topo_sin_recibir, recibir_sin_topo)

    html = build_html(
        df=df_st,
        label_a="Topo_sin_recibir",
        label_b="recibir_sin_topo",
        output_title=f"REVISION TOPOLOGÍA MSE VIEW: {ctc_detectado}",
        display_label_a="ELEMENTOS EN CATÁLOGO CTC Y EN TOPOLOGÍA MSE VIEW DE LOS QUE NO SE RECIBE INFORMACIÓN (INCIDENCIA DE TECNÓLOGO CTC)",
        display_label_b="ELEMENTOS PUBLICADOS POR CTC QUE NO ESTÁN CORRECTOS EN TOPOLOGÍA MSE VIEW (INCIDENCIA TOPOLOGÍA MSE)",
    )

    today_date = date.today().strftime("%Y-%m-%d")

    nombre_archivo = f"{today_date}new{ctc_detectado}.html"
    # fname = output_dir / ctc_detectado / nombre_archivo
    # fname.parent.mkdir(parents=True, exist_ok=True)
    # with open(fname, "w", encoding="utf-8") as f:
    #     f.write(html)

    if SHAREPOINT_DISPONIBLE:
        carpeta_sharepoint = f"{carpeta_sharepoint_base}/{ctc_detectado}"
        resultado = uploadSharepoint(
            nombre_archivo=nombre_archivo,
            contenido_archivo=html.encode("utf-8"),
            carpeta=carpeta_sharepoint,
        )
        if resultado.get("success"):
            print(f"✅ Informe subido correctamente a SharePoint: {resultado['file']}")
        else:
            print(f"❌ Error al subir el informe a SharePoint: {resultado}")
    else:
        print("⚠️  uploadSharepoint no disponible: el informe no se ha subido a SharePoint.")

    


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ctc", required=True)
    parser.add_argument("--dias", type=int, default=1)
    parser.add_argument("--stream-id", default=DEFAULT_STREAM_ID)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_BASE)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sleep-interlock", type=float, default=2.0)
    parser.add_argument(
        "--carpeta-sharepoint",
        default=CARPETA_SHAREPOINT_BASE,
        help="Carpeta base destino en SharePoint (relativa a '.../_Análisis Calidad Datos MSE y MIE/'). "
             "El CTC detectado se añade como subcarpeta automáticamente.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    fname = run(
        ctc=args.ctc,
        dias=args.dias,
        stream_id=args.stream_id,
        output_dir=args.output_dir,
        limit=args.limit,
        sleep_interlock=args.sleep_interlock,
        carpeta_sharepoint_base=args.carpeta_sharepoint,
    )


if __name__ == "__main__":
    sys.exit(main())