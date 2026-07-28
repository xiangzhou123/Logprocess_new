#!/usr/bin/env python3
"""
Catálogo Virtual CTC
=====================
Compara los elementos publicados por un CTC (vía Graylog) contra la
topología MSE View (vía API de topología), detecta discrepancias en
ambos sentidos y genera un dashboard HTML interactivo.

Uso básico:
    python catalogo_virtual.py --ctc MAC
    python catalogo_virtual.py --ctc BCN --dias 3
    python catalogo_virtual.py --ctc ZAR --output-dir "D:/Catalogo_Virtual" --sin-dashboard

Credenciales:
    Por defecto se usan las credenciales embebidas en el notebook original.
    Se recomienda sobreescribirlas con variables de entorno:
        GRAYLOG_USER, GRAYLOG_PASSWORD
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import pickle
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import orjson
import pandas as pd
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
logger = logging.getLogger("catalogo_virtual")


# ─────────────────────────────────────────────────────────────────────────────
# Configuración
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()
GRAYLOG_URL = os.getenv("GRAYLOG_URL")
GRAYLOG_USER = os.getenv("GRAYLOG_USER")
GRAYLOG_PASSWORD = os.getenv("GRAYLOG_PASSWORD")
TOPO_URL = "http://topo.rail.api.elcano.operaciones.adif/msetopo/download/filesInterlock"
DEFAULT_STREAM_ID = "68fb73bc6456d79315e70710"

# Enclavamientos que se excluyen por CTC (conocidos / no aplicables)
INTERLOCK_EXCLUSIONS = {
    "BCN": {"XC"},
    "MAC": {"DU", "GO", "AI"},
    "ZAR": {"SAC"},
    "COR": {"LV", "ML"},
}

DEFAULT_OUTPUT_BASE = Path(r"C:\Users\xiangzhou.zhang\ADIF\MSE - 00-CALIDAD DATO\Catálogo_Virtual")


# ─────────────────────────────────────────────────────────────────────────────
# Graylog: descarga de mensajes
# ─────────────────────────────────────────────────────────────────────────────
def get_streams(session: requests.Session) -> list[dict]:
    """Lista los streams disponibles en Graylog (utilidad de diagnóstico)."""
    url = f"{GRAYLOG_URL}/api/streams"
    response = session.get(url, headers={"Accept": "application/json"})
    response.raise_for_status()
    streams = response.json()["streams"]
    for s in streams:
        print(f"ID: {s['id']} | Nombre: {s['title']}")
    return streams


def graylog_get(session: requests.Session, url: str, params: dict, headers: dict,
                 max_retries: int = 5) -> dict:
    for attempt in range(max_retries):
        try:
            r = session.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 500:
                raise requests.exceptions.HTTPError("500", response=r)
            r.raise_for_status()
            return r.json()

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 500:
                raise  # Propagar 500 sin reintentar — lo gestiona fetch_window
            raise

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            wait = 2 ** attempt
            logger.warning("Error red — reintento %d/%d en %ds...", attempt + 1, max_retries, wait)
            time.sleep(wait)

    raise RuntimeError(f"Fallaron {max_retries} reintentos")


def fetch_window(session: requests.Session, query: str, from_str: str, to_str: str,
                  stream_id: str | None, headers: dict,
                  batch_size: int = 5000, _depth: int = 0) -> list[dict]:
    """
    Descarga una ventana de tiempo. Si encuentra error 500 por offset alto,
    divide la ventana en 2 mitades y las descarga recursivamente.
    Máximo 8 niveles de recursión (ventana mínima ~1s).
    """
    if _depth > 8:
        logger.warning("Ventana demasiado densa incluso dividida: %s → %s", from_str, to_str)
        return []

    messages: list[dict] = []
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
                # Demasiados resultados con offset alto → dividir ventana en 2
                t_from = datetime.fromisoformat(from_str.replace("Z", "+00:00"))
                t_to = datetime.fromisoformat(to_str.replace("Z", "+00:00"))
                mid = t_from + (t_to - t_from) / 2
                mid_str = mid.strftime("%Y-%m-%dT%H:%M:%S.000Z")

                print(f"\n✂️  Dividiendo [{from_str} → {to_str}] (offset={offset}, depth={_depth})")

                left = fetch_window(session, query, from_str, mid_str, stream_id, headers,
                                     batch_size, _depth + 1)
                right = fetch_window(session, query, mid_str, to_str, stream_id, headers,
                                      batch_size, _depth + 1)
                return messages[:offset - len(batch)] + left + right
            raise

    return messages


def get_total_results(session: requests.Session, query: str, from_str: str, to_str: str,
                       stream_id: str | None, headers: dict) -> int:
    params = {"query": query, "from": from_str, "to": to_str,
              "limit": 1, "offset": 0, "fields": "timestamp"}
    if stream_id:
        params["filter"] = f"streams:{stream_id}"
    data = graylog_get(session, f"{GRAYLOG_URL}/api/search/universal/absolute", params, headers)
    return data.get("total_results", 0)


def search_logs(session: requests.Session, query: str, from_date: str, to_date: str,
                 stream_id: str | None = None, limit: int | None = None,
                 max_per_window: int = 5000, checkpoint_file: str = "checkpoint.pkl") -> list[dict]:
    headers = {"Accept": "application/json"}
    all_messages: list[dict] = []
    range_start = datetime.fromisoformat(from_date.replace("Z", "+00:00"))
    range_end = datetime.fromisoformat(to_date.replace("Z", "+00:00"))
    total_seconds = (range_end - range_start).total_seconds()

    # Reanudar desde checkpoint
    resume_from = range_start
    if os.path.exists(checkpoint_file):
        logger.info("♻️  Reanudando desde checkpoint...")
        with open(checkpoint_file, "rb") as f:
            ckpt = pickle.load(f)
        all_messages = ckpt["messages"]
        resume_from = ckpt["last_window_end"]
        logger.info("   %s msgs ya descargados, continuando desde %s", f"{len(all_messages):,}", resume_from)

    logger.info("🔍 Calculando total de mensajes...")
    total_global = get_total_results(
        session, query,
        range_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        range_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        stream_id, headers,
    )
    logger.info("📊 Total: %s", f"{total_global:,}")

    if total_global == 0:
        return []

    density = total_global / total_seconds
    window_seconds = max(int((max_per_window / density) * 0.85), 5)
    remaining = (range_end - resume_from).total_seconds()
    logger.info("⚙️  Ventana: %ds | Estimadas: %d", window_seconds, int(remaining / window_seconds) + 1)

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

        except RuntimeError:
            with open(checkpoint_file, "wb") as f:
                pickle.dump({"messages": all_messages, "last_window_end": current}, f)
            logger.error("💾 Guardado emergencia: %s msgs", f"{len(all_messages):,}")
            raise

        if window_count % 50 == 0:
            with open(checkpoint_file, "wb") as f:
                pickle.dump({"messages": all_messages, "last_window_end": window_end}, f)
            logger.info("💾 Checkpoint: %s msgs", f"{len(all_messages):,}")

        current = window_end

        if limit and len(all_messages) >= limit:
            all_messages = all_messages[:limit]
            logger.info("🛑 Límite alcanzado: %s msgs", f"{limit:,}")
            break

    if os.path.exists(checkpoint_file):
        os.remove(checkpoint_file)

    logger.info("✅ Descarga completa: %s mensajes", f"{len(all_messages):,}")
    return all_messages


# ─────────────────────────────────────────────────────────────────────────────
# Transformación de mensajes Graylog → DataFrame de elementos publicados
# ─────────────────────────────────────────────────────────────────────────────
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


def messages_to_dataframe(messages: list[dict], chunk_size: int = 10_000) -> pd.DataFrame:
    """Convierte la lista cruda de mensajes de Graylog en un DataFrame normalizado."""
    lista_messages = [item["message"]["message"] for item in messages]
    dict_list = [orjson.loads(x) for x in lista_messages]

    chunks = [dict_list[i:i + chunk_size] for i in range(0, len(dict_list), chunk_size)]
    dfs = []
    for i, chunk in enumerate(chunks):
        dfs.append(pd.json_normalize(chunk))
        logger.info("Chunk %d/%d procesado", i + 1, len(chunks))

    df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    if "header.timestampMSG" in df.columns:
        df["header.timestampMSG"] = pd.to_datetime(df["header.timestampMSG"], unit="ms")
    return df


def to_camel_case(s) -> str:
    if pd.isna(s):
        return ""
    parts = str(s).split()
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])


def build_elementos_publicados(df: pd.DataFrame) -> pd.DataFrame:
    """Extrae y unifica las columnas de elemento (name/type/interlock) desde el
    DataFrame crudo de mensajes, y construye el ElementoID."""
    df_filter = df[["header.ctc", "header.ContentType"]].copy()

    for target_col, source_cols in COLS_MAP.items():
        for col in source_cols:
            if col not in df.columns:
                df[col] = None
        result = df[source_cols[0]]
        for col in source_cols[1:]:
            result = result.combine_first(df[col])
        df_filter[target_col] = result

    df_filter = df_filter.drop_duplicates(keep="first").reset_index(drop=True)

    df_filter["ElementoID"] = (
        df_filter["header.ctc"] + "." +
        df_filter["element.interlock"] + "." +
        df_filter["header.ContentType"].apply(to_camel_case) + "." +
        df_filter["element.name"]
    )
    return df_filter


# ─────────────────────────────────────────────────────────────────────────────
# API de topología MSE
# ─────────────────────────────────────────────────────────────────────────────
def get_elements(session: requests.Session, interlock: str, ctc: str) -> pd.DataFrame | None:
    payload = {"interlock": interlock, "ctc": ctc}
    response = session.post(TOPO_URL, json=payload)

    if response.status_code != 200:
        logger.warning("Error %s en %s: %s", response.status_code, interlock, response.text)
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


def fetch_topologia(session: requests.Session, ctc: str, interlocks: list[str],
                     sleep_seconds: float = 2.0) -> pd.DataFrame:
    exclusiones = INTERLOCK_EXCLUSIONS.get(ctc, set())
    interlocks = [i for i in interlocks if i not in exclusiones]

    dfs = []
    for interlock in interlocks:
        try:
            df_topo = get_elements(session, interlock, ctc)
            if df_topo is None or df_topo.empty:
                logger.info("%s no devolvió datos", interlock)
            else:
                dfs.append(df_topo)
            time.sleep(sleep_seconds)
        except Exception as e:
            logger.warning("%s falló: %s", interlock, e)

    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# Cruce de datos: publicado (CTC) vs topología (MSE View)
# ─────────────────────────────────────────────────────────────────────────────
def merge_dependencias(elementos: pd.DataFrame, df_topos: pd.DataFrame) -> pd.DataFrame:
    dependencias = df_topos[["Mnemónico", "NombreEnclavamiento"]].drop_duplicates(subset="Mnemónico")
    return elementos.merge(
        dependencias, left_on="element.interlock", right_on="Mnemónico", how="left"
    )


def compute_diffs(df_topos: pd.DataFrame, elementos: pd.DataFrame) -> tuple[dict, dict]:
    """
    Compara, por enclavamiento, los elementos de topología vs los recibidos por CTC.

    Devuelve:
        Topo_sin_recibir: elementos en topología/catálogo que NO llegan por CTC
                          (incidencia de tecnólogo CTC)
        recibir_sin_topo: elementos publicados por CTC que NO están en topología
                          (incidencia de topología MSE)
    """
    topos_enclavamientos = {i: g for i, g in df_topos.groupby("NombreEnclavamiento")}
    enclavamientos_recibidos = {i: g for i, g in elementos.groupby("NombreEnclavamiento")}

    topo_sin_recibir: dict[str, list[str]] = {}
    recibir_sin_topo: dict[str, list[str]] = {}

    for i in topos_enclavamientos.keys() & enclavamientos_recibidos.keys():
        df1_filtrado = topos_enclavamientos[i][
            (~topos_enclavamientos[i]["ElementoID"].str.lower().str.contains(
                "alarm|undefined|operationcontrol", na=False)) &
            (topos_enclavamientos[i]["SubtipoElemento"] != "trackCircuitNotSignalized")
        ]

        nombres1_dict = {x.lower(): x for x in df1_filtrado["ElementoID"]}
        nombres1_pre_set = set(x.lower() for x in df1_filtrado["NombreCircuito"])
        nombres2_dict = {x.lower(): x for x in enclavamientos_recibidos[i]["ElementoID"]}

        diff1 = set(nombres1_dict.keys()) - set(nombres2_dict.keys())
        diff2 = set(nombres2_dict.keys()) - set(nombres1_dict.keys())

        if diff1:
            topo_sin_recibir[i] = [nombres1_dict[n] for n in diff1]

        diff2_filtrado = [
            nombres2_dict[n] for n in diff2
            if n not in diff1 and n not in nombres1_pre_set
        ]
        if diff2_filtrado:
            recibir_sin_topo[i] = diff2_filtrado

    return topo_sin_recibir, recibir_sin_topo


# ─────────────────────────────────────────────────────────────────────────────
# Persistencia del estado del dashboard (comentarios / fixed / error) en HTML previo
# ─────────────────────────────────────────────────────────────────────────────
def _extract_js_object(script: str, var_name: str):
    """
    Extrae el valor JSON de una declaración JS del tipo:
        const VAR_NAME = { ... };   o   const VAR_NAME = [ ... ];
    usando un contador de brackets para manejar estructuras anidadas correctamente.
    """
    pattern = rf"const\s+{re.escape(var_name)}\s*=\s*"
    match = re.search(pattern, script)
    if not match:
        raise ValueError(f"No se encontró la variable '{var_name}' en el HTML.")

    start = match.end()
    while start < len(script) and script[start] not in ("{", "["):
        start += 1

    if start >= len(script):
        raise ValueError(f"No se encontró el valor de '{var_name}'.")

    open_char = script[start]
    close_char = "}" if open_char == "{" else "]"
    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(script[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return json.loads(script[start:i + 1])

    raise ValueError(f"No se pudo extraer el valor de '{var_name}': brackets no balanceados.")


def _extract_script(html: str) -> str:
    """
    Devuelve el contenido del bloque <script> que contiene DICT_A.
    El HTML generado por build_html puede tener múltiples bloques <script>
    (librerías externas, etc.), por lo que buscamos específicamente
    el que contiene las variables de datos.
    """
    matches = re.findall(r"<script[^>]*>([\s\S]*?)</script>", html, re.IGNORECASE)
    if not matches:
        raise ValueError("No se encontró ningún bloque <script> en el HTML.")

    for block in reversed(matches):
        if "DICT_A" in block:
            return block

    raise ValueError(
        f"No se encontró el bloque <script> con DICT_A. "
        f"Bloques encontrados: {len(matches)}, tamaños: {[len(b) for b in matches]}"
    )


def read_html_state(html_path) -> dict | None:
    if html_path is None:
        return None
    html = Path(html_path).read_text(encoding="utf-8")
    script = _extract_script(html)

    dict_a = _extract_js_object(script, "DICT_A")
    dict_b = _extract_js_object(script, "DICT_B")
    comments = _extract_js_object(script, "EMBEDDED_COMMENTS")
    fixed = _extract_js_object(script, "EMBEDDED_FIXED")
    errors = _extract_js_object(script, "EMBEDDED_ERRORS")
    all_keys = sorted(set(dict_a) | set(dict_b))
    result = {}

    for key in all_keys:
        result[key] = {}
        for panel, cls, panel_dict in (
            ("Topo_sin_recibir", "a", dict_a),
            ("recibir_sin_topo", "b", dict_b),
        ):
            items = panel_dict.get(key, [])
            if not items:
                result[key][panel] = []
                continue

            fixed_key = f"{key}|{cls}"
            error_key = f"{key}|{cls}|error"

            fixed_elems = set(fixed.get(fixed_key, []))
            error_elems = set(errors.get(error_key, []))

            rows = []
            for elem in items:
                comment_key = f"{key}|{elem}"
                comentario = comments.get(comment_key)
                if elem in fixed_elems:
                    subseccion = "fixed"
                elif elem in error_elems:
                    subseccion = "error"
                else:
                    subseccion = "normal"
                rows.append({"elem": elem, "subseccion": subseccion, "comentario": comentario})

            result[key][panel] = rows

    return result


def to_dataframe(result: dict | None) -> pd.DataFrame | None:
    if result is None:
        return None

    rows = []
    for key, panels in result.items():
        for panel, items in panels.items():
            for r in items:
                rows.append({
                    "enclavamiento": key,
                    "panel": panel,
                    "elem": r["elem"],
                    "subseccion": r["subseccion"],
                    "comentario": r["comentario"],
                })
    return pd.DataFrame(rows)


def find_latest_html(output_dir: Path) -> Path | None:
    if not output_dir.exists():
        return None
    return next(iter(sorted(output_dir.glob("*.html"), key=lambda f: f.stat().st_mtime, reverse=True)), None)


def filter_by_dicts(
    df: pd.DataFrame | None,
    topo_sin_recibir: dict,
    recibir_sin_topo: dict,
    panel_a: str = "Topo_sin_recibir",
    panel_b: str = "recibir_sin_topo",
) -> pd.DataFrame:
    """Combina el estado previo (comentarios/fixed/error, si existía un HTML previo)
    con los diffs recién calculados, conservando el estado de los elementos que
    siguen presentes y añadiendo los nuevos."""

    if df is None or df.empty:
        rows = []
        for panel, source_dict in ((panel_a, topo_sin_recibir), (panel_b, recibir_sin_topo)):
            for enclavamiento, elems in source_dict.items():
                for elem in elems:
                    rows.append({
                        "enclavamiento": enclavamiento,
                        "panel": panel,
                        "elem": elem,
                        "subseccion": "normal",
                        "comentario": None,
                    })
        return (
            pd.DataFrame(rows)
            .sort_values(["enclavamiento", "panel", "elem"])
            .reset_index(drop=True)
        )

    def _in_dict(row):
        key = row["enclavamiento"]
        elem = row["elem"]
        panel = row["panel"]
        if panel == panel_a:
            return elem in topo_sin_recibir.get(key, [])
        elif panel == panel_b:
            return elem in recibir_sin_topo.get(key, [])
        return False

    mask = df.apply(_in_dict, axis=1)
    df_in = df[mask].copy()

    missing_rows = []
    for panel, source_dict in ((panel_a, topo_sin_recibir), (panel_b, recibir_sin_topo)):
        for enclavamiento, elems in source_dict.items():
            existing = set(
                df.loc[(df["enclavamiento"] == enclavamiento) & (df["panel"] == panel), "elem"]
            )
            for elem in elems:
                if elem not in existing:
                    missing_rows.append({
                        "enclavamiento": enclavamiento,
                        "panel": panel,
                        "elem": elem,
                        "subseccion": "normal",
                        "comentario": None,
                    })

    if missing_rows:
        df_missing = pd.DataFrame(missing_rows)
        result = pd.concat([df_in, df_missing], ignore_index=True)
    else:
        result = df_in

    return result.sort_values(["enclavamiento", "panel", "elem"]).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard HTML
# ─────────────────────────────────────────────────────────────────────────────
import json
import hashlib
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
        enclavamiento | panel | elem | subseccion | comentario

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
    df["subseccion"]    = df["subseccion"].fillna("normal").astype(str).str.strip()
    df["comentario"]    = df["comentario"].fillna("").astype(str).str.strip()
    df.loc[df["comentario"] == "None", "comentario"] = ""
    df.loc[df["subseccion"] == "None", "subseccion"] = "normal"

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

    panel_to_cls = {label_a: "a", label_b: "b"}

    # ── Hash único por fichero ───────────────────────────────────────────────
    data_hash = hashlib.md5(
        json.dumps(
            {"a": dict_a, "b": dict_b, "title": output_title, "date": date.today().isoformat()},
            sort_keys=True
        ).encode()
    ).hexdigest()[:12]

    # ── Comentarios: {"enclavamiento|elem": "texto"} ─────────────────────────
    initial_comments: dict[str, str] = {}
    for _, row in df[df["comentario"] != ""].iterrows():
        initial_comments[f"{row['enclavamiento']}|{row['elem']}"] = row["comentario"]

    # ── Fixed: {"enclavamiento|cls": [elems]} ────────────────────────────────
    initial_fixed: dict[str, list[str]] = {}
    for _, row in df[df["subseccion"] == "fixed"].iterrows():
        cls = panel_to_cls.get(row["panel"])
        if cls:
            initial_fixed.setdefault(f"{row['enclavamiento']}|{cls}", []).append(row["elem"])

    # ── Errors: {"enclavamiento|cls|error": [elems]} ─────────────────────────
    initial_errors: dict[str, list[str]] = {}
    for _, row in df[df["subseccion"] == "error"].iterrows():
        cls = panel_to_cls.get(row["panel"])
        if cls:
            initial_errors.setdefault(f"{row['enclavamiento']}|{cls}|error", []).append(row["elem"])

    # ── Colores y totales ────────────────────────────────────────────────────
    colors   = build_color_map(dict_a, dict_b)
    all_keys = sorted(set(dict_a) | set(dict_b))
    total_a  = sum(len(v) for v in dict_a.values())
    total_b  = sum(len(v) for v in dict_b.values())

    colors_json           = json.dumps({k: list(v) for k, v in colors.items()}, ensure_ascii=False)
    dict_a_json           = json.dumps(dict_a,          ensure_ascii=False)
    dict_b_json           = json.dumps(dict_b,          ensure_ascii=False)
    all_keys_json         = json.dumps(all_keys,         ensure_ascii=False)
    initial_comments_json = json.dumps(initial_comments, ensure_ascii=False)
    initial_fixed_json    = json.dumps(initial_fixed,    ensure_ascii=False)
    initial_errors_json   = json.dumps(initial_errors,   ensure_ascii=False)

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
.key-card .kc-counts {{ font-size: 11px; font-weight: 400; margin-top: 3px; opacity: .7; }}

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
.pills {{ display: flex; flex-wrap: wrap; gap: 5px; min-height: 30px;
          padding: 4px; border-radius: 6px; transition: background 0.2s; }}
.pills.drag-over {{ background: #e0e7ff; }}
.pills.empty {{ border: 1px dashed #d0d0d0; min-height: 50px;
                display: flex; align-items: center; justify-content: center;
                color: #999; font-size: 11px; font-style: italic; }}
.pill {{ font-size: 12px; padding: 3px 11px; border-radius: 99px;
         background: #f5f5f3; border: 1px solid #e4e4e0; cursor: grab;
         transition: all .15s; position: relative; }}
.pill:hover {{ background: #e8e8e6; border-color: #d0d0cc; transform: translateY(-1px); }}
.pill.has-comment {{ background: #fff4e6; border-color: #ffa500; }}
.pill.active {{ background: #e0e7ff; border-color: #4f46e5; }}
.pill.fixed {{ background: #d1fae5; border-color: #10b981; border-width: 2px; }}
.pill.error {{ background: #fee2e2; border-color: #ef4444; border-width: 2px; }}
.pill.dragging {{ opacity: 0.5; cursor: grabbing; }}

.special-sections {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 16px; }}
@media (max-width: 680px) {{ .special-sections {{ grid-template-columns: 1fr; }} }}

.fixed-section {{ padding: 12px; background: #f0fdf4;
                  border: 2px dashed #10b981; border-radius: 8px; min-height: 80px; }}
.fixed-section.drag-over {{ background: #dcfce7; border-style: solid; }}
.fixed-section h4 {{ font-size: 11px; font-weight: 600; text-transform: uppercase;
                     letter-spacing: .06em; color: #059669; margin-bottom: 8px; }}
.fixed-section .pills-fixed {{ min-height: 40px; }}

.error-section {{ padding: 12px; background: #fef2f2;
                  border: 2px dashed #ef4444; border-radius: 8px; min-height: 80px; }}
.error-section.drag-over {{ background: #fee2e2; border-style: solid; }}
.error-section h4 {{ font-size: 11px; font-weight: 600; text-transform: uppercase;
                     letter-spacing: .06em; color: #dc2626; margin-bottom: 8px; }}
.error-section .pills-error {{ min-height: 40px; }}

.section-empty {{ text-align: center; font-size: 12px;
                  padding: 12px; font-style: italic; }}
.fixed-section .section-empty {{ color: #10b981; }}
.error-section .section-empty {{ color: #ef4444; }}

.normal-section {{ position: relative; }}
.normal-section::after {{ content: ''; position: absolute; inset: -4px;
                          border: 2px dashed transparent; border-radius: 8px;
                          pointer-events: none; transition: border-color 0.2s; }}
.normal-section.drag-over::after {{ border-color: #94a3b8; }}

.comment-section {{ margin-top: 8px; padding: 12px; background: #fafafa;
                     border-radius: 8px; border: 1px solid #e8e8e8;
                     display: none; }}
.comment-section.show {{ display: block; animation: slideDown 0.2s ease; }}
@keyframes slideDown {{ from {{ opacity: 0; transform: translateY(-10px); }}
                        to   {{ opacity: 1; transform: translateY(0); }} }}

.comment-header {{ display: flex; justify-content: space-between; align-items: center;
                   margin-bottom: 8px; }}
.comment-header .elem-name {{ font-size: 11px; font-weight: 600; color: #666; }}
.comment-header .close-btn {{ background: none; border: none; color: #999;
                              cursor: pointer; font-size: 18px; padding: 0;
                              width: 24px; height: 24px; line-height: 24px;
                              border-radius: 4px; transition: all .15s; }}
.comment-header .close-btn:hover {{ background: #e8e8e8; color: #333; }}

.comment-textarea {{ width: 100%; min-height: 80px; padding: 8px 10px;
                     border: 1px solid #d0d0d0; border-radius: 6px;
                     font-family: inherit; font-size: 13px; resize: vertical;
                     transition: border-color .15s; }}
.comment-textarea:focus {{ outline: none; border-color: #4f46e5; }}

.comment-actions {{ display: flex; gap: 8px; margin-top: 8px; }}
.comment-btn {{ padding: 6px 14px; border-radius: 6px; font-size: 12px;
                font-weight: 500; cursor: pointer; transition: all .15s;
                border: 1px solid; }}
.comment-btn.save   {{ background: #4f46e5; color: white; border-color: #4f46e5; }}
.comment-btn.save:hover {{ background: #4338ca; }}
.comment-btn.cancel {{ background: white; color: #666; border-color: #d0d0d0; }}
.comment-btn.cancel:hover {{ background: #f5f5f5; }}
.comment-btn.delete {{ background: #dc2626; color: white; border-color: #dc2626; }}
.comment-btn.delete:hover {{ background: #b91c1c; }}

.comment-display {{ font-size: 13px; color: #333; padding: 8px 10px;
                    background: white; border-radius: 6px; border: 1px solid #e0e0e0;
                    white-space: pre-wrap; word-wrap: break-word; }}

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
    <p>Arrastra elementos entre secciones | Clic para comentarios | Gris = Sin errores en Topología MSE | Rojo = +10 errores | Naranja = 6-10 errores | Verde = 1-5 errores</p>
  </div>
  <button class="export-btn" onclick="exportHTML()">⬇ Exportar HTML</button>
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

// ── Estado embebido (se sobreescribe al exportar) ────────────────────────────
const EMBEDDED_COMMENTS = {initial_comments_json};
const EMBEDDED_FIXED    = {initial_fixed_json};
const EMBEDDED_ERRORS   = {initial_errors_json};

// ── Claves localStorage (solo para guardar cambios durante la sesión) ────────
const DATA_VERSION = '{data_hash}';
const COMMENTS_KEY = 'bcn_comments_{data_hash}';
const FIXED_KEY    = 'bcn_fixed_{data_hash}';
const ERROR_KEY    = 'bcn_error_{data_hash}';

let comments   = {{}};
let fixedItems = {{}};
let errorItems = {{}};

// ── Al abrir el fichero SIEMPRE se carga el estado embebido en el HTML.
//    Esto garantiza que al compartir/descargar de SharePoint cualquier
//    navegador (Edge, Chrome, Firefox) ve el mismo estado, sin importar
//    lo que haya en su localStorage local.
//    El localStorage solo se usa para conservar cambios dentro de la sesión.
function loadComments() {{
  comments = JSON.parse(JSON.stringify(EMBEDDED_COMMENTS));
}}

function loadFixed() {{
  fixedItems = JSON.parse(JSON.stringify(EMBEDDED_FIXED));
}}

function loadError() {{
  errorItems = JSON.parse(JSON.stringify(EMBEDDED_ERRORS));
}}

function saveComments() {{
  try {{ localStorage.setItem(COMMENTS_KEY, JSON.stringify(comments)); }} catch(e) {{}}
}}

function saveFixed() {{
  try {{ localStorage.setItem(FIXED_KEY, JSON.stringify(fixedItems)); }} catch(e) {{}}
}}

function saveError() {{
  try {{ localStorage.setItem(ERROR_KEY, JSON.stringify(errorItems)); }} catch(e) {{}}
}}

// ── Export con estado embebido ───────────────────────────────────────────────
function exportHTML() {{
  let src = document.documentElement.outerHTML;
  src = src.replace(
    /const EMBEDDED_COMMENTS\s*=\s*\{{[\s\S]*?\}};/,
    `const EMBEDDED_COMMENTS = ${{JSON.stringify(comments)}};`
  );
  src = src.replace(
    /const EMBEDDED_FIXED\s*=\s*\{{[\s\S]*?\}};/,
    `const EMBEDDED_FIXED    = ${{JSON.stringify(fixedItems)}};`
  );
  src = src.replace(
    /const EMBEDDED_ERRORS\s*=\s*\{{[\s\S]*?\}};/,
    `const EMBEDDED_ERRORS   = ${{JSON.stringify(errorItems)}};`
  );

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

// ── Helpers ──────────────────────────────────────────────────────────────────
function escAttr(s) {{
  return String(s)
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}}

function color(k)  {{ return COLORS[k] || DEF_COLOR; }}
function getCommentKey(key, elem) {{ return `${{key}}|${{elem}}`; }}
function getFixedKey(key, cls)    {{ return `${{key}}|${{cls}}`; }}
function getErrorKey(key, cls)    {{ return `${{key}}|${{cls}}|error`; }}

function isFixed(key, cls, elem) {{
  const f = fixedItems[getFixedKey(key, cls)];
  return f && f.includes(elem);
}}
function isError(key, cls, elem) {{
  const e = errorItems[getErrorKey(key, cls)];
  return e && e.includes(elem);
}}

function addFixed(key, cls, elem) {{
  const fkey = getFixedKey(key, cls);
  const ekey = getErrorKey(key, cls);
  if (errorItems[ekey]) {{
    errorItems[ekey] = errorItems[ekey].filter(e => e !== elem);
    if (!errorItems[ekey].length) delete errorItems[ekey];
  }}
  if (!fixedItems[fkey]) fixedItems[fkey] = [];
  if (!fixedItems[fkey].includes(elem)) {{ fixedItems[fkey].push(elem); saveFixed(); saveError(); }}
}}
function removeFixed(key, cls, elem) {{
  const fkey = getFixedKey(key, cls);
  if (fixedItems[fkey]) {{
    fixedItems[fkey] = fixedItems[fkey].filter(e => e !== elem);
    if (!fixedItems[fkey].length) delete fixedItems[fkey];
    saveFixed();
  }}
}}
function addError(key, cls, elem) {{
  const ekey = getErrorKey(key, cls);
  const fkey = getFixedKey(key, cls);
  if (fixedItems[fkey]) {{
    fixedItems[fkey] = fixedItems[fkey].filter(e => e !== elem);
    if (!fixedItems[fkey].length) delete fixedItems[fkey];
  }}
  if (!errorItems[ekey]) errorItems[ekey] = [];
  if (!errorItems[ekey].includes(elem)) {{ errorItems[ekey].push(elem); saveError(); saveFixed(); }}
}}
function removeError(key, cls, elem) {{
  const ekey = getErrorKey(key, cls);
  if (errorItems[ekey]) {{
    errorItems[ekey] = errorItems[ekey].filter(e => e !== elem);
    if (!errorItems[ekey].length) delete errorItems[ekey];
    saveError();
  }}
}}

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

loadComments(); loadFixed(); loadError();

let activeKey     = null;
let activeElement = null;

// ── Render keys ──────────────────────────────────────────────────────────────
function renderKeys() {{
  document.getElementById('keysGrid').innerHTML = ALL_KEYS.map(k => {{
    const [bg, border, text] = color(k);
    return `<div class="key-card ${{activeKey === k ? 'active' : ''}}"
                 style="background:${{bg}};border-color:${{border}};color:${{text}}"
                 data-key="${{escAttr(k)}}"
                 onclick="selectKey(this.dataset.key)">${{k}}</div>`;
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

  function buildPillHTML(e, cls) {{
    const commentKey = getCommentKey(k, e.full);
    const hasComment = !!comments[commentKey];
    const isAct      = activeElement === e.full;
    const extraCls   = isFixed(k, cls, e.full) ? 'fixed'
                     : isError(k, cls, e.full) ? 'error' : '';
    return `<span class="pill ${{hasComment ? 'has-comment' : ''}} ${{isAct ? 'active' : ''}} ${{extraCls}}"
                  draggable="true"
                  data-cls="${{escAttr(cls)}}"
                  data-elem="${{escAttr(e.full)}}"
                  ondragstart="handleDragStart(event,this.dataset.cls,this.dataset.elem)"
                  ondragend="handleDragEnd(event)"
                  onclick="toggleComment(this.dataset.elem)"
                  title="${{escAttr(e.full)}}">${{e.id}}</span>`;
  }}

  function buildPanel(items, cls, label) {{
    if (!items || !items.length)
      return `<div class="dict-panel ${{cls}} empty-panel">Sin datos en este diccionario</div>`;

    const fkey   = getFixedKey(k, cls);
    const ekey   = getErrorKey(k, cls);
    const fixed  = fixedItems[fkey]  || [];
    const errors = errorItems[ekey]  || [];

    const normal   = items.filter(i => !fixed.includes(i) && !errors.includes(i));
    const allTypes = getAllTypes(items);
    const gNormal  = byType(normal);
    const gFixed   = byType(fixed);
    const gError   = byType(errors);

    const normalSections = allTypes.map(t => {{
      const els = gNormal[t] || [];
      const pillsHTML = els.map(e => buildPillHTML(e, cls)).join('');
      const pillsCls  = els.length ? 'pills' : 'pills empty';
      const emptyTxt  = els.length ? '' : 'Arrastra aquí para devolver elementos';
      return `
        <div class="type-block normal-section"
             ondragover="handleNormalDragOver(event)"
             ondragleave="handleNormalDragLeave(event)"
             ondrop="handleNormalDrop(event,'${{cls}}')">
          <div class="type-lbl">${{t}} (${{els.length}})</div>
          <div class="${{pillsCls}}">${{pillsHTML || emptyTxt}}</div>
          <div class="comment-container"></div>
        </div>`;
    }}).join('');

    function specialPills(grouped) {{
      return Object.entries(grouped).map(([t, els]) => `
        <div class="type-block">
          <div class="type-lbl">${{t}} (${{els.length}})</div>
          <div class="pills">${{els.map(e => buildPillHTML(e, cls)).join('')}}</div>
          <div class="comment-container"></div>
        </div>`).join('');
    }}

    const fixedContent = Object.keys(gFixed).length
      ? specialPills(gFixed)
      : '<div class="section-empty">Arrastra elementos aquí para marcarlos como arreglados</div>';
    const errorContent = Object.keys(gError).length
      ? specialPills(gError)
      : '<div class="section-empty">Arrastra elementos aquí para marcarlos como errores controlados</div>';

    return `
      <div class="dict-panel ${{cls}}">
        <h3>${{label}} — ${{items.length}} elementos</h3>
        ${{normalSections}}
        <div class="special-sections">
          <div class="fixed-section"
               ondragover="handleFixedDragOver(event)"
               ondragleave="handleFixedDragLeave(event)"
               ondrop="handleFixedDrop(event,'${{cls}}')">
            <h4>✓ Arreglados (${{fixed.length}})</h4>
            <div class="pills-fixed">${{fixedContent}}</div>
          </div>
          <div class="error-section"
               ondragover="handleErrorDragOver(event)"
               ondragleave="handleErrorDragLeave(event)"
               ondrop="handleErrorDrop(event,'${{cls}}')">
            <h4>⚠ Errores Controlados (${{errors.length}})</h4>
            <div class="pills-error">${{errorContent}}</div>
          </div>
        </div>
      </div>`;
  }}

  detail.innerHTML = `
    <div class="detail-header"><h2>${{k}}</h2></div>
    <div class="dict-panels">
      ${{buildPanel(DICT_A[k], 'a', LABEL_A)}}
      ${{buildPanel(DICT_B[k], 'b', LABEL_B)}}
    </div>`;

  if (activeElement) setTimeout(() => showCommentSection(activeKey, activeElement), 10);
}}

// ── Drag & drop ──────────────────────────────────────────────────────────────
let draggedElem      = null;
let draggedCls       = null;
let draggedFromFixed = false;
let draggedFromError = false;

function handleDragStart(event, cls, elem) {{
  draggedElem      = elem;
  draggedCls       = cls;
  draggedFromFixed = isFixed(activeKey, cls, elem);
  draggedFromError = isError(activeKey, cls, elem);
  event.target.classList.add('dragging');
  event.dataTransfer.effectAllowed = 'move';
}}
function handleDragEnd(event) {{ event.target.classList.remove('dragging'); }}

function handleNormalDragOver(event) {{
  if (!draggedFromFixed && !draggedFromError) return;
  event.preventDefault(); event.dataTransfer.dropEffect = 'move';
  event.currentTarget.classList.add('drag-over');
}}
function handleNormalDragLeave(event) {{ event.currentTarget.classList.remove('drag-over'); }}
function handleNormalDrop(event, targetCls) {{
  event.preventDefault(); event.currentTarget.classList.remove('drag-over');
  if (draggedElem && draggedCls === targetCls) {{
    if (draggedFromFixed)      removeFixed(activeKey, targetCls, draggedElem);
    else if (draggedFromError) removeError(activeKey, targetCls, draggedElem);
    renderDetail();
  }}
  draggedElem = draggedCls = null; draggedFromFixed = draggedFromError = false;
}}

function handleFixedDragOver(event) {{
  if (draggedFromFixed) return;
  event.preventDefault(); event.dataTransfer.dropEffect = 'move';
  event.currentTarget.classList.add('drag-over');
}}
function handleFixedDragLeave(event) {{ event.currentTarget.classList.remove('drag-over'); }}
function handleFixedDrop(event, targetCls) {{
  event.preventDefault(); event.currentTarget.classList.remove('drag-over');
  if (draggedElem && draggedCls === targetCls && !draggedFromFixed) {{
    addFixed(activeKey, targetCls, draggedElem); renderDetail();
  }}
  draggedElem = draggedCls = null; draggedFromFixed = draggedFromError = false;
}}

function handleErrorDragOver(event) {{
  if (draggedFromError) return;
  event.preventDefault(); event.dataTransfer.dropEffect = 'move';
  event.currentTarget.classList.add('drag-over');
}}
function handleErrorDragLeave(event) {{ event.currentTarget.classList.remove('drag-over'); }}
function handleErrorDrop(event, targetCls) {{
  event.preventDefault(); event.currentTarget.classList.remove('drag-over');
  if (draggedElem && draggedCls === targetCls && !draggedFromError) {{
    addError(activeKey, targetCls, draggedElem); renderDetail();
  }}
  draggedElem = draggedCls = null; draggedFromFixed = draggedFromError = false;
}}

// ── Comentarios ───────────────────────────────────────────────────────────────
function toggleComment(elem) {{
  event.stopPropagation();
  activeElement = activeElement === elem ? null : elem;
  renderDetail();
}}

function showCommentSection(key, elem) {{
  const commentKey      = getCommentKey(key, elem);
  const existingComment = comments[commentKey] || '';
  let targetPill = null;
  document.querySelectorAll('.pill').forEach(p => {{ if (p.dataset.elem === elem) targetPill = p; }});
  if (!targetPill) return;
  const typeBlock = targetPill.closest('.type-block');
  if (!typeBlock) return;
  const container = typeBlock.querySelector('.comment-container');
  if (!container) return;

  const isEditing = !existingComment;
  const deleteBtn = existingComment
    ? `<button class="comment-btn delete"
               data-key="${{escAttr(key)}}" data-elem="${{escAttr(elem)}}"
               onclick="deleteComment(this.dataset.key,this.dataset.elem)">Eliminar</button>`
    : '';

  const editingHTML = `
    <textarea class="comment-textarea" id="commentText"
              placeholder="Escribe tu comentario aquí...">${{existingComment}}</textarea>
    <div class="comment-actions">
      <button class="comment-btn save"
              data-key="${{escAttr(key)}}" data-elem="${{escAttr(elem)}}"
              onclick="saveComment(this.dataset.key,this.dataset.elem)">
        ${{existingComment ? 'Actualizar' : 'Guardar'}}</button>
      <button class="comment-btn cancel" onclick="closeComment()">Cancelar</button>
      ${{deleteBtn}}
    </div>`;

  const displayHTML = `
    <div class="comment-display">${{existingComment}}</div>
    <div class="comment-actions">
      <button class="comment-btn save" onclick="editComment()">Editar</button>
      <button class="comment-btn delete"
              data-key="${{escAttr(key)}}" data-elem="${{escAttr(elem)}}"
              onclick="deleteComment(this.dataset.key,this.dataset.elem)">Eliminar</button>
    </div>`;

  container.innerHTML = `
    <div class="comment-section show">
      <div class="comment-header">
        <span class="elem-name">${{elem}}</span>
        <button class="close-btn" onclick="closeComment()">×</button>
      </div>
      ${{isEditing ? editingHTML : displayHTML}}
    </div>`;

  if (isEditing) setTimeout(() => {{ const ta = document.getElementById('commentText'); if (ta) ta.focus(); }}, 50);
}}

function editComment()  {{ if (activeKey && activeElement) renderDetail(); }}
function closeComment() {{ activeElement = null; renderDetail(); }}

function saveComment(key, elem) {{
  const ta = document.getElementById('commentText');
  if (!ta) return;
  const val = ta.value.trim();
  const ck  = getCommentKey(key, elem);
  if (val) comments[ck] = val; else delete comments[ck];
  saveComments(); renderDetail();
}}

function deleteComment(key, elem) {{
  if (!confirm('¿Estás seguro de que quieres eliminar este comentario?')) return;
  delete comments[getCommentKey(key, elem)];
  saveComments(); activeElement = null; renderDetail();
}}

function selectKey(k) {{
  activeKey     = activeKey === k ? null : k;
  activeElement = null;
  renderKeys(); renderDetail();
}}

renderKeys();
</script>
</body>
</html>"""
    return html

# ─────────────────────────────────────────────────────────────────────────────
# Orquestación principal
# ─────────────────────────────────────────────────────────────────────────────
def run(ctc: str, dias: int, stream_id: str, output_dir: Path, dashboard_dir: Path | None,
        limit: int | None, sleep_interlock: float, checkpoint_file: str,
        generar_dashboard: bool) -> Path:
    """Ejecuta el pipeline completo para un CTC dado y devuelve la ruta del HTML generado."""

    session = requests.Session()
    session.auth = HTTPBasicAuth(GRAYLOG_USER, GRAYLOG_PASSWORD)

    ctc = ctc.upper()

    # 1) Descargar mensajes de Graylog para el CTC solicitado
    hoy = datetime.now(timezone.utc)
    desde = hoy - timedelta(days=dias)

    query = f"contentType:(Block OR Signal OR TrackCircuit OR LevelCrossing) AND ctc:{ctc}"
    logger.info("Descargando mensajes de Graylog para CTC=%s (últimos %d días)...", ctc, dias)
    messages = search_logs(
        session,
        query=query,
        from_date=desde.strftime("%Y-%m-%dT00:00:00.000Z"),
        to_date=hoy.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        stream_id=stream_id,
        limit=limit,
        checkpoint_file=checkpoint_file,
    )

    if not messages:
        raise RuntimeError(f"No se encontraron mensajes en Graylog para CTC={ctc}")

    df_raw = messages_to_dataframe(messages)
    elementos = build_elementos_publicados(df_raw)

    # El CTC real presente en los datos (por si difiere de mayúsculas/formato del parámetro)
    ctc_detectado = elementos["header.ctc"].unique()[0]

    # 2) Descargar topología MSE view para cada enclavamiento visto en los datos
    interlocking = elementos["element.interlock"].unique().tolist()
    logger.info("Descargando topología para %d enclavamientos...", len(interlocking))
    df_topos = fetch_topologia(session, ctc_detectado, interlocking, sleep_seconds=sleep_interlock)

    if df_topos.empty:
        raise RuntimeError("No se pudo obtener topología para ningún enclavamiento")

    # 3) Cruce publicado (CTC) vs topología (MSE view)
    elementos = merge_dependencias(elementos, df_topos)
    topo_sin_recibir, recibir_sin_topo = compute_diffs(df_topos, elementos)

    # 4) Recuperar estado previo (comentarios/fixed/error) del último HTML generado, si existe
    ctc_output_dir = output_dir / ctc_detectado
    html_previo = find_latest_html(ctc_output_dir)
    if html_previo:
        logger.info("Estado previo encontrado: %s", html_previo)
    estado_previo = read_html_state(html_previo)
    df_estado_previo = to_dataframe(estado_previo)

    df_estado = filter_by_dicts(df_estado_previo, topo_sin_recibir, recibir_sin_topo)

    # 5) Construir el HTML del dashboard
    html = build_html(
        df=df_estado,
        label_a="Topo_sin_recibir",
        label_b="recibir_sin_topo",
        output_title=f"REVISION TOPOLOGÍA MSE VIEW: {ctc_detectado}",
        display_label_a=(
            "ELEMENTOS EN CATÁLOGO CTC Y EN TOPOLOGÍA MSE VIEW DE LOS QUE "
            "NO SE RECIBE INFORMACIÓN (INCIDENCIA DE TECNÓLOGO CTC)"
        ),
        display_label_b=(
            "ELEMENTOS PUBLICADOS POR CTC QUE NO ESTÁN CORRECTOS EN "
            "TOPOLOGÍA MSE VIEW (INCIDENCIA TOPOLOGÍA MSE)"
        ),
    )

    # 6) Guardar el HTML (histórico por CTC + copia opcional en dashboard)
    today_str = date.today().strftime("%Y-%m-%d")

    ctc_output_dir.mkdir(parents=True, exist_ok=True)
    fname = ctc_output_dir / f"{today_str}_new_{ctc_detectado}.html"
    fname.write_text(html, encoding="utf-8")
    logger.info("HTML guardado en: %s", fname)

    if generar_dashboard:
        dash_dir = (dashboard_dir or (output_dir / "Dashboard(No tocar)")) / ctc_detectado
        dash_dir.mkdir(parents=True, exist_ok=True)
        dash_fname = dash_dir / f"{today_str}_{ctc_detectado}.html"
        dash_fname.write_text(html, encoding="utf-8")
        logger.info("Copia de dashboard guardada en: %s", dash_fname)

    return fname


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Genera el dashboard de Catálogo Virtual CTC comparando "
                    "los elementos publicados por Graylog contra la topología MSE view.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--ctc", required=True,
                        help="Código del CTC a procesar (p.ej. MAC, BCN, ZAR, COR)")
    parser.add_argument("--dias", type=int, default=2,
                        help="Número de días hacia atrás a consultar en Graylog")
    parser.add_argument("--stream-id", default=DEFAULT_STREAM_ID,
                        help="ID del stream de Graylog a consultar")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_BASE,
                        help="Directorio base donde se guardan los HTML históricos por CTC")
    parser.add_argument("--dashboard-dir", type=Path, default=None,
                        help="Directorio base del dashboard 'no tocar' "
                             "(por defecto: <output-dir>/Dashboard(No tocar))")
    parser.add_argument("--sin-dashboard", action="store_true",
                        help="No generar la copia en el directorio de dashboard")
    parser.add_argument("--limit", type=int, default=None,
                        help="Límite opcional de mensajes a descargar (para pruebas)")
    parser.add_argument("--sleep-interlock", type=float, default=2.0,
                        help="Segundos de espera entre llamadas a la API de topología por enclavamiento")
    parser.add_argument("--checkpoint-file", default="checkpoint.pkl",
                        help="Fichero de checkpoint para reanudar descargas de Graylog interrumpidas")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Activa logging en modo DEBUG")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        fname = run(
            ctc=args.ctc,
            dias=args.dias,
            stream_id=args.stream_id,
            output_dir=args.output_dir,
            dashboard_dir=args.dashboard_dir,
            limit=args.limit,
            sleep_interlock=args.sleep_interlock,
            checkpoint_file=args.checkpoint_file,
            generar_dashboard=not args.sin_dashboard,
        )
    except Exception as e:
        logger.error("El proceso falló: %s", e)
        return 1

    print(f"\n✅ Proceso completado. Dashboard generado en: {fname}")
    return 0


if __name__ == "__main__":
    sys.exit(main())