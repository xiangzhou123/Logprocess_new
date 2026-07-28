# %%
import requests
import numpy as np
import argparse
from requests.auth import HTTPBasicAuth
import pandas as pd
import json
from datetime import datetime, timezone, timedelta
import pickle
import os
import re
from GCT import imprimir_rotacion
from src.utils.ficheros import guardarExcel, guardarExcelMulti
from pathlib import Path
from src.utils.util import loadEstaciones,loadEstacionSinCTC
from dotenv import load_dotenv
from html import unescape
import xml.etree.ElementTree as ET
import pandas as pd
GRAYLOG_URL = os.getenv("GRAYLOG_URL")
GRAYLOG_USER = os.getenv("GRAYLOG_USER")
GRAYLOG_PASSWORD = os.getenv("GRAYLOG_PASSWORD")
import time
from datetime import datetime, timedelta, timezone
from src.utils.ficheros import guardarExcel
from pathlib import Path



def graylog_get(url, params, headers, max_retries=5):
    for attempt in range(max_retries):
        try:
            r = requests.get(
                url, params=params,
                auth=HTTPBasicAuth(GRAYLOG_USER, GRAYLOG_PASSWORD),
                headers=headers, timeout=30
            )
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
            print(f"\n⏱️  Error red — reintento {attempt+1}/{max_retries} en {wait}s...")
            time.sleep(wait)

    raise RuntimeError(f"❌ Fallaron {max_retries} reintentos")


def fetch_window(query, from_str, to_str, stream_id, headers,
                 batch_size=5000, _depth=0):
    """
    Descarga una ventana de tiempo. Si encuentra error 500 por offset alto,
    divide la ventana en 2 mitades y las descarga recursivamente.
    Máximo 8 niveles de recursión (ventana mínima ~1s).
    """
    if _depth > 8:
        print(f"\n⛔ Ventana demasiado densa incluso dividida: {from_str} → {to_str}")
        return []

    messages = []
    offset    = 0

    while True:
        params = {
            "query": query, "from": from_str, "to": to_str,
            "limit": batch_size, "offset": offset,
            "fields": "timestamp,source,message,level,contentType"
        }
        if stream_id:
            params["filter"] = f"streams:{stream_id}"

        try:
            data  = graylog_get(f"{GRAYLOG_URL}/api/search/universal/absolute", params, headers)
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
                t_to   = datetime.fromisoformat(to_str.replace("Z", "+00:00"))
                mid    = t_from + (t_to - t_from) / 2
                mid_str = mid.strftime("%Y-%m-%dT%H:%M:%S.000Z")

                print(f"\n✂️  Dividiendo [{from_str} → {to_str}] (offset={offset}, depth={_depth})")

                # Descartar mensajes parciales de esta ventana y rehacer por mitades
                left  = fetch_window(query, from_str, mid_str, stream_id, headers,
                                     batch_size, _depth + 1)
                right = fetch_window(query, mid_str, to_str, stream_id, headers,
                                     batch_size, _depth + 1)
                return messages[:offset - len(batch)] + left + right
            raise

    return messages


def get_total_results(query, from_str, to_str, stream_id, headers):
    params = {"query": query, "from": from_str, "to": to_str,
              "limit": 1, "offset": 0, "fields": "timestamp"}
    if stream_id:
        params["filter"] = f"streams:{stream_id}"
    data = graylog_get(f"{GRAYLOG_URL}/api/search/universal/absolute", params, headers)
    return data.get("total_results", 0)


def search_logs(query, from_date, to_date, stream_id=None, limit=None,
                max_per_window=5000, checkpoint_file="checkpoint.pkl"):
    headers      = {"Accept": "application/json"}
    all_messages = []
    range_start  = datetime.fromisoformat(from_date.replace("Z", "+00:00"))
    range_end    = datetime.fromisoformat(to_date.replace("Z", "+00:00"))
    total_seconds = (range_end - range_start).total_seconds()

    # Reanudar desde checkpoint
    resume_from = range_start
    if os.path.exists(checkpoint_file):
        print(f"♻️  Reanudando desde checkpoint...")
        with open(checkpoint_file, "rb") as f:
            ckpt = pickle.load(f)
        all_messages = ckpt["messages"]
        resume_from  = ckpt["last_window_end"]
        print(f"   {len(all_messages):,} msgs ya descargados, continuando desde {resume_from}\n")

    # Calcular ventana óptima
    print("🔍 Calculando total de mensajes...")
    total_global = get_total_results(
        query,
        range_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        range_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        stream_id, headers
    )
    print(f"📊 Total: {total_global:,}")

    if total_global == 0:
        return []

    density        = total_global / total_seconds
    window_seconds = max(int((max_per_window / density) * 0.85), 5)
    remaining      = (range_end - resume_from).total_seconds()
    print(f"⚙️  Ventana: {window_seconds}s | Estimadas: {int(remaining/window_seconds)+1}\n")

    current      = resume_from
    window_count = 0

    while current < range_end:
        window_end = min(current + timedelta(seconds=window_seconds), range_end)
        from_str   = current.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        to_str     = window_end.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        try:
            batch = fetch_window(query, from_str, to_str, stream_id, headers)
            all_messages.extend(batch)
            window_count += 1
            print(f"✅ {from_str} → {to_str} | +{len(batch):,} | Total: {len(all_messages):,}")

        except RuntimeError as e:
            with open(checkpoint_file, "wb") as f:
                pickle.dump({"messages": all_messages, "last_window_end": current}, f)
            print(f"\n💾 Guardado emergencia: {len(all_messages):,} msgs")
            raise e

        # Checkpoint cada 50 ventanas
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


def search_logs_to_dataframe(query, from_date, to_date, stream_id=None,
                              limit=None, checkpoint_file="checkpoint.pkl"):
    messages = search_logs(query, from_date, to_date, stream_id, limit,
                           checkpoint_file=checkpoint_file)
    if not messages:
        return pd.DataFrame()
    rows = [msg.get("message", msg) for msg in messages]
    return pd.DataFrame(rows)



def validar_fecha(fecha):
    try:
        datetime.strptime(fecha, "%Y-%m-%d")
        return True
    except ValueError:
        return False








# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Circulaciones planificadas")
    parser.add_argument("--fecha", required=True, help="Fecha a consultar, formato YYYY-MM-DD")
    parser.add_argument("--tipo", required=True,type =int, help="Tipo de mensaje sitra: 1 realmovement, 2 realparking, 3 ruoperation")
    parser.add_argument("--ruta",
                         help="Ruta del Excel donde guardar (solo con --guardar). "
                              "Si no se indica, se usa el directorio actual.")
    parser.add_argument("--append-sheet", action="store_true",
                         help="Si se indica --guardar, añade como hoja nueva en vez de sobrescribir")
    parser.add_argument("--NTecnico", help="NTécnico obligatorio para --guardar; también usado por --rotacion")

    args = parser.parse_args()

    if args.tipo not in [1, 2, 3]:
        parser.error("Seleccione un tipo válido: 1 -> RM, 2 -> RP o 3 -> RU")

    try:
        fecha = datetime.strptime(args.fecha, "%Y-%m-%d")
    except ValueError:
        parser.error(
            f"Fecha inválida '{args.fecha}'. Formato esperado: YYYY-MM-DD"
        )
    print("Argumentos correctos")

    
    fecha = datetime.strptime(args.fecha, "%Y-%m-%d")
    fecha_mas_1 = fecha + timedelta(days=1)
    
    queries = {
        1: "realMovement",
        2: "realParkingTrack",
        3: "ruOperationRequest"
    }
    query = queries[args.tipo]
    
    if args.NTecnico:
        query = f'{query} AND "{args.NTecnico}"'

    data = search_logs(
        query=query,
        from_date=fecha.strftime("%Y-%m-%dT00:00:00.000Z"),
        to_date=fecha_mas_1.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        stream_id="692d53016456d79315fb46c6",
        limit=None
    )
    print(f"Mensajes descargados: {len(data)}")
    lista_messages = [item['message']['message'] for item in data]
    xml_messages = [
    x for x in lista_messages
    if isinstance(x, str) and '<?xml' in x
    ]
    rows = []
    for msg in xml_messages:
        try:
            # Convierte &lt; en < y &gt; en >
            txt = unescape(msg)
            # Busca el inicio real del XML
            pos = txt.find('<?xml')
            if pos == -1:
                continue
            xml = txt[pos:]
            root = ET.fromstring(xml)
            fila = {}
            for child in root:
                fila[child.tag] = child.text
            rows.append(fila)
        except Exception as e:
            print("Error:", e)

    df = pd.DataFrame(rows)
    ruta = Path(args.ruta) if args.ruta else Path.cwd()
    ruta_excel = ruta / "Sitra.xlsx"
    guardarExcel(df,ruta_excel)
    print("Archivo generado en:", ruta_excel)


if __name__ == "__main__":
    main()
