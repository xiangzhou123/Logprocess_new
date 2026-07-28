"""
circulaciones_planificadas.py
==============================
Descarga las circulaciones planificadas de un día concreto desde la API,
las aplana a un DataFrame y permite:

  - Guardarlas en Excel indicando fecha y ruta de destino (usa guardarExcel).
  - Consultar en modo "rotación": dado un NTécnico, imprime el valor de
    SigEnlace_Tipo (siguiente enlace) para esa circulación.

Uso:
    # Descargar y guardar
    python circulaciones_planificadas.py --fecha 2026-07-20 --ruta data/circulaciones.xlsx

    # Consultar rotación de un técnico concreto (sin guardar)
    python circulaciones_planificadas.py --fecha 2026-07-20 --rotacion 12345
"""

# ---------------------------------------------------------------------------
# Importaciones
# ---------------------------------------------------------------------------
import argparse
import json
from pathlib import Path

import pandas as pd
import regex

from src.api.APIs import hacerPeticion, parse_launching_date
from src.utils import guardarExcel

# ---------------------------------------------------------------------------
# Descarga y aplanado de circulaciones planificadas
# ---------------------------------------------------------------------------

def getCirculacionesPlanificadas_1(fecha) -> pd.DataFrame:
    """
    Información sobre circulaciones de un día concreto (todos los campos disponibles).

    Args:
        fecha: fecha del día a consultar (str o cualquier tipo aceptado por pd.to_datetime).

    Returns:
        DataFrame con una fila por step/parada de cada circulación planificada.
    """
    HOSTPATH = "http://info.api.elcano.operaciones.adif/mse-circulations/msecirculations/planning/day/"
    data = {"day": pd.to_datetime(fecha).strftime("%Y-%m-%d")}
    data = json.dumps(data)
    response = hacerPeticion("POST", HOSTPATH, data=data)

    res_data = regex.sub(r"\n*data:\s*", ",", response.text)[1:]
    res_data = json.loads(f"[{res_data}]")

    rows = []
    for el in res_data:
        # --- circulationId ---
        cid = el.get("circulationId", {}) or {}
        tecnico = cid.get("number")
        fecha_circ = parse_launching_date(cid.get("launchingDate"))

        # --- dayTrain (nivel raíz) ---
        day_train = el.get("dayTrain", {}) or {}
        all_numbers      = day_train.get("allNumbers", [])
        commercial_num   = day_train.get("commercialNumber")
        commercial_train = day_train.get("commercialTrain")
        company          = day_train.get("company")
        nucleus          = day_train.get("nucleus")
        operator         = day_train.get("operator")
        special          = day_train.get("special")
        train_type       = day_train.get("trainType")
        virtual          = day_train.get("virtual")

        # --- dayTrain.line ---
        line = (day_train.get("line") or {}).get("name")

        # --- dayTrain.identifier ---
        identifier = day_train.get("identifier") or {}
        identifier_number = identifier.get("number")
        identifier_date   = parse_launching_date(identifier.get("launchingDate"))

        # --- dayTrain.connections ---
        connections = day_train.get("connections", {}) or {}

        def parse_connection(conn):
            """Extrae los campos de un bloque next/previous."""
            if not conn:
                return {}
            conn_cid = conn.get("circulationId") or {}
            comm_link = conn.get("commercialLink") or {}
            comm_link_cid = comm_link.get("circulationId") or {}
            return {
                "conn_number":      conn_cid.get("number"),
                "conn_date":        parse_launching_date(conn_cid.get("launchingDate")),
                "conn_time":        conn.get("connectionTime"),
                "conn_station":     conn.get("stationCode"),
                "conn_type":        conn.get("type"),
                "conn_link_number": comm_link_cid.get("number"),
                "conn_link_date":   parse_launching_date(comm_link_cid.get("launchingDate")),
                "conn_link_time":   comm_link.get("connectionTime"),
            }

        next_conn = parse_connection(connections.get("next"))
        prev_conn = parse_connection(connections.get("previous"))
        next_comm_links = connections.get("nextCommercialLinks") or []
        prev_comm_links = connections.get("previousCommercialLinks") or []

        # --- dayTrain.journey ---
        journey = day_train.get("journey", {}) or {}

        def parse_assimilation(assm):
            if not assm:
                return {}
            assm_cid = assm.get("circulationId") or {}
            orig = assm.get("originConnection") or {}
            dest = assm.get("destinationConnection") or {}
            return {
                "assm_number":       assm_cid.get("number"),
                "assm_date":         parse_launching_date(assm_cid.get("launchingDate")),
                "assm_orig_pointId": orig.get("pointId"),
                "assm_orig_step":    orig.get("step"),
                "assm_dest_pointId": dest.get("pointId"),
                "assm_dest_step":    dest.get("step"),
            }

        assimilate_to  = parse_assimilation(journey.get("assimilateTo"))
        assimilated_by = parse_assimilation(journey.get("assimilatedBy"))

        # forecastedSuppressionData
        fsd = journey.get("forecastedSuppresssionData") or {}
        interruption = fsd.get("forecastedSuppressionInterruption") or {}
        forecasted = {
            "fsd_dest_step":       fsd.get("forecastedSuppressionDestinationStep"),
            "fsd_origin_step":     fsd.get("forecastedSuppressionOriginStep"),
            "fsd_ts_dest":         fsd.get("timeStampInMillisSuppressionDestinationStep"),
            "fsd_ts_origin":       fsd.get("timeStampInMillisSuppressionOriginStep"),
            "fsd_ts_interruption": fsd.get("timeStampInMillisSuppressionInterruption"),
            "fsd_int_start_step":  interruption.get("forecastedSuppressionInterruptionStartLocationStep"),
            "fsd_int_end_step":    interruption.get("forecastedSuppressionInterruptionEndLocationStep"),
        }

        # journeySectionsData
        sections_data = journey.get("journeySectionsData") or {}
        sections_ts = sections_data.get("timeStampInMillisSections")
        sections    = sections_data.get("sections") or []

        # --- steps ---
        for s in journey.get("steps") or []:
            row = {
                # circulationId
                "NTécnico":            tecnico,
                "FechaOrigen":         fecha_circ,
                # dayTrain raíz
                "NúmerosAdicionales":  ", ".join(all_numbers) if all_numbers else None,
                "NComercial":          commercial_num,
                "EsTrenComercial":     commercial_train,
                "Compañia":            company,
                "Núcleo":              nucleus,
                "Operador":            operator,
                "Especial":            special,
                "TipoTren":            train_type,
                "Virtual":             virtual,
                "Línea":               line,
                # identifier
                "IdentificadorNúmero": identifier_number,
                "IdentificadorFecha":  identifier_date,
                # connections - next
                "SigConexión_Número":   next_conn.get("conn_number"),
                "SigConexión_Fecha":    next_conn.get("conn_date"),
                "SigConexión_Tiempo":   next_conn.get("conn_time"),
                "SigConexión_Estación": next_conn.get("conn_station"),
                "SigConexión_Tipo":     next_conn.get("conn_type"),
                "SigEnlace_Número":     next_conn.get("conn_link_number"),
                "SigEnlace_Fecha":      next_conn.get("conn_link_date"),
                "SigEnlace_Tiempo":     next_conn.get("conn_link_time"),
                "SigEnlace_Tipo":       next_conn.get("conn_type"),
                # connections - previous
                "AntConexión_Número":   prev_conn.get("conn_number"),
                "AntConexión_Fecha":    prev_conn.get("conn_date"),
                "AntConexión_Tiempo":   prev_conn.get("conn_time"),
                "AntConexión_Estación": prev_conn.get("conn_station"),
                "AntConexión_Tipo":     prev_conn.get("conn_type"),
                "AntEnlace_Número":     prev_conn.get("conn_link_number"),
                "AntEnlace_Fecha":      prev_conn.get("conn_link_date"),
                "AntEnlace_Tiempo":     prev_conn.get("conn_link_time"),
                # commercial links (guardados como JSON string para no explotar filas)
                "SigEnlacesComerciales": json.dumps(next_comm_links) if next_comm_links else None,
                "AntEnlacesComerciales": json.dumps(prev_comm_links) if prev_comm_links else None,
                # assimilateTo
                "AsimilaA_Número":      assimilate_to.get("assm_number"),
                "AsimilaA_Fecha":       assimilate_to.get("assm_date"),
                "AsimilaA_OrigenPunto": assimilate_to.get("assm_orig_pointId"),
                "AsimilaA_OrigenStep":  assimilate_to.get("assm_orig_step"),
                "AsimilaA_DestPunto":   assimilate_to.get("assm_dest_pointId"),
                "AsimilaA_DestStep":    assimilate_to.get("assm_dest_step"),
                # assimilatedBy
                "AsimiladoPor_Número":      assimilated_by.get("assm_number"),
                "AsimiladoPor_Fecha":       assimilated_by.get("assm_date"),
                "AsimiladoPor_OrigenPunto": assimilated_by.get("assm_orig_pointId"),
                "AsimiladoPor_OrigenStep":  assimilated_by.get("assm_orig_step"),
                "AsimiladoPor_DestPunto":   assimilated_by.get("assm_dest_pointId"),
                "AsimiladoPor_DestStep":    assimilated_by.get("assm_dest_step"),
                # forecastedSuppressionData
                **{f"Supresion_{k}": v for k, v in forecasted.items()},
                # journeySectionsData
                "Secciones_TS":   sections_ts,
                "Secciones_JSON": json.dumps(sections) if sections else None,
                # step
                "Secuencia":         s.get("step"),
                "Índice":            s.get("index"),
                "Steps24h":          s.get("steps24h"),
                "Código":            s.get("pointId"),
                "Llegada":           s.get("arrive"),
                "Salida":            s.get("leave"),
                "ModoCirculación":   s.get("circulationMode"),
                "DistanciaAnterior": s.get("distanceToPrevious"),
                "Paridad":           s.get("parity"),
                "Vía_Planificada":   s.get("parkingTrack"),
                "Vía_Salida":        s.get("parkingTrackForDeparture"),
                "TipoParada":        s.get("stationaryType"),
                "ParadaTécnica":     s.get("technicalStop"),
            }
            rows.append(row)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Guardado
# ---------------------------------------------------------------------------

def guardar_circulaciones(df,fecha, ntecnico, ruta: str = None) -> pd.DataFrame:
    """
    Descarga las circulaciones planificadas de `fecha`, filtra por `ntecnico`
    y guarda el resultado mediante guardarExcel.

    Guardar exige un NTécnico concreto: no se permite volcar todo el día
    completo a Excel, solo la circulación de ese técnico.

    Args:
        fecha: fecha a consultar (str "YYYY-MM-DD" o similar).
        ntecnico: NTécnico obligatorio por el que filtrar antes de guardar.
        ruta: ruta del fichero Excel de destino. Si no se indica, se guarda
            en el directorio actual con nombre 'circulaciones_<ntecnico>_<fecha>.xlsx'.
        append_sheet: si True, añade como nueva hoja en vez de sobrescribir
            (se pasa tal cual a guardarExcel).

    Returns:
        El DataFrame filtrado que se ha guardado.
    """
    df_filtrado = df.loc[df["NTécnico"] == ntecnico].copy()

    if df_filtrado.empty:
        raise ValueError(f"No se encontró ninguna circulación con NTécnico={ntecnico} para la fecha seleccionada.")

    fecha_fmt = pd.to_datetime(fecha).strftime("%Y-%m-%d")
    ruta_base = Path.cwd() if ruta is None else Path(ruta)
    ruta_completa = ruta_base / f"circulaciones_{ntecnico}_{fecha_fmt}.xlsx"
    guardarExcel(df_filtrado, str(ruta_completa))
    print(f"✅ {len(df_filtrado)} filas (NTécnico={ntecnico}) guardadas en '{ruta_completa}' (fecha={fecha_fmt})")


# ---------------------------------------------------------------------------
# Consulta de rotación
# ---------------------------------------------------------------------------

def imprimir_rotacion( df,ntecnico) -> pd.Series:
    """
    Dado un NTécnico, imprime (y devuelve) el valor de SigEnlace_Tipo
    para todas las filas de esa circulación.
    """
    
  
    resultado = (
        df.loc[
            df["NTécnico"] == ntecnico,
            ["SigEnlace_Número", "SigEnlace_Tipo"]
        ]
        .drop_duplicates()
        )


    if resultado.empty:
        print(f"⚠ No se encontró NTécnico={ntecnico} en los datos.")
        return resultado


    for _, fila in resultado.iterrows():
        print(
            f"NTécnico: {ntecnico} "
            f"→ SigEnlace_Número: {fila['SigEnlace_Número']} "
            f"→ Tipo: {fila['SigEnlace_Tipo']}"
        )


    return resultado


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Circulaciones planificadas")
    parser.add_argument("--fecha", required=True, help="Fecha a consultar, formato YYYY-MM-DD")
    parser.add_argument("--guardar", action="store_true",
                         help="Guarda en Excel la circulación del NTécnico indicado en --ntecnico")
    parser.add_argument("--ruta",
                         help="Ruta del Excel donde guardar (solo con --guardar). "
                              "Si no se indica, se usa el directorio actual.")
    parser.add_argument("--NTécnico",required=True, help="NTécnico obligatorio para --guardar; también usado por --rotacion")
    parser.add_argument("--rotacion", action="store_true",
                         help="Imprime SigEnlace_Tipo para el NTécnico indicado en --ntecnico")
    args = parser.parse_args()
    df = getCirculacionesPlanificadas_1(args.fecha)
    if args.guardar:
        if not args.NTécnico:
            parser.error("--guardar requiere --NTécnico (NTécnico específico a guardar).")
        else:
            guardar_circulaciones(df, args.fecha,args.NTécnico, args.ruta)


    if args.rotacion:
        if not args.NTécnico:
            parser.error("--rotacion requiere --NTécnico.")
        else:
            imprimir_rotacion(df,args.NTécnico)


if __name__ == "__main__":
    main()