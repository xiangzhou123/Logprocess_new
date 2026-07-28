"""
rotulacion_desrotulacion.py
===========================
Versión optimizada del notebook original.
Los gráficos se generan con matplotlib directamente en memoria (BytesIO)
y se insertan en el PDF sin guardar ningún fichero de imagen en disco.

Sin dependencias nuevas: usa matplotlib que ya estaba en el entorno original.
"""

# ---------------------------------------------------------------------------
# Importaciones
# ---------------------------------------------------------------------------
import warnings
warnings.simplefilter(action="ignore", category=FutureWarning)

import io
import json
import os
import regex
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # backend sin ventana, necesario en scripts sin GUI
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

import numpy as np
import pandas as pd
import yaml
from lxml import etree
from tqdm.auto import tqdm

from reportlab.lib import colors
from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.api import cargarHistorico, getHistoricoMOW
from src.api.APIs import getPlanificacionCirculacionesTecnicas, hacerPeticion
from src.processor import LogProcessor
from src.processor.log_procesor import LogProcessor
from src.utils import (
    dateFromText,
    formatTimedelta,
    getEstacionamientos,
    getFilesByDate,
    getFilesByWeek,
    getNumbers,
    guardarExcel,
    guardarExcelMulti,
    isEmpty,
    isValidCode,
    listTopos,
    loadEstaciones,
    localizeFecha,
    map_cod2name,
    map_name2use_name,
    parallelizeFunction,
    rellenarId,
    roundGroup,
    setEF,
    slidingWindow,
    sortElements,
    sortStrNumbers,
    splitDataframe,
    splitList,
    splitLongString,
    time2localtime,
)
from src.utils.util import loadEstacionSinCTC
from src.visualizacion.visualizaciones import (
    build_hierarchical_dataframe,
    sample_random_colors,
    setHoverInfo,
)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
TRAIN_TYPES = {
    "Approach": "APROXIMACIÓN",
    "Arrival": "LLEGADA",
    "Departure": "SALIDA",
    "Elimination": "SUPRESIÓN",
    "End": "FIN",
    "Entry": "ENTRY",
    "Exit": "EXIT",
    "Maneuver": "MANIOBRA",
    "Platform": "ORIGEN",
    "PlatformForecast": "PREVISIÓN",
    "Stopped": "STOP",
    "TrackingLost": "LOST_TRACK",
}

MOV_SORTER = {
    v: k
    for k, v in enumerate(
        [
            "PREVISIÓN", "APROXIMACIÓN", "EXIT", "LLEGADA", "FIN",
            "BAJA", "ALTA", "SALIDA",
            "MANIOBRA_APROXIMACIONMANIOBRA_LLEGADA", "MANIOBRA_SALIDA",
        ]
    )
}

DIR1 = {
    "RC CENTRO": "SD CENTRO",
    "RC NORTE": "SD NORTE",
    "RC SUR": "SD SUR",
    "RED DE ALTA VELOCIDAD (RAV)": "SD ALTA VELOCIDAD",
    "RC ESTE": "SD ESTE",
    "RC NOROESTE": "SD NOROESTE",
    "RC NORESTE": "SD NORESTE",
}

DESTINOS_NOERRONEA = {
    "05485": "05482",
    "51419": "51406",
    "60913": "60914",
    "05361": "15206",
    "72303": "B7173",
}

HOSTPATH = "http://info.api.elcano.operaciones.adif/mse-circulations/msecirculations/planning/day/"

CARPETA_DESTINO = (
    r"C:\Users\xiangzhou.zhang\ADIF"
    r"\MSE - 00-CALIDAD DATO"
    r"\_Análisis Calidad Datos MSE y MIE"
    r"\00.Rotulación-Fiabilidad-Supresiones\Rotulación"
)

# ---------------------------------------------------------------------------
# Utilidades de datos
# ---------------------------------------------------------------------------

def cargar_historico(start_date: str, end_date: str) -> pd.DataFrame:
    ntrenes = [rellenarId(el) for el in np.arange(100000)]
    if end_date <= start_date:
        end_date = (pd.to_datetime(start_date) + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    historico = getHistoricoMOW(
        estaciones=[],
        trenes=ntrenes,
        inicio=start_date,
        fin=end_date,
        xSIV=True,
        jCTC=False,
        pro=True,
        maniobra=True,
    )
    historico = historico[
        (historico["Fecha"] >= pd.to_datetime(start_date))
        & (historico["Fecha"] <= pd.to_datetime(end_date))
    ]
    historico = historico[historico["NTécnico"].apply(isValidCode)].dropna(subset=["Movimiento"])
    historico["mov_ord"] = historico["Movimiento"].apply(MOV_SORTER.get)
    return historico


def parse_launching_date(ld):
    try:
        if isinstance(ld, (list, tuple)) and len(ld) >= 3:
            y, m, d = int(ld[0]), int(ld[1]), int(ld[2])
            if len(ld) >= 6:
                hh, mm, ss = int(ld[3]), int(ld[4]), int(ld[5])
                return pd.Timestamp(year=y, month=m, day=d, hour=hh, minute=mm, second=ss)
            return pd.Timestamp(year=y, month=m, day=d)
        return pd.to_datetime(ld, errors="coerce")
    except Exception:
        return pd.NaT


def eliminar_duplicado(estaciones: pd.DataFrame) -> pd.DataFrame:
    estaciones.drop_duplicates(subset=["Código", "Nombre"], inplace=True)
    duplicados = estaciones[estaciones.duplicated("Código", keep=False)]
    estaciones = estaciones[
        ~((estaciones["Código"].isin(duplicados["Código"])) & estaciones["Nombre"].str.endswith("RAM"))
    ]
    estaciones = estaciones[
        ~((estaciones["Código"].isin(duplicados["Código"])) & estaciones["Nombre"].str.endswith("AV"))
    ]
    estaciones.drop_duplicates(subset=["Código"], keep="first", inplace=True)
    return estaciones


# ---------------------------------------------------------------------------
# Carga y procesado de datos
# ---------------------------------------------------------------------------

def procesar_datos(start_date: str, end_date: str):
    """Carga y procesa todos los datos; devuelve (origenes, df_merge)."""

    # ---- Histórico ----
    historico = cargar_historico(start_date, end_date)
    historico = historico[historico["FuenteVía"] != "SITRA_PROVIDED"].copy()
    historico = historico[historico["FuenteMovimiento"] == "CTC_MIE"].copy()
    historico = historico.sort_values(
        by=["FechaOrigen", "NTécnico", "Fecha", "mov_ord"]
    ).reset_index(drop=True)
    historico["Día"] = historico["Fecha"].dt.date
    historico["day_of_week"] = historico["Fecha"].dt.day_of_week
    historico["day_of_year"] = historico["Fecha"].dt.day_of_year
    historico["week_of_year"] = (historico["day_of_year"] / 7).astype(int)

    aux_df = historico[historico["FechaOrigen"] == historico["Fecha"].dt.date].copy()
    aux_split = np.split(
        aux_df,
        np.where(
            (~aux_df["NTécnico"].eq(aux_df["NTécnico"].shift()))
            | (~aux_df["FechaOrigen"].eq(aux_df["FechaOrigen"].shift()))
        )[0][1:],
    )

    cols = [
        "FechaOrigen", "CTC", "NTécnico", "LíneaComercial", "Secuencia",
        "Código", "Nombre", "Movimiento", "CategoríaCirculación", "Empresa", "Núcleo",   "CódigoOrigen",
        "NombreOrigen","CódigoDestino","NombreDestino"
    ]

    origenes_list, destinos_list, ultimos_list = [], [], []

    for df in aux_split:
        if not any(df["Secuencia"] == 1) and not (
            df.shape[0] == 1 and df["Movimiento"].iloc[0] == "PÉRDIDA_SEGUIMIENTO"
        ):
            if not (df["Movimiento"] == "ORIGEN").any():
                df.sort_values(by=["Fecha", "Secuencia"], inplace=True)
                origenes_list.append(df.iloc[0][cols])

        if any((df["Movimiento"].isin(["FIN", "BAJA"])) & (df["Código"] == df["CódigoDestino"])):
            df = df.reset_index(drop=True)
            destino = df[
                (df["Movimiento"].isin(["FIN", "BAJA"])) & (df["Código"] == df["CódigoDestino"])
            ].iloc[-1]
            if df.shape[0] == destino.name + 1:
                continue
            continuacion = df.iloc[destino.name + 1:]
            if any(
                np.invert(continuacion["Código"] == destino["Código"])
                & (continuacion["Secuencia"] == -1)
            ):
                if any(continuacion["Movimiento"].isin(["MANIOBRA_ENTRADA", "MANIOBRA_SALIDA"])):
                    continuacion_maniobra = continuacion[
                        (continuacion["Movimiento"] != "MANIOBRA_APROXIMACION")
                        & (continuacion["Secuencia"] == -1)
                    ]
                    if not continuacion_maniobra.empty:
                        ultimo = continuacion_maniobra.iloc[-1]
                        destino_codigo = destino["Código"]
                        ultimo_codigo = ultimo["Código"]
                        if DESTINOS_NOERRONEA.get(destino_codigo) != ultimo_codigo:
                            if ultimo["Código"] != destino["Código"]:
                                ultimos_list.append(ultimo)
                                destinos_list.append(destino)

    destinos = pd.DataFrame(destinos_list)
    destinos = destinos[[
        "FechaOrigen", "CTC", "NTécnico", "Núcleo", "LíneaComercial", "Secuencia",
        "Código", "Nombre", "Movimiento", "CategoríaCirculación", "Producto", "Empresa","CódigoOrigen",
        "NombreOrigen","CódigoDestino","NombreDestino"
    ]]
    destinos = destinos.sort_values(by=["FechaOrigen", "NTécnico"])
    destinos.sort_values(by=["Secuencia"], inplace=True)

    ultimos = pd.DataFrame(ultimos_list)
    ultimos.reset_index(drop=True, inplace=True)

    info_extra = ultimos[["NTécnico", "FechaOrigen", "Nombre", "Código"]].rename(
        columns={
            "Nombre": "ESTACIÓN HASTA LA QUE SIGUE ROTULADO",
            "Código": "CÓDIGO ESTACIÓN DESROTULAN",
        }
    )

    destinos.rename(
        columns={
            "Secuencia": "SECUENCIA DONDE FINALIZA",
            "Nombre": "ESTACIÓN EN LA QUE FINALIZA",
            "Código": "CÓDIGO ESTACIÓN FINALIZA",
        },
        inplace=True,
    )

    df_merge = pd.merge(destinos, info_extra, how="left", on=["NTécnico", "FechaOrigen"])

    # ---- Estaciones / subdirección ----
    estaciones_sub = pd.read_csv("data/Subdirección.csv")

    origenes_df = pd.DataFrame(origenes_list)
    origenes_df = pd.merge(estaciones_sub, origenes_df, how="right", on=["Código"])

    estaciones_sub_dest = estaciones_sub.rename(
        columns={"Código": "CÓDIGO ESTACIÓN FINALIZA"}
    )
    df_merge = pd.merge(df_merge, estaciones_sub_dest, how="left", on=["CÓDIGO ESTACIÓN FINALIZA"])

    origenes_df.rename(
        columns={
            "Subdirección": "Delegación",
            "Secuencia": "Secuencia en la que se rotula",
        },
        inplace=True,
    )
    origenes_df = origenes_df[[
        "FechaOrigen", "Delegación", "CTC", "NTécnico", "Núcleo", "LíneaComercial",
        "Secuencia en la que se rotula", "Código", "Nombre", "Movimiento",
        "CategoríaCirculación", "Empresa","CódigoOrigen",
        "NombreOrigen","CódigoDestino","NombreDestino"
    ]]
    origenes_df.sort_values(by=["Nombre"], inplace=True)
    origenes_df.reset_index(drop=True, inplace=True)
    df_merge.rename(columns={"Subdirección": "Delegación"}, inplace=True)
    df_merge = df_merge[[
        "FechaOrigen", "Delegación", "CTC", "NTécnico", "LíneaComercial", "Producto",
        "SECUENCIA DONDE FINALIZA", "CÓDIGO ESTACIÓN FINALIZA", "ESTACIÓN EN LA QUE FINALIZA",
        "ESTACIÓN HASTA LA QUE SIGUE ROTULADO", "CÓDIGO ESTACIÓN DESROTULAN",
        "Movimiento", "CategoríaCirculación", "Empresa","CódigoOrigen",
        "NombreOrigen","CódigoDestino","NombreDestino"
    ]]
    df_merge.sort_values(by=["ESTACIÓN EN LA QUE FINALIZA"], inplace=True)
    df_merge.reset_index(drop=True, inplace=True)

    # ---- Planificación comercial ----
    planifi = getPlanificacionCirculacionesTecnicas(start_date)
    comercial = planifi[planifi["esComercial"] == True][["NTécnico", "Fecha", "esComercial"]].copy()
    comercial.rename(columns={"Fecha": "FechaOrigen"}, inplace=True)
    comercial["FechaOrigen"] = pd.to_datetime(comercial["FechaOrigen"]).dt.strftime("%Y-%m-%d")

    origenes_df["FechaOrigen"] = pd.to_datetime(origenes_df["FechaOrigen"]).dt.strftime("%Y-%m-%d")
    origenes_df = pd.merge(origenes_df, comercial, on=["FechaOrigen", "NTécnico"], how="left")
    origenes_df = origenes_df[origenes_df["esComercial"] == True]

    df_merge["FechaOrigen"] = pd.to_datetime(comercial["FechaOrigen"]).dt.strftime("%Y-%m-%d")
    df_merge = pd.merge(df_merge, comercial, on=["FechaOrigen", "NTécnico"], how="left")
    df_merge = df_merge[df_merge["esComercial"] == True]
    origenes_df.rename(columns={"NombreOrigen":"Nombre_origen","CódigoOrigen":"Código_origen"}, inplace=True)
    origenes_df["Secuencia en la que se rotula"] = (
        pd.to_numeric(
            origenes_df["Secuencia en la que se rotula"].replace("", pd.NA),
            errors="coerce",
        )
        .fillna(0)
        .astype("Int64")
    )
    origenes_df["Nombre_origen"] = origenes_df["Nombre_origen"].str.upper()

    if {"Nombre", "Código", "Código_origen", "Nombre_origen"}.issubset(origenes_df.columns):
        mask = origenes_df["Nombre"].isna() & (origenes_df["Código"] == origenes_df["Código_origen"])
        if mask.any():
            origenes_df.loc[mask, "Nombre"] = origenes_df.loc[mask, "Nombre_origen"]

    # Mapeo de delegaciones
    origenes_df["Delegación"] = origenes_df["Delegación"].map(
        lambda x: DIR1.get(x, x) if pd.notnull(x) else x
    )
    df_merge["Delegación"] = df_merge["Delegación"].map(
        lambda x: DIR1.get(x, x) if pd.notnull(x) else x
    )

    return origenes_df, df_merge


# ---------------------------------------------------------------------------
# Gráficos matplotlib → bytes PNG en memoria (sin kaleido, sin ficheros)
# ---------------------------------------------------------------------------

# Paleta Set3 de matplotlib (12 colores), igual que la de Plotly Set3
_SET3 = [
    "#8DD3C7", "#FFFFB3", "#BEBADA", "#FB8072", "#80B1D3",
    "#FDB462", "#B3DE69", "#FCCDE5", "#D9D9D9", "#BC80BD",
    "#CCEBC5", "#FFED6F",
]


def crear_grafico_provincias(df: pd.DataFrame, rotulacion: bool = True) -> io.BytesIO:
    """
    Genera un gráfico de donut con matplotlib y devuelve un BytesIO con el PNG.
    No usa kaleido ni ningún proceso externo; compatible con Python 3.12.
    """
    conteo = df["Delegación"].value_counts()
    labels = conteo.index.tolist()
    values = conteo.values.tolist()
    n = len(labels)
    palette = (_SET3 * ((n // len(_SET3)) + 1))[:n]

    fig, ax = plt.subplots(figsize=(9, 6), facecolor="white")

    wedges, texts, autotexts = ax.pie(
        values,
        labels=None,
        colors=palette,
        autopct=lambda p: f"{int(round(p * sum(values) / 100))}",
        pctdistance=0.75,
        startangle=90,
        wedgeprops=dict(width=0.55),   # donut: hueco interior
    )
    for at in autotexts:
        at.set_fontsize(13)
        at.set_fontweight("bold")

    centro_txt = "Total mal\nrotulados" if rotulacion else "Total mal\ndesrotulados"
    ax.text(0, 0, centro_txt, ha="center", va="center", fontsize=12, fontweight="bold")

    # Leyenda lateral
    legend_patches = [
        mpatches.Patch(color=palette[i], label=f"{labels[i]}  ({values[i]})")
        for i in range(n)
    ]
    ax.legend(
        handles=legend_patches,
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        fontsize=10,
        frameon=False,
    )

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf


def fig_to_bytesio(buf: io.BytesIO) -> io.BytesIO:
    """Devuelve el BytesIO listo para pasarlo directamente a Image() de ReportLab."""
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Cabecera / pie de página PDF
# ---------------------------------------------------------------------------

def add_header(canvas, doc):
    canvas.saveState()
    fecha_actual = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")
    try:
        logo_path = Path("data/logo.png")
        canvas.drawImage(
            str(logo_path),
            doc.leftMargin,
            doc.height + doc.topMargin - 0.75 * inch,
            width=2 * inch,
            height=0.75 * inch,
            preserveAspectRatio=True,
        )
    except Exception:
        canvas.setFont("Helvetica", 10)
        canvas.drawString(doc.leftMargin, doc.height + doc.topMargin - 0.5 * inch, "[LOGO NO ENCONTRADO]")

    center_x = doc.width / 2.0 + doc.leftMargin
    y = doc.height + doc.topMargin - 0.4 * inch
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawCentredString(center_x, y, "INDICADORES CALIDAD MSE")
    canvas.drawCentredString(center_x, y - 12, "Rotulaciones y desrotulaciones")
    canvas.drawCentredString(center_x, y - 24, f"Análisis de datos {fecha_actual}")
    canvas.setFont("Helvetica", 10)
    canvas.drawRightString(
        doc.width + doc.leftMargin,
        doc.height + doc.topMargin - 0.3 * inch,
        f"Fecha: {fecha_actual}",
    )
    canvas.restoreState()


def add_footer(canvas, doc):
    canvas.saveState()
    fecha = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")
    styles = getSampleStyleSheet()
    footer_text_left = (
        "SD. de Sistemas y Medios Operacionales<br/>"
        "D. de Circulación y Gestión de Capacidad<br/>"
        "DG. de OPERACIONES Y EXPLOTACIÓN"
    )
    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=7,
        leading=10,
        spaceBefore=5,
        alignment=0,
    )
    left_paragraph = Paragraph(footer_text_left, footer_style)
    left_paragraph.wrapOn(canvas, 3.5 * inch, 0.5 * inch)
    left_paragraph.drawOn(canvas, 0.5 * inch, 0.3 * inch)
    canvas.setFont("Helvetica", 9)
    footer_text_center = f"Página {doc.page}"
    text_width = canvas.stringWidth(footer_text_center, "Helvetica", 9)
    canvas.drawString(
        (doc.width + doc.leftMargin + doc.rightMargin - text_width) / 2,
        0.5 * inch,
        footer_text_center,
    )
    footer_text_right = f"Fecha: {fecha}"
    right_pos = (
        doc.width + doc.leftMargin
        - canvas.stringWidth(footer_text_right, "Helvetica", 9)
        - 0.75 * inch
    )
    canvas.drawString(right_pos, 0.5 * inch, footer_text_right)
    canvas.restoreState()


def add_header_footer(canvas, doc):
    add_header(canvas, doc)
    add_footer(canvas, doc)


# ---------------------------------------------------------------------------
# Tablas PDF
# ---------------------------------------------------------------------------

def tabla_resumen(df, story, filas_por_pagina=30, col_widths=None, rotulacion=True):
    if df is None or df.empty:
        return False

    if rotulacion:
        target_cols = [c for c in ("Código_origen", "Nombre_origen", "Código", "Nombre") if c in df.columns]
        if not target_cols:
            target_cols = list(df.columns[: min(4, len(df.columns))])
        df_group = df.groupby(target_cols).size().reset_index(name="Nº trenes")
        rename_map = {
            "Código": "Código donde\nse rotula",
            "Nombre": "Nombre donde\nse rotula",
            "Código_origen": "Código\norigen",
            "Nombre_origen": "Nombre\norigen",
        }
    else:
        target_cols = [
            c for c in (
                "CÓDIGO ESTACIÓN FINALIZA", "ESTACIÓN EN LA QUE FINALIZA",
                "CÓDIGO ESTACIÓN DESROTULAN", "ESTACIÓN HASTA LA QUE SIGUE ROTULADO",
            )
            if c in df.columns
        ]
        if not target_cols:
            target_cols = list(df.columns[: min(4, len(df.columns))])
        df_group = df.groupby(target_cols).size().reset_index(name="Nº trenes")
        rename_map = {
            "CÓDIGO ESTACIÓN FINALIZA": "CÓDIGO ESTACIÓN\nFINALIZA",
            "ESTACIÓN EN LA QUE FINALIZA": "ESTACIÓN EN LA QUE\nFINALIZA",
            "CÓDIGO ESTACIÓN DESROTULAN": "CÓDIGO ESTACIÓN\nDESROTULAN",
            "ESTACIÓN HASTA LA QUE SIGUE ROTULADO": "ESTACIÓN HASTA LA QUE\nSIGUE ROTULADO",
        }

    df_group = df_group.rename(columns={k: v for k, v in rename_map.items() if k in df_group.columns})

    sec_col = next(
        (c for c in df_group.columns if "codigo" in c.lower() and ("origen" in c.lower() or "finaliza" in c.lower())),
        None,
    )
    if "Nº trenes" in df_group.columns:
        sort_cols = ["Nº trenes"] + ([sec_col] if sec_col else [])
        asc = [False] + ([True] if sec_col else [])
        df_group = df_group.sort_values(by=sort_cols, ascending=asc).reset_index(drop=True)

    # garantizar columnas únicas
    seen: dict = {}
    unique_cols = []
    for c in df_group.columns:
        if c not in seen:
            seen[c] = 0
            unique_cols.append(c)
        else:
            seen[c] += 1
            unique_cols.append(f"{c}_{seen[c]}")
    df_group.columns = unique_cols

    styles = getSampleStyleSheet()
    header_style = ParagraphStyle("TH", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9, alignment=1, leading=11)
    cell_style = ParagraphStyle("TC", parent=styles["Normal"], fontName="Helvetica", fontSize=8, leading=10, wordWrap="CJK")
    estilo_tabla = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
    ])
    title_style = ParagraphStyle("TT", parent=styles["Heading3"], alignment=1, spaceAfter=6, fontSize=10)

    df_clean = df_group.fillna("").astype(str).reset_index(drop=True)
    partes = [df_clean.iloc[i: i + filas_por_pagina] for i in range(0, len(df_clean), filas_por_pagina)]
    total_partes = max(1, len(partes))

    for i, parte in enumerate(partes):
        titulo = f"Tabla resumen — página {i + 1} / {total_partes}" if total_partes > 1 else "Tabla resumen"
        story.append(Paragraph(titulo, title_style))
        header_row = [Paragraph(h, header_style) for h in parte.columns]
        data_rows = [
            [Paragraph(cell.replace("\n", "<br/>"), cell_style) for cell in row]
            for row in parte.itertuples(index=False)
        ]
        ncols = len(parte.columns)
        cw = col_widths if (col_widths and len(col_widths) == ncols) else [6 * inch / ncols] * ncols
        tabla = Table([header_row] + data_rows, repeatRows=1, colWidths=cw)
        tabla.setStyle(estilo_tabla)
        story.append(tabla)
        if i < len(partes) - 1:
            story.append(PageBreak())
            story.append(Spacer(1, 70))

    return len(partes[-1]) > (filas_por_pagina / 4) if partes else False


def tabla_detalle(df, story, filas_por_pagina=30, col_widths=None, rotulacion=True):
    if df is None or df.empty:
        return

    if rotulacion:
        desired = ["NTécnico", "Código_origen", "Nombre_origen", "Secuencia en la que se rotula", "Código", "Nombre"]
    else:
        desired = ["NTécnico", "CÓDIGO ESTACIÓN FINALIZA", "ESTACIÓN EN LA QUE FINALIZA", "CÓDIGO ESTACIÓN DESROTULAN", "ESTACIÓN HASTA LA QUE SIGUE ROTULADO"]

    cols = [c for c in desired if c in df.columns] or list(df.columns[: min(6, len(df.columns))])
    df_sel = df[cols].copy()
    if "Código_origen" in df_sel.columns:
        df_sel = df_sel.sort_values(by=["Código_origen"]).reset_index(drop=True)
    else:
        df_sel = df_sel.reset_index(drop=True)

    rename_map = {
        "Código": "Código donde\nse rotula",
        "Nombre": "Nombre donde\nse rotula",
        "Código_origen": "Código\norigen",
        "Nombre_origen": "Nombre\norigen",
        "NTécnico": "Nº Técnico",
    }
    df_sel = df_sel.rename(columns={k: v for k, v in rename_map.items() if k in df_sel.columns})

    styles = getSampleStyleSheet()
    header_style = ParagraphStyle("TH", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9, alignment=1, leading=11)
    cell_style = ParagraphStyle("TC", parent=styles["Normal"], fontName="Helvetica", fontSize=8, leading=10, wordWrap="CJK")
    estilo_tabla = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
    ])
    title_style = ParagraphStyle("TT", parent=styles["Heading3"], alignment=1, spaceAfter=6, fontSize=10)

    df_clean = df_sel.fillna("").astype(str).reset_index(drop=True)
    partes = [df_clean.iloc[i: i + filas_por_pagina] for i in range(0, len(df_clean), filas_por_pagina)]
    total_partes = max(1, len(partes))

    for i, parte in enumerate(partes):
        titulo = f"Tabla detalle — página {i + 1} / {total_partes}" if total_partes > 1 else "Tabla detalle"
        story.append(Paragraph(titulo, title_style))
        header_row = [Paragraph(h.replace("\n", "<br/>"), header_style) for h in parte.columns]
        data_rows = [
            [Paragraph(cell.replace("\n", "<br/>"), cell_style) for cell in row]
            for row in parte.itertuples(index=False)
        ]
        ncols = len(parte.columns)
        cw = col_widths if (col_widths and len(col_widths) == ncols) else [6 * inch / ncols] * ncols
        tabla = Table([header_row] + data_rows, repeatRows=1, colWidths=cw)
        tabla.setStyle(estilo_tabla)
        story.append(tabla)
        if i < len(partes) - 1:
            story.append(PageBreak())
            story.append(Spacer(1, 70))


# ---------------------------------------------------------------------------
# Generación del PDF
# ---------------------------------------------------------------------------

def generar_pdf(origenes: pd.DataFrame, df_merge: pd.DataFrame) -> str:
    """
    Construye el PDF completo.
    Los gráficos se generan con matplotlib en memoria (BytesIO) y se
    insertan directamente; no se crea ningún fichero de imagen en disco.
    """
    fecha_ayer = datetime.now() - timedelta(days=1)
    fecha_formateada = fecha_ayer.strftime("%Y-%m-%d")
    semana = fecha_ayer.isocalendar()[1]

    os.makedirs(CARPETA_DESTINO, exist_ok=True)
    filename = os.path.join(
        CARPETA_DESTINO,
        f"Semana{semana}_{fecha_formateada}_rotulaciones_erroneas.pdf",
    )

    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=1 * inch,
        bottomMargin=1 * inch,
    )

    styles = getSampleStyleSheet()
    story: list = []

    # ---- Estilos comunes ----
    verde_oscuro = Color(0 / 255, 100 / 255, 0 / 255)
    verde_claro = Color(52 / 255, 207 / 255, 145 / 255)
    contenido_style = ParagraphStyle("Contenido", parent=styles["Normal"], spaceAfter=12)
    Indicador_style = ParagraphStyle(
        "Indicador",
        parent=styles["Heading1"],
        fontName="Helvetica",
        fontSize=20,
        spaceAfter=20,
        alignment=1,
        textColor=colors.black,
    )

    def barra(texto, color=verde_oscuro):
        t = Table([[texto]], colWidths=[6 * inch])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), color),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 12),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOX", (0, 0), (-1, -1), 1, colors.black),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("LEFTPADDING", (0, 0), (-1, -1), 100),
            ("RIGHTPADDING", (0, 0), (-1, -1), 100),
        ]))
        return t

    # ================================================================
    # PÁGINA 1 – Descripción
    # ================================================================
    story.append(Spacer(1, 70))
    story.append(barra("DESCRIPCIÓN"))
    story.append(Spacer(1, 30))
    story.append(Paragraph(
        "El presente informe tiene como objetivo analizar las incidencias detectadas en los procesos de rotulación y desrotulación de trenes, "
        "específicamente aquellos que no se rotulan en su estación de origen o no se desrotulan en su estación de destino.",
        contenido_style,
    ))
    story.append(Spacer(1, 12))
    story.append(Paragraph("<b>1. Rotulaciones incorrectas en estaciones no origen</b>", contenido_style))
    story.append(Paragraph(
        "Se presentan los casos en los que los trenes no son rotulados correctamente en su estación de origen. "
        "En primer lugar se ofrece una visión global que abarca todas las subdirecciones, seguida de un desglose detallado por cada una de ellas. "
        "Finalmente, se incluyen tablas con un mayor nivel de detalle, en las que se identifican las estaciones específicas donde se han detectado estas incidencias.",
        contenido_style,
    ))
    story.append(Spacer(1, 12))
    story.append(Paragraph("<b>2. Desrotulaciones en estaciones que no son destino</b>", contenido_style))
    story.append(Paragraph(
        "Se analizan los casos en los que las circulaciones son desrotuladas en estaciones distintas a su destino final. "
        "Al igual que en el caso de las rotulaciones, se proporciona una visión general por subdirección, seguida de un desglose específico por cada una de ellas, "
        "y por último se incluye una tabla detallada con información de cada circulación donde se ha identificado esta incidencia.",
        contenido_style,
    ))

    # ================================================================
    # PÁGINA 2 – Índice
    # ================================================================
    story.append(PageBreak())
    story.append(Spacer(1, 70))
    story.append(barra("ÍNDICE DE CONTENIDO"))
    story.append(Spacer(1, 30))
    for item in [
        "Análisis Global",
        "Análisis de rotulación SD AV",
        "Análisis de rotulación de vía SD CENTRO",
        "Análisis de rotulación de vía SD ESTE",
        "Análisis de rotulación de vía SD NORESTE",
        "Análisis de rotulación de vía SD NOROESTE",
        "Análisis de rotulación de vía SD NORTE",
        "Análisis de rotulación de vía SD SUR",
        "Análisis de desrotulación SD AV",
        "Análisis de desrotulación de vía SD CENTRO",
        "Análisis de desrotulación de vía SD ESTE",
        "Análisis de desrotulación de vía SD NORESTE",
        "Análisis de desrotulación de vía SD NOROESTE",
        "Análisis de desrotulación de vía SD NORTE",
        "Análisis de desrotulación de vía SD SUR",
    ]:
        story.append(Paragraph(item, contenido_style))

    # ================================================================
    # PÁGINA 3 – Indicadores (descripción de secciones)
    # ================================================================
    story.append(PageBreak())
    story.append(Spacer(1, 70))
    story.append(barra("INDICADORES"))
    story.append(Spacer(1, 30))
    story.append(Paragraph(
        "<b>Distribución por delegación:</b> se presentan gráficos que reflejan el número total de trenes, así como el porcentaje de trenes con errores de rotulación o desrotulación, desglosados por subdirección.",
        contenido_style,
    ))
    story.append(Paragraph(
        "<b>Tabla resumen:</b> se incluye una tabla consolidada que recoge el número total de trenes incorrectamente rotulados o desrotulados, clasificados según su estación de origen y destino.",
        contenido_style,
    ))
    story.append(Paragraph(
        "<b>Tabla de detalle:</b> se proporciona una tabla detallada que contiene el número técnico de los trenes identificados con errores de rotulación o desrotulación, junto con la secuencia específica en la que se produce la incidencia en cada caso.",
        contenido_style,
    ))

    # ================================================================
    # ROTULACIONES ERRÓNEAS
    # ================================================================
    story.append(PageBreak())
    story.append(Spacer(1, 60))
    story.append(Paragraph("Rotulaciones erróneas", Indicador_style))
    story.append(Spacer(1, 50))
    story.append(barra("Distribución total de errores de rotulación por delegación"))
    story.append(Spacer(1, 40))

    # ---- Gráfico rotulaciones → matplotlib en memoria, sin fichero ----
    buf_rot = crear_grafico_provincias(origenes, rotulacion=True)
    story.append(Image(fig_to_bytesio(buf_rot), width=6 * inch, height=4 * inch))
    story.append(PageBreak())

    direcciones_rot = sorted(origenes["Delegación"].dropna().unique().tolist()) if not origenes.empty else []
    for idx, direc in enumerate(direcciones_rot):
        if idx > 0:
            story.append(PageBreak())
        story.append(Spacer(1, 70))
        story.append(barra(f"Subdirección: {direc}"))
        story.append(Spacer(1, 12))
        df_dir = origenes[origenes["Delegación"] == direc]
        if not df_dir.empty:
            necesita_salto = tabla_resumen(df_dir, story, filas_por_pagina=16, col_widths=[1.0 * inch] * len(df_dir.columns), rotulacion=True)
            if necesita_salto:
                story.append(PageBreak())
                story.append(Spacer(1, 70))
            tabla_detalle(df_dir, story, filas_por_pagina=15, col_widths=[1.0 * inch] * len(df_dir.columns), rotulacion=True)
        else:
            story.append(Paragraph("Sin datos de rotulaciones para esta subdirección.", contenido_style))

    # ================================================================
    # DESROTULACIONES ERRÓNEAS
    # ================================================================
    story.append(PageBreak())
    story.append(Spacer(1, 60))
    story.append(Paragraph("Desrotulaciones erróneas", Indicador_style))
    story.append(Spacer(1, 50))
    story.append(barra("Distribución total de desrotulados por delegaciones"))
    story.append(Spacer(1, 40))

    # ---- Gráfico desrotulaciones → matplotlib en memoria, sin fichero ----
    buf_des = crear_grafico_provincias(df_merge, rotulacion=False)
    story.append(Image(fig_to_bytesio(buf_des), width=6 * inch, height=4 * inch))
    story.append(PageBreak())

    direcciones_des = sorted(df_merge["Delegación"].dropna().unique().tolist()) if not df_merge.empty else []
    for idx, direc in enumerate(direcciones_des):
        if idx > 0:
            story.append(PageBreak())
        story.append(Spacer(1, 70))
        story.append(barra(f"Subdirección: {direc}"))
        story.append(Spacer(1, 12))
        df_dir = df_merge[df_merge["Delegación"] == direc]
        if not df_dir.empty:
            necesita_salto = tabla_resumen(df_dir, story, filas_por_pagina=10, col_widths=[1.0 * inch] * len(df_dir.columns), rotulacion=False)
            if necesita_salto:
                story.append(PageBreak())
                story.append(Spacer(1, 70))
            tabla_detalle(df_dir, story, filas_por_pagina=15, col_widths=[1.0 * inch] * len(df_dir.columns), rotulacion=False)
        else:
            story.append(Paragraph("Sin datos de desrotulaciones para esta subdirección.", contenido_style))

    # ================================================================
    # Build
    # ================================================================
    doc.build(story, onFirstPage=add_header_footer, onLaterPages=add_header_footer)
    print(f"✅ PDF generado: {filename}")
    return filename


# ---------------------------------------------------------------------------
# Guardar Excel (igual que antes)
# ---------------------------------------------------------------------------

def guardar_excel(origenes: pd.DataFrame, df_merge: pd.DataFrame, fecha_ayer: datetime) -> None:
    semana = fecha_ayer.isocalendar()[1]
    today_str = fecha_ayer.strftime("%Y-%m-%d")
    fname = Path(CARPETA_DESTINO) / f"Semana{semana}_{today_str}_rotulaciones_erroneas.xlsx"
    data = {
        "trenes_no_rotulado_en_el_origen": origenes,
        "trenes_que_no_se_desrotulan": df_merge,
    }
    guardarExcelMulti(data, fname)
    print(f"✅ Excel guardado: {fname}")


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def main():
    start_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")

    print(f"📅 Procesando datos: {start_date} → {end_date}")
    origenes, df_merge = procesar_datos(start_date, end_date)

    fecha_ayer = datetime.now() - timedelta(days=1)
    guardar_excel(origenes, df_merge, fecha_ayer)
    generar_pdf(origenes, df_merge)


if __name__ == "__main__":
    main()