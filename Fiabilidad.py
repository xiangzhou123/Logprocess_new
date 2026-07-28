"""
fiabilidad_vias.py
==================
Script optimizado del notebook fiabilidad_vias.ipynb.

Los gráficos se generan en memoria (BytesIO) y se insertan directamente en el
PDF sin ficheros intermedios HTML/PNG ni Chrome headless:
  - Sankey Plotly  → kaleido==0.2.1  (pip install "kaleido==0.2.1")
  - Barras semanales → matplotlib (Agg, sin GUI)

Los ficheros de origen "FuentesVías" (fuentevias_yyyy_mm_dd.xlsx) se descargan
directamente de SharePoint (carpeta "05. Fuentes de Vías") en memoria, sin
tocar disco. La fiabilidad de estaciones (FiabilidadEstaciones.xlsx) se sigue
leyendo/generando localmente.
"""

# ---------------------------------------------------------------------------
# Importaciones
# ---------------------------------------------------------------------------
import io
import os
import re
import statistics
import warnings
warnings.simplefilter(action="ignore", category=FutureWarning)

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd
from tqdm.auto import tqdm

from reportlab.lib import colors
from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from src.api import getInfoEstacionesComerciales
from src.api.APIs import (
    getInfoFiabilidadEstacion, uploadSharepoint, descargarSharepoint_bytesio,
)
from src.utils import (
    dateFromText, guardarExcel, isEmpty, rellenarId, sortStrNumbers,
)
from src.utils.util import range_normalization, sortElements

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
CARPETA_SHAREPOINT = "00.Rotulación-Fiabilidad-Supresiones/Fiabilidad"
CARPETA_FUENTES_VIAS = "05. Fuentes de Vía"

# Paleta de colores vivos para nodos (igual que en el notebook Plotly)
_NODE_COLORS = [
    "#4CAF50","#2196F3","#FF5722","#9C27B0","#FFC107",
    "#00BCD4","#E91E63","#8BC34A","#FF9800","#3F51B5",
    "#607D8B","#F44336","#009688","#CDDC39","#795548",
]
_DARK24 = _NODE_COLORS  # alias para compatibilidad con grafico_semanal

# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------

def _rango_fechas(fecha_ini: str, fecha_fin: str):
    """Genera la lista de fechas (date) entre fecha_ini y fecha_fin, ambas incluidas."""
    d0 = datetime.strptime(fecha_ini, "%Y-%m-%d").date()
    d1 = datetime.strptime(fecha_fin, "%Y-%m-%d").date()
    dias = (d1 - d0).days
    return [d0 + timedelta(days=i) for i in range(dias + 1)]


def _cargar_excels(fecha_ini: str, fecha_fin: str):
    """Descarga y lee de SharePoint los xlsx de FuentesVías en el rango de fechas dado.

    Espera un archivo por día en la carpeta CARPETA_FUENTES_VIAS con nomenclatura
    'fuentevias_yyyy_mm_dd.xlsx'. Los días sin archivo se omiten (con aviso).
    """
    fechas = _rango_fechas(fecha_ini, fecha_fin)
    resumen_l, computo_l, fuentes_vias_l, detalle_l = [], [], [], []

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with tqdm(total=len(fechas), desc="Descargando excels SharePoint") as pbar:
            for fecha in fechas:
                nombre_archivo = f"FuenteVías_{fecha.strftime('%Y-%m-%d')}.xlsx"
                pbar.set_description(str(fecha))

                resultado = descargarSharepoint_bytesio(nombre_archivo, CARPETA_FUENTES_VIAS)
                if not resultado.get("success"):
                    pbar.set_description(f"⚠ No encontrado: {nombre_archivo}")
                    pbar.update()
                    continue

                xl = pd.read_excel(resultado["stream"], sheet_name=None, engine="openpyxl")

                r = xl["Resumen"].copy()
                r.columns = r.iloc[0].tolist()
                r = r.loc[1:, r.columns[1:]]
                r["Fecha"] = fecha
                resumen_l.append(r)

                ce = xl["CómputoEstación"].copy()
                ce.columns = ce.iloc[2].tolist()
                ce = ce.loc[3:, ce.columns[1:]]
                ce["Fecha"] = fecha
                computo_l.append(ce)

                ev = xl["CómputoEstaciónVía"].copy()
                ev.columns = ev.iloc[2].tolist()
                ev = ev.loc[3:, ev.columns[1:]]
                ev["Fecha"] = fecha
                fuentes_vias_l.append(ev)

                dt = xl["DetalleTren"].copy()
                dt.columns = dt.iloc[0].tolist()
                dt = dt.loc[1:, dt.columns[1:]]
                detalle_l.append(dt)

                pbar.update()

    if not resumen_l:
        raise RuntimeError(
            f"No se encontró ningún archivo 'fuentevias_*.xlsx' en SharePoint "
            f"para el rango {fecha_ini} → {fecha_fin} (carpeta '{CARPETA_FUENTES_VIAS}')."
        )

    resumen_l = [
        df.loc[:, ~df.columns.duplicated()]
        for df in resumen_l
    ]
    resumen      = pd.concat(resumen_l).reset_index(drop=True)
    computo      = pd.concat(computo_l).reset_index(drop=True)
    fuentes_vias = pd.concat(fuentes_vias_l).reset_index(drop=True)
    detalle      = pd.concat(detalle_l).reset_index(drop=True)
    detalle[["Cod. Est", "Tren"]] = detalle[["Cod. Est", "Tren"]].map(rellenarId)
    return resumen, computo, fuentes_vias, detalle


def cargar_fiabilidad() -> pd.DataFrame:
    path = Path("data/FiabilidadEstaciones.xlsx")
    if path.exists():
        fiab = pd.read_excel(path)
        fiab["Código"] = fiab["Código"].astype(str).apply(rellenarId)
    else:
        fiab = []
        estaciones = pd.DataFrame(getInfoEstacionesComerciales())
        for c in tqdm(estaciones[estaciones["commercial"]]["code"].unique(), desc="Fiabilidad API"):
            f = getInfoFiabilidadEstacion(c)
            f["Código"] = c
            fiab.append(f)
        fiab = pd.concat(fiab)
        guardarExcel(fiab, str(path), append_sheet=False)
    return fiab


# ---------------------------------------------------------------------------
# Procesado de datos
# ---------------------------------------------------------------------------

def _agregar_fuentes_vias(df: pd.DataFrame, con_fecha: bool = False) -> pd.DataFrame:
    cols_num = ["Num. Trenes","Registro Vía","FuenteCTC","FuenteSitra",
                "FuenteAger","Coincide Vía","Coincide Vía CTC"]
    df[cols_num] = df[cols_num].apply(pd.to_numeric, errors="coerce")

    group_cols = (["Fecha"] if con_fecha else []) + [
        "Provincia","Subdirección","Desc Delegacion/Gerencia PR",
        "Código","Estación","Vía Real de Estacionamiento",
    ]
    agg_cols = group_cols + cols_num
    out = df[agg_cols].groupby(group_cols).agg("sum").reset_index()
    out["% Coincide Vía"]     = out["Coincide Vía"] / out["Num. Trenes"]
    out["% Coincide Vía CTC"] = out["Coincide Vía CTC"].astype(float) / out["FuenteCTC"].astype(float)
    out["% Registro Vía CTC"] = out["FuenteCTC"] / out["Num. Trenes"]
    out = out.dropna(subset=["Código","Estación","Vía Real de Estacionamiento"]).fillna(0)
    out["Vía Real de Estacionamiento"] = out["Vía Real de Estacionamiento"].astype(int).astype(str)
    return out


def _build_resumen_fiabilidad(fuentes_agg: pd.DataFrame, fiabilidad: pd.DataFrame,
                               con_fecha: bool = False) -> pd.DataFrame:
    # fiabilidad es estática (sin columna Fecha), siempre se une solo por Código + Técnica
    left_on  = ["Código", "Vía Real de Estacionamiento"]
    right_on = ["Código", "Técnica"]
    rf = pd.merge(fuentes_agg, fiabilidad, left_on=left_on, right_on=right_on, how="left")
    rf["% Registro Vía"] = rf["Registro Vía"] / rf["Num. Trenes"]
    base_cols = (["Fecha"] if con_fecha else []) + [
        "Provincia","Subdirección","Desc Delegacion/Gerencia PR",
        "Código","Estación","Vía Real de Estacionamiento",
        "Num. Trenes","Registro Vía","% Registro Vía",
        "Coincide Vía","% Coincide Vía","Técnica","Comercial","Fiabilidad",
    ]
    rf = rf[[c for c in base_cols if c in rf.columns]]
    rf = rf.rename(columns={"Coincide Vía": "Correctas", "% Coincide Vía": "% Correctas"})
    return rf


def _build_confusion(detalle: pd.DataFrame, con_fecha: bool = False) -> tuple:
    """Devuelve (df_confusion, df_estaciones, planificacion_region, planificacion_estaciones)."""
    fecha_cols = ["Fecha Origen"] if con_fecha else []
    base_cols  = fecha_cols + [
        "Provincia","Subdirección","Desc Delegacion/Gerencia PR",
        "Cod. Est","Estación","Vía Teórica","Vía Real",
    ]
    df = detalle[[c for c in base_cols if c in detalle.columns]].reset_index(drop=True).copy()
    df[["Vía Teórica","Vía Real"]] = (
        df[["Vía Teórica","Vía Real"]].astype(str)
        .map(lambda x: x.split(".")[0].replace("nan","SinVía"))
    )
    df = df.groupby(by=df.columns.tolist(), as_index=False).size()
    df = df[~(df[["Vía Real","Vía Teórica"]] == "SinVía").any(axis=1)]
    df["CORRECTO"] = df["size"] * (df["Vía Real"] == df["Vía Teórica"]).astype(int)
    sort_vias = {v: i for i, v in enumerate(sortStrNumbers(df["Vía Real"].unique()))}
    df["_ord_vias"] = df["Vía Real"].apply(sort_vias.get)
    df.rename(columns={"Cod. Est": "Código"}, inplace=True)

    group_est_cols = fecha_cols + ["Provincia","Subdirección","Código","Estación"]
    df_est = (
        df.groupby(group_est_cols)
        .agg({"size": "sum", "CORRECTO": "sum"})
        .reset_index()
    )
    df_est["Porcentaje_Total"] = df_est["CORRECTO"] / df_est["size"]
    df_est.rename(columns={"size": "Tamaño_Total","CORRECTO": "Correctos_Total"}, inplace=True)

    plan_group = fecha_cols + ["Subdirección","Provincia","Estación","Vía Real","Vía Teórica"]
    plan_region = (
        df.groupby(plan_group).agg({"size":"sum","CORRECTO":"sum"})
        .reset_index().rename(columns={"size":"Total"})
    )

    plan_est = (
        df[["Subdirección","Código","Estación","size"]]
        .groupby(["Subdirección","Código","Estación"]).agg("sum").reset_index()
        .sort_values(by=["size"], ascending=False).reset_index(drop=True).reset_index()
    )

    return df, df_est, plan_region, plan_est


# ---------------------------------------------------------------------------
# Métricas F1
# ---------------------------------------------------------------------------

def F1Score(tp: int, v_teorica: int, v_real: int):
    with np.errstate(divide="ignore", invalid="ignore"):
        precision = np.nan_to_num(tp / v_teorica, nan=0.0, posinf=0.0, neginf=0.0)
        recall    = np.nan_to_num(tp / v_real,     nan=0.0, posinf=0.0, neginf=0.0)
        f1 = statistics.harmonic_mean([precision, recall])
    return precision, recall, f1


def scale(x):
    esc = -8
    prop = 20 * np.log10(x)
    return 1 + (-esc) / (esc - np.exp(-1 / esc * prop))


def estadoEstacion(exactitud: float, f1: float, ntrenes: int):
    if ntrenes == 0:
        return None
    if exactitud == 0 or f1 == 0:
        return 0
    return scale(ntrenes) * statistics.harmonic_mean([exactitud, f1])


def calcular_estado_vias(detalle: pd.DataFrame, con_fecha: bool = False):
    """Calcula estado_vias y resumen_estacion (diario o semanal)."""
    fecha_cols = ["Fecha Origen"] if con_fecha else []
    iter_cols  = fecha_cols + ["Provincia","Subdirección","Desc Delegacion/Gerencia PR","Cod. Est","Estación"]

    estado_l, resumen_l = [], []
    for row in tqdm(detalle[iter_cols].dropna().drop_duplicates().itertuples(index=False),
                    desc="Calculando F1"):
        filt = pd.Series([True] * len(detalle))
        for col, val in zip(iter_cols, row):
            filt &= (detalle[col] == val)
        aux = detalle[filt].reset_index(drop=True).copy()
        aux["CoincideVía"] = aux["CoincideVía"].astype(bool)
        aux[["Vía Teórica","Vía Real"]] = (
            aux[["Vía Teórica","Vía Real"]].astype(str)
            .map(lambda x: "" if str(x).startswith("#") else x.split(".")[0].replace("nan",""))
        )

        fecha_val = getattr(row, "Fecha_Origen", None) if con_fecha else None
        prov, subd, ger = row.Provincia, row.Subdirección, getattr(row, "Desc_Delegacion_Gerencia_PR", "")
        # acceso robusto para columnas con espacios/barras
        ger  = aux["Desc Delegacion/Gerencia PR"].iloc[0]
        cod  = aux["Cod. Est"].iloc[0]
        est  = aux["Estación"].iloc[0]

        vias = set(aux["Vía Real"].tolist() + aux["Vía Teórica"].tolist())
        est_vias = []
        for v in vias:
            if isEmpty(v):
                continue
            tp       = (aux["CoincideVía"]) & (aux["Vía Teórica"] == v)
            v_teo    = aux["Vía Teórica"] == v
            v_real   = aux["Vía Real"] == v
            prefix   = ([fecha_val] if con_fecha else [])
            est_vias.append(prefix + [prov, subd, ger, cod, est, v,
                                       tp.sum(), v_teo.sum(), v_real.sum()])

        cols_ev = (["Fecha Origen"] if con_fecha else []) + [
            "Provincia","Subdirección","Desc Delegacion/Gerencia PR",
            "Código","Estación","Vía","tp","v_teorica","v_real",
        ]
        ev_df = pd.DataFrame(est_vias, columns=cols_ev)
        ev_df[["Precisión","Exhaustividad","F1"]] = (
            ev_df[["tp","v_teorica","v_real"]].apply(lambda x: F1Score(**x), axis=1).tolist()
        )
        ev_df = ev_df.sort_values(
            by="Vía", key=lambda s: pd.to_numeric(s, errors="coerce"), na_position="last"
        )
        estado_l.append(ev_df)

        acc = aux["CoincideVía"].sum() / aux.shape[0]
        with np.errstate(divide="ignore", invalid="ignore"):
            f1_w = np.nan_to_num(
                (ev_df["F1"] * ev_df["v_real"]).sum() / ev_df["v_real"].sum(),
                nan=0.0, posinf=0.0, neginf=0.0,
            )
        resumen_l.append(
            (([fecha_val] if con_fecha else []) +
             [prov, subd, ger, cod, est, ev_df["v_real"].sum(), acc, f1_w])
        )

    res_cols = (["Fecha Origen"] if con_fecha else []) + [
        "Provincia","Subdirección","Desc Delegacion/Gerencia PR",
        "Código","Estación","MovimientosReales","Exactitud","F1_proporcional",
    ]
    estado_vias     = pd.concat(estado_l) if estado_l else pd.DataFrame()
    resumen_estacion = pd.DataFrame(resumen_l, columns=res_cols)
    resumen_estacion["Estado"] = resumen_estacion[
        ["Exactitud","F1_proporcional","MovimientosReales"]
    ].apply(lambda x: estadoEstacion(x["Exactitud"], x["F1_proporcional"], x["MovimientosReales"]), axis=1)
    return estado_vias, resumen_estacion


# ---------------------------------------------------------------------------
# Gráficos → BytesIO en memoria (sin ficheros en disco)
# ---------------------------------------------------------------------------

def _init_kaleido():
    """
    Inicializa kaleido 0.2.x apuntando al plotly.min.js del paquete plotly.
    Copia el JS a una ruta sin espacios (C:/tmp/plotly.min.js) para evitar
    el error de kaleido con rutas que contienen espacios o caracteres especiales.
    """
    import warnings as _w
    _w.filterwarnings("ignore", category=DeprecationWarning)

    import shutil as _shutil
    import plotly, plotly.io as pio
    from pathlib import Path as _Path

    # Buscar plotly.min.js dentro del paquete plotly
    plotly_pkg = _Path(plotly.__file__).parent
    candidates = list(plotly_pkg.rglob("plotly.min.js"))
    if not candidates:
        return

    src = candidates[0]

    # Copiar a una ruta corta sin espacios ni caracteres especiales
    dest = _Path("C:/tmp/plotly.min.js")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        _shutil.copy2(src, dest)

    plotlyjs_path = str(dest)

    # API nueva (plotly >= 5.19)
    try:
        pio.defaults.plotlyjs = plotlyjs_path
    except AttributeError:
        pass
    # API antigua (kaleido 0.2.x)
    try:
        pio.kaleido.scope.plotlyjs = plotlyjs_path
    except Exception:
        pass


def _nat_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def grafico_sankey_matplotlib(df_confusion_est: pd.DataFrame, titulo: str) -> io.BytesIO:
    """
    Sankey matplotlib bezier — estilo notebook original:
    fondo gris claro, flujos grises semitransparentes, nodos con colores vivos.
    """
    from matplotlib.patches import PathPatch, FancyBboxPatch
    from matplotlib.path import Path as MplPath

    def _nat(s):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]

    if df_confusion_est.empty:
        fig, ax = plt.subplots(figsize=(8, 4), facecolor="#F0F0F0")
        ax.set_facecolor("#F0F0F0")
        ax.text(0.5, 0.5, "Sin datos", ha="center", va="center", fontsize=14, color="#555")
        ax.axis("off")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor="#F0F0F0")
        plt.close(fig); buf.seek(0); return buf

    col_src, col_dst, col_val = "Vía Teórica", "Vía Real", "size"
    agg      = df_confusion_est.groupby([col_src, col_dst])[col_val].sum().reset_index()
    srcs     = sorted(agg[col_src].unique(), key=_nat)
    dsts     = sorted(agg[col_dst].unique(), key=_nat)
    all_vias = sorted(set(srcs + dsts), key=_nat)
    cmap     = {v: _NODE_COLORS[i % len(_NODE_COLORS)] for i, v in enumerate(all_vias)}

    src_totals = agg.groupby(col_src)[col_val].sum()
    dst_totals = agg.groupby(col_dst)[col_val].sum()
    grand      = max(src_totals.sum(), dst_totals.sum())

    PAD = 0.03

    def node_positions(labels, totals):
        total_h = sum(totals.get(l, 0) for l in labels) / grand
        gap     = PAD * (len(labels) - 1)
        scale   = (0.85 - gap) / max(total_h, 1e-9)
        pos = {}; y = 0.07
        for l in labels:
            h = (totals.get(l, 0) / grand) * scale
            pos[l] = (y, y + h)
            y += h + PAD
        return pos

    pos_src = node_positions(srcs, src_totals)
    pos_dst = node_positions(dsts, dst_totals)

    fig_h  = max(4.5, (len(srcs) + len(dsts)) * 0.35)
    fig, ax = plt.subplots(figsize=(12, fig_h), facecolor="#F0F0F0")
    ax.set_facecolor("#F0F0F0")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1.05); ax.axis("off")
    ax.set_title(titulo, fontsize=12, pad=14, color="#333333")

    X_SRC, X_DST, NODE_W = 0.18, 0.82, 0.018

    src_cursor = {l: pos_src[l][0] for l in srcs}
    dst_cursor = {l: pos_dst[l][0] for l in dsts}

    # Flujos bezier grises
    for _, row in agg.sort_values(col_val, ascending=False).iterrows():
        s, d, v = row[col_src], row[col_dst], row[col_val]
        if s not in pos_src or d not in pos_dst:
            continue
        node_h = pos_src[s][1] - pos_src[s][0]
        h      = (v / max(src_totals.get(s, 1), 1e-9)) * node_h
        y0b = src_cursor[s]; y0t = y0b + h
        y1b = dst_cursor[d]; y1t = y1b + h
        src_cursor[s] += h; dst_cursor[d] += h

        verts = [
            (X_SRC + NODE_W, y0b),
            (X_SRC + 0.22, y0b), (X_DST - 0.22, y1b), (X_DST - NODE_W, y1b),
            (X_DST - NODE_W, y1t),
            (X_DST - 0.22, y1t), (X_SRC + 0.22, y0t), (X_SRC + NODE_W, y0t),
            (X_SRC + NODE_W, y0b),
        ]
        codes = [MplPath.MOVETO,
                 MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
                 MplPath.LINETO,
                 MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
                 MplPath.CLOSEPOLY]
        ax.add_patch(PathPatch(MplPath(verts, codes),
                               facecolor="#BBBBBB", edgecolor="none", alpha=0.55, zorder=1))

    # Nodos izquierda (Planificación)
    for l in srcs:
        y0, y1 = pos_src[l]
        ax.add_patch(FancyBboxPatch(
            (X_SRC - NODE_W, y0), NODE_W * 2, y1 - y0,
            boxstyle="square,pad=0", facecolor=cmap[l], edgecolor="none", zorder=3))
        ax.text(X_SRC + NODE_W + 0.012, (y0 + y1) / 2,
                f"Vía{l}: {int(src_totals.get(l, 0))}",
                ha="left", va="center", fontsize=9, color="#333333", zorder=4)

    # Nodos derecha (Real)
    for l in dsts:
        y0, y1 = pos_dst[l]
        ax.add_patch(FancyBboxPatch(
            (X_DST - NODE_W, y0), NODE_W * 2, y1 - y0,
            boxstyle="square,pad=0", facecolor=cmap[l], edgecolor="none", zorder=3))
        ax.text(X_DST - NODE_W - 0.012, (y0 + y1) / 2,
                f"Vía{l}: {int(dst_totals.get(l, 0))}",
                ha="right", va="center", fontsize=9, color="#333333", zorder=4)

    ax.text(X_SRC, 0.02, "Planificación", ha="center", fontsize=10, color="#666666", style="italic")
    ax.text(X_DST, 0.02, "Real",          ha="center", fontsize=10, color="#666666", style="italic")

    plt.tight_layout(pad=0.5)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="#F0F0F0")
    plt.close(fig); buf.seek(0)
    return buf


def grafico_semanal_matplotlib(df_final: pd.DataFrame, estacion: str) -> io.BytesIO:
    """
    Barras apiladas semanal — estilo notebook original:
    rojo=NoCoincide, verde=Coincide, eje X multicategoría Fecha-Vía.
    """
    COLOR_C  = "#2E7D32"   # verde oscuro Coincide
    COLOR_NC = "#C62828"   # rojo  oscuro NoCoincide

    def _nat(s):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]

    if df_final.empty:
        fig, ax = plt.subplots(figsize=(12, 5), facecolor="white")
        ax.text(0.5, 0.5, "Sin datos", ha="center", va="center", fontsize=14)
        ax.axis("off")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor="white")
        plt.close(fig); buf.seek(0); return buf

    grouped = (
        df_final.groupby(["Fecha Origen", "Vía Real"])[["Coincide", "NoCoincide"]]
        .sum().reset_index()
    )
    grouped["Fecha Origen"] = pd.to_datetime(grouped["Fecha Origen"])
    fechas = sorted(grouped["Fecha Origen"].unique())
    vias   = sorted(grouped["Vía Real"].astype(str).unique(), key=_nat)

    # Grid completo fecha × vía
    idx = pd.MultiIndex.from_product([fechas, vias], names=["Fecha Origen", "Vía Real"])
    grouped["Vía Real"] = grouped["Vía Real"].astype(str)
    grouped = (grouped.set_index(["Fecha Origen", "Vía Real"])
               .reindex(idx, fill_value=0).reset_index())

    n    = len(grouped)
    x    = np.arange(n)
    bw   = 0.65

    fig, ax = plt.subplots(figsize=(max(14, n * 0.55), 6), facecolor="white")
    ax.set_facecolor("white")

    ax.bar(x, grouped["Coincide"],   width=bw, color=COLOR_C,  label="Coincide",   zorder=3)
    ax.bar(x, grouped["NoCoincide"], width=bw, color=COLOR_NC, label="No Coincide",
           bottom=grouped["Coincide"], zorder=3)

    # Etiquetas de valor
    for i, (c, nc) in enumerate(zip(grouped["Coincide"], grouped["NoCoincide"])):
        if c > 0:
            ax.text(i, c / 2, str(int(c)), ha="center", va="center",
                    fontsize=6.5, color="white", fontweight="bold", zorder=4)
        if nc > 0:
            ax.text(i, c + nc / 2, str(int(nc)), ha="center", va="center",
                    fontsize=6.5, color="white", fontweight="bold", zorder=4)

    # Ticks menores = vías
    ax.set_xticks(x)
    ax.set_xticklabels(grouped["Vía Real"].astype(str), fontsize=7.5)
    ax.tick_params(axis="x", length=0)

    # Etiquetas de fecha centradas por grupo + separadores
    fecha_groups: dict = {}
    for i, row in grouped.iterrows():
        k = pd.Timestamp(row["Fecha Origen"]).strftime("%Y-%m-%d")
        fecha_groups.setdefault(k, []).append(i)   # i == posición en x

    for fecha_str, positions in fecha_groups.items():
        cx = np.mean(positions)
        ax.annotate(fecha_str,
                    xy=(cx, 0), xycoords=("data", "axes fraction"),
                    xytext=(0, -26), textcoords="offset points",
                    ha="center", va="top", fontsize=8, color="#444444")
        if positions[0] > 0:
            ax.axvline(positions[0] - 0.5, color="#CCCCCC", linewidth=0.8, zorder=1)

    ax.set_ylabel("Nº de trenes", fontsize=10)
    ax.set_xlabel("\nFecha  -  Vía real", fontsize=10, labelpad=22)
    ax.set_title("Seguimiento semanal coincidencia vía", fontsize=13,
                 color="#2C3E50", pad=12)
    ax.yaxis.grid(True, color="#E8E8E8", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.spines[["top", "right", "bottom"]].set_visible(False)
    ax.spines["left"].set_color("#CCCCCC")

    ax.legend(
        handles=[
            mpatches.Patch(color=COLOR_NC, label="No Coincide"),
            mpatches.Patch(color=COLOR_C,  label="Coincide"),
        ],
        loc="upper right", fontsize=9, framealpha=0.95, edgecolor="#CCCCCC",
    )

    plt.subplots_adjust(bottom=0.20)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig); buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Cabecera / pie de página PDF
# ---------------------------------------------------------------------------

def add_header(canvas, doc):
    canvas.saveState()
    fecha_actual = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")
    try:
        canvas.drawImage(
            str(Path("data/logo.png")), doc.leftMargin,
            doc.height + doc.topMargin - 0.75 * inch,
            width=2 * inch, height=0.75 * inch, preserveAspectRatio=True,
        )
    except Exception:
        canvas.setFont("Helvetica", 10)
        canvas.drawString(doc.leftMargin, doc.height + doc.topMargin - 0.5 * inch,
                          "[LOGO NO ENCONTRADO]")
    center_x = doc.width / 2.0 + doc.leftMargin
    y = doc.height + doc.topMargin - 0.4 * inch
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawCentredString(center_x, y,      "INDICADORES CALIDAD MSE")
    canvas.drawCentredString(center_x, y - 12, "Fiabilidad de vías")
    canvas.drawCentredString(center_x, y - 24, f"Análisis de datos {fecha_actual}")
    canvas.setFont("Helvetica", 10)
    canvas.drawRightString(doc.width + doc.leftMargin,
                           doc.height + doc.topMargin - 0.3 * inch,
                           f"Fecha: {fecha_actual}")
    canvas.restoreState()


def add_footer(canvas, doc):
    canvas.saveState()
    fecha = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")
    styles = getSampleStyleSheet()
    footer_style = ParagraphStyle("Footer", parent=styles["Normal"],
                                  fontSize=7, leading=10, spaceBefore=5, alignment=0)
    p = Paragraph(
        "SD. de Sistemas y Medios Operacionales<br/>"
        "D. de Circulación y Gestión de Capacidad<br/>"
        "DG. de OPERACIONES Y EXPLOTACIÓN",
        footer_style,
    )
    p.wrapOn(canvas, 3.5 * inch, 0.5 * inch)
    p.drawOn(canvas, 0.5 * inch, 0.3 * inch)
    canvas.setFont("Helvetica", 9)
    center_txt = f"Página {doc.page}"
    tw = canvas.stringWidth(center_txt, "Helvetica", 9)
    canvas.drawString(
        (doc.width + doc.leftMargin + doc.rightMargin - tw) / 2, 0.5 * inch, center_txt
    )
    right_txt = f"Fecha: {fecha}"
    canvas.drawString(
        doc.width + doc.leftMargin - canvas.stringWidth(right_txt, "Helvetica", 9) - 0.75 * inch,
        0.5 * inch, right_txt,
    )
    canvas.restoreState()


def add_header_footer(canvas, doc):
    add_header(canvas, doc)
    add_footer(canvas, doc)


# ---------------------------------------------------------------------------
# Construcción del PDF
# ---------------------------------------------------------------------------

def _barra(texto: str, color=None):
    if color is None:
        color = Color(0 / 255, 100 / 255, 0 / 255)
    t = Table([[texto]], colWidths=[6 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), color),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0), 12),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("BOX",          (0, 0), (-1, -1), 1, colors.black),
        ("INNERGRID",    (0, 0), (-1, -1), 0.5, colors.grey),
        ("LEFTPADDING",  (0, 0), (-1, -1), 100),
        ("RIGHTPADDING", (0, 0), (-1, -1), 100),
    ]))
    return t


def generar_pdf(
    df_confusion: pd.DataFrame,
    df_estaciones: pd.DataFrame,
    planificacion_region: pd.DataFrame,
    planificacion_region_semanal: pd.DataFrame,
) -> str:
    fecha_ayer      = datetime.now() - timedelta(days=1)
    fecha_formateada = fecha_ayer.strftime("%Y-%m-%d")
    fecha_ini       = fecha_ayer.strftime("%Y-%m-%d")
    semana          = fecha_ayer.isocalendar()[1]
    buffer_pdf = io.BytesIO()
    nombre_archivo = f"Semana{semana}_{fecha_formateada}_Fiabilidad_vía.pdf"

    doc = SimpleDocTemplate(
        buffer_pdf, pagesize=A4,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=1 * inch,     bottomMargin=1 * inch,
    )

    styles = getSampleStyleSheet()
    contenido_style = ParagraphStyle("Contenido", parent=styles["Normal"], spaceAfter=12)
    indicador_style = ParagraphStyle(
        "Indicador", parent=styles["Heading1"], fontName="Helvetica",
        fontSize=20, spaceAfter=20, alignment=1, textColor=colors.black,
    )
    story = []

    # ================================================================
    # PÁGINA 1 – Descripción + Índice
    # ================================================================
    story.append(Spacer(1, 70))
    story.append(_barra("DESCRIPCIÓN"))
    story.append(Spacer(1, 30))
    story.append(Paragraph(
        "Este informe tiene por objeto evaluar la fiabilidad en la asignación de vías de "
        "estacionamiento, mediante la comparación entre la vía planificada y la vía real de "
        "estacionamiento de cada circulación, según los datos registrados procedentes de "
        "distintas fuentes (CTC, Sitra, Ager y Planificación).",
        contenido_style,
    ))
    story.append(Spacer(1, 30))
    story.append(_barra("ÍNDICE DE CONTENIDO"))
    story.append(Spacer(1, 30))
    for sd in ["SD CENTRO","SD SUR","SD NORTE","SD NOROESTE","SD NORESTE","SD ESTE","SD AV"]:
        story.append(Paragraph(f"Análisis de coincidencia de vía {sd}", contenido_style))

    # ================================================================
    # PÁGINA 2 – Descripción indicadores
    # ================================================================
    story.append(PageBreak())
    story.append(Spacer(1, 70))
    story.append(_barra("INDICADORES"))
    story.append(Spacer(1, 30))
    story.append(Paragraph(
        "<b>1. Planificación de vía:</b> gráficos por subdirección de las cinco estaciones "
        "con mayor discrepancia entre la vía planificada y la vía real.",
        contenido_style,
    ))
    story.append(Paragraph(
        "<b>2. Seguimiento Semanal:</b> coincidencia de vías en los últimos 7 días para "
        "las estaciones con mayor discrepancia.",
        contenido_style,
    ))

    # ================================================================
    # PÁGINAS POR SUBDIRECCIÓN
    # ================================================================
    story.append(PageBreak())

    subdirecciones = planificacion_region["Subdirección"].dropna().unique().tolist()
    total_graficos = sum(
        min(5, len(df_estaciones[df_estaciones["Subdirección"] == sd]))
        for sd in subdirecciones
    )
    fmt_graf = "  {desc:40s} {bar} {n_fmt}/{total_fmt}  [{elapsed}]"

    with tqdm(total=total_graficos, bar_format=fmt_graf, colour="cyan") as pbar_g:
     for sd_idx, sd in enumerate(subdirecciones):
        if sd_idx > 0:
            story.append(PageBreak())

        story.append(Spacer(1, 70))
        story.append(_barra(f"Subdirección: {sd}"))
        story.append(Spacer(1, 20))

        # Top 5 estaciones con más discrepancia en el día
        df_sd = df_estaciones[df_estaciones["Subdirección"] == sd].sort_values(
            by="Porcentaje_Total", ascending=True
        )
        top5 = df_sd[["Código","Estación"]].head(5).values

        if len(top5) == 0:
            story.append(Paragraph("Sin datos para esta subdirección.", contenido_style))
            continue

        for cod, est in top5:
            pbar_g.set_description(f"🖼  {sd} — {est[:25]}")
            # ---- Gráfico HOY: heatmap Vía Teórica × Vía Real ----
            aux_hoy = df_confusion[
                (df_confusion["Código"] == cod) &
                (df_confusion["Estación"] == est) &
                (df_confusion["Subdirección"] == sd)
            ].copy()

            buf_hoy = grafico_sankey_matplotlib(
                aux_hoy,
                f"Planificación Vías — {est} (SD {sd}) — {fecha_ini}",
            )
            story.append(Image(buf_hoy, width=6 * inch, height=3.0 * inch))
            story.append(Spacer(1, 10))

            # ---- Gráfico SEMANAL: barras apiladas Coincide/NoCoincide ----
            semanal = planificacion_region_semanal[
                (planificacion_region_semanal["Estación"] == est) &
                (planificacion_region_semanal["Subdirección"] == sd)
            ].copy()

            if not semanal.empty:
                semanal["Coincide"] = semanal.apply(
                    lambda r: r["Total"] if r["Vía Real"] == r["Vía Teórica"] else 0, axis=1
                )
                semanal["NoCoincide"] = semanal.apply(
                    lambda r: r["Total"] if r["Vía Real"] != r["Vía Teórica"] else 0, axis=1
                )
                coincide_d   = semanal.groupby(["Fecha Origen","Vía Real"])["Coincide"].sum().reset_index()
                nocoincide_d = semanal.groupby(["Fecha Origen","Vía Real"])["NoCoincide"].sum().reset_index()
                final = pd.merge(coincide_d, nocoincide_d, on=["Fecha Origen","Vía Real"], how="left")
            else:
                final = pd.DataFrame(columns=["Fecha Origen","Vía Real","Coincide","NoCoincide"])

            buf_sem = grafico_semanal_matplotlib(final, est)
            story.append(Image(buf_sem, width=6 * inch, height=3.0 * inch))
            story.append(Spacer(1, 20))

            pbar_g.update(1)

            # Salto de página entre estaciones (no al final)
            story.append(PageBreak())
            story.append(Spacer(1, 70))
            story.append(_barra(f"Subdirección: {sd}"))
            story.append(Spacer(1, 20))

    print("  📝 Compilando PDF...")
    doc.build(story, onFirstPage=add_header_footer, onLaterPages=add_header_footer)
    contenido_pdf = buffer_pdf.getvalue()
    buffer_pdf.close()

    resultado = uploadSharepoint(
        nombre_archivo=nombre_archivo,
        contenido_archivo=contenido_pdf,
        carpeta=CARPETA_SHAREPOINT,
    )

    if not resultado.get("success"):
        raise RuntimeError(f"Error al subir el PDF a SharePoint: {resultado}")

    print(f"✅ PDF subido a SharePoint: {nombre_archivo}")
    return nombre_archivo


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def _paso(pbar: tqdm, mensaje: str) -> None:
    """Avanza la barra principal y muestra el mensaje actual."""
    pbar.set_description(mensaje)
    pbar.update(1)


def main():
    hoy      = datetime.now()
    ayer     = hoy - timedelta(days=1)
    hace7    = hoy - timedelta(days=7)

    fecha_hoy  = hoy.strftime("%Y-%m-%d")
    fecha_ayer = ayer.strftime("%Y-%m-%d")
    fecha_sem  = hace7.strftime("%Y-%m-%d")

    pasos_totales = 8
    fmt = "{desc:45s} {bar} {n_fmt}/{total_fmt} pasos  [{elapsed}]"

    with tqdm(total=pasos_totales, bar_format=fmt, colour="green") as pbar:

        _paso(pbar, f"📅 Cargando datos diarios ({fecha_ayer})")
        _, computo, fuentes_vias, detalle = _cargar_excels(fecha_ayer, fecha_hoy)

        _paso(pbar, f"📅 Cargando datos semanales ({fecha_sem}→{fecha_ayer})")
        _, _, fuentes_vias_sem, detalle_sem = _cargar_excels(fecha_sem, fecha_hoy)

        _paso(pbar, "📊 Cargando fiabilidad IHM")
        fiabilidad = cargar_fiabilidad()

        _paso(pbar, "🔧 Agregando fuentes de vía")
        fuentes_agg     = _agregar_fuentes_vias(fuentes_vias,     con_fecha=False)
        fuentes_agg_sem = _agregar_fuentes_vias(fuentes_vias_sem, con_fecha=True)
        fuentes_agg["Código"]     = fuentes_agg["Código"].apply(rellenarId)
        fuentes_agg_sem["Código"] = fuentes_agg_sem["Código"].apply(rellenarId)
        _build_resumen_fiabilidad(fuentes_agg,     fiabilidad, con_fecha=False)
        _build_resumen_fiabilidad(fuentes_agg_sem, fiabilidad, con_fecha=True)

        _paso(pbar, "🔢 Calculando matriz de confusión diaria")
        df_confusion, df_estaciones, plan_region, _ = _build_confusion(detalle, con_fecha=False)

        _paso(pbar, "🔢 Calculando matriz de confusión semanal")
        _, _, plan_region_sem, _ = _build_confusion(detalle_sem, con_fecha=True)

        _paso(pbar, "🎨 Generando gráficos y construyendo PDF")
        generar_pdf(df_confusion, df_estaciones, plan_region, plan_region_sem)

        _paso(pbar, "✅ Proceso completado")


if __name__ == "__main__":
    main()