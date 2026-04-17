# fiabilidad_via.py
# Ejecutar desde terminal:
# cd C:\Users\xiangzhou.zhang\Documents\Codigo\LogProcess
# venv\Scripts\activate
# python fiabilidad_via.py

import os
import re
import shutil
import statistics
import warnings
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import regex
from plotly.colors import sample_colorscale
from plotly.express import colors
from plotly.subplots import make_subplots
from tqdm.auto import tqdm

from reportlab.lib import colors as rl_colors
from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
)

from src.api import getInfoEstacionesComerciales
from src.api.APIs import getInfoFiabilidadEstacion
from src.utils import (
    dateFromText, formatTimedelta, getFilesByDate,
    guardarExcel, isEmpty, rellenarId, sortStrNumbers,
)
from src.utils.util import range_normalization, sortElements
from src.visualizacion.color_maps import sample_random_colors
from src.visualizacion.visualizaciones import (
    build_hierarchical_dataframe, mostrarConfusionSankey,
    mostrarConfusionTree, setHoverInfo, setLayout,
)

color24 = colors.qualitative.Dark24
color12 = colors.qualitative.Set3

# ── Kaleido: sin configuración de scope (no existe en v1.x) ─────────────────
pio.kaleido.scope.mathjax = None


# =============================================================================
# FUNCIONES
# =============================================================================

def F1Score(tp: int, v_teorica: int, v_real: int):
    with np.errstate(divide="ignore", invalid="ignore"):
        precision = np.nan_to_num(tp / v_teorica, nan=0.0, posinf=0.0, neginf=0.0)
        recall    = np.nan_to_num(tp / v_real,     nan=0.0, posinf=0.0, neginf=0.0)
        f1        = statistics.harmonic_mean([precision, recall])
    return precision, recall, f1


def scale(x):
    esc  = -8
    prop = 20 * np.log10(x)
    return 1 + (-esc) / (esc - np.exp(-1 / esc * prop))


def estadoEstacion(exactitud: float, f1: float, ntrenes: int):
    if ntrenes == 0:
        return None
    if exactitud == 0 or f1 == 0:
        return 0
    return scale(ntrenes) * statistics.harmonic_mean([exactitud, f1])


def seguimiento_semanal(df: pd.DataFrame, est: str) -> go.Figure:
    grouped = (
        df.groupby(['Fecha Origen', 'Vía Real'])[['Coincide', 'NoCoincide']]
        .sum().reset_index()
    )
    grouped['Fecha Origen'] = pd.to_datetime(grouped['Fecha Origen'])
    vias_unicas_str = grouped['Vía Real'].astype(str).unique().tolist()

    def nat_key(s: str):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]

    orden_vias    = sorted(vias_unicas_str, key=nat_key)
    fechas_unicas = grouped['Fecha Origen'].drop_duplicates().sort_values()
    full_idx      = pd.MultiIndex.from_product(
        [fechas_unicas, orden_vias], names=['Fecha Origen', 'Vía Real']
    )
    grouped = (
        grouped.assign(**{'Vía Real': grouped['Vía Real'].astype(str)})
        .set_index(['Fecha Origen', 'Vía Real'])
        .reindex(full_idx, fill_value=0).reset_index()
    )
    map_via_orden        = {v: i for i, v in enumerate(orden_vias)}
    grouped['__ord__']   = grouped['Vía Real'].map(map_via_orden).fillna(len(orden_vias))
    grouped              = grouped.sort_values(['Fecha Origen', '__ord__']).drop(columns='__ord__')
    grouped['fecha_key'] = grouped['Fecha Origen'].dt.strftime('%Y-%m-%d')
    grouped['fecha_label'] = grouped['Fecha Origen'].dt.strftime('%d-%m')
    grouped['via_str']   = grouped['Vía Real'].astype(str)
    orden_fechas         = fechas_unicas.dt.strftime('%Y-%m-%d').tolist()
    grouped['fecha_key'] = pd.Categorical(grouped['fecha_key'], categories=orden_fechas, ordered=True)
    grouped['via_str']   = pd.Categorical(grouped['via_str'],   categories=orden_vias,  ordered=True)
    x_multi    = [grouped['fecha_key'], grouped['via_str']]
    customdata = np.stack([grouped['fecha_label'].astype(str), grouped['via_str'].astype(str)], axis=-1)

    fig = go.Figure()
    fig.add_bar(
        name='Coincide', x=x_multi, y=grouped['Coincide'],
        marker_color='#2E8B57',
        text=grouped['Coincide'].apply(lambda v: str(v) if v > 0 else ''),
        textposition='inside', textfont=dict(size=9, color='white'),
        customdata=customdata,
        hovertemplate='Fecha: %{customdata[0]}<br>%{customdata[1]}<br>Coincide: %{y}<extra></extra>'
    )
    fig.add_bar(
        name='No Coincide', x=x_multi, y=grouped['NoCoincide'],
        marker_color='#CD5C5C',
        text=grouped['NoCoincide'].apply(lambda v: str(v) if v > 0 else ''),
        textposition='inside', textfont=dict(size=9, color='white'),
        customdata=customdata,
        hovertemplate='Fecha: %{customdata[0]}<br>%{customdata[1]}<br>No Coincide: %{y}<extra></extra>'
    )
    fig.update_layout(
        barmode='stack',
        title={'text': f'Seguimiento semanal coincidencia vía — {est}', 'x': 0.5, 'font': {'size': 20}},
        xaxis_title='Fecha  -  Vía real', yaxis_title='Nº de trenes',
        xaxis=dict(type='multicategory', categoryorder='array', categoryarray=orden_fechas,
                   tickangle=0, tickfont=dict(size=10), showgrid=True, gridwidth=1, gridcolor='lightgray'),
        yaxis=dict(showgrid=True, gridwidth=1, gridcolor='lightgray',
                   zeroline=True, zerolinewidth=1, zerolinecolor='gray', tickfont=dict(size=11)),
        height=700, width=1200, margin=dict(b=140, l=80, r=180, t=80),
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02, font=dict(size=10)),
        plot_bgcolor='white', paper_bgcolor='white',
        bargap=0.25, bargroupgap=0.08,
        uniformtext_minsize=8, uniformtext_mode='hide',
        font=dict(family="DejaVu Sans")
    )
    return fig


def add_header(canv, doc):
    canv.saveState()
    fecha_actual = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")
    try:
        logo_path = Path("data/logo.png")
        canv.drawImage(str(logo_path), doc.leftMargin,
                       doc.height + doc.topMargin - 0.75 * inch,
                       width=2 * inch, height=0.75 * inch, preserveAspectRatio=True)
    except Exception:
        canv.setFont("Helvetica", 10)
        canv.drawString(doc.leftMargin, doc.height + doc.topMargin - 0.5 * inch,
                        "[LOGO NO ENCONTRADO]")
    center_x = doc.width / 2.0 + doc.leftMargin
    y = doc.height + doc.topMargin - 0.4 * inch
    canv.setFont("Helvetica-Bold", 10)
    canv.drawCentredString(center_x, y,      "INDICADORES CALIDAD MSE")
    canv.drawCentredString(center_x, y - 12, "Fiabilidad de vías")
    canv.drawCentredString(center_x, y - 24, f"Análisis de datos {fecha_actual}")
    canv.setFont("Helvetica", 10)
    canv.drawRightString(doc.width + doc.leftMargin,
                         doc.height + doc.topMargin - 0.3 * inch,
                         f"Fecha: {fecha_actual}")
    canv.restoreState()


def add_footer(canv, doc):
    canv.saveState()
    fecha     = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")
    styles    = getSampleStyleSheet()
    footer_l  = ("SD. de Sistemas y Medios Operacionales<br/>"
                 "D. de Circulación y Gestión de Capacidad<br/>"
                 "DG. de OPERACIONES Y EXPLOTACIÓN")
    footer_c  = f"Página {doc.page}"
    footer_r  = f"Fecha: {fecha}"
    footer_style = ParagraphStyle(
        'Footer', parent=styles['Normal'],
        fontSize=7, leading=10, spaceBefore=5, alignment=0
    )
    left_p = Paragraph(footer_l, footer_style)
    left_p.wrapOn(canv, 3.5 * inch, 0.5 * inch)
    left_p.drawOn(canv, 0.5 * inch, 0.3 * inch)
    canv.setFont("Helvetica", 9)
    tw = canv.stringWidth(footer_c, "Helvetica", 9)
    canv.drawString((doc.width + doc.leftMargin + doc.rightMargin - tw) / 2, 0.5 * inch, footer_c)
    rp = doc.width + doc.leftMargin - canv.stringWidth(footer_r, "Helvetica", 9) - 0.75 * inch
    canv.drawString(rp, 0.5 * inch, footer_r)
    canv.setStrokeColor(rl_colors.gray)
    canv.setLineWidth(0.5)
    canv.restoreState()


def add_header_footer(canv, doc):
    add_header(canv, doc)
    add_footer(canv, doc)


def fiabilidad_PDF(provincias: dict):
    fecha_ayer      = datetime.now() - timedelta(days=1)
    fecha_formateada = fecha_ayer.strftime("%Y-%m-%d")
    carpeta_destino = (r"C:\Users\xiangzhou.zhang\ADIF\Elcano - Documentos\Elcano Calidad Dato"
                       r"\_Análisis Calidad Datos MSE y MIE"
                       r"\00.Rotulación-Fiabilidad-Supresiones\Fiabilidad")
    os.makedirs(carpeta_destino, exist_ok=True)
    semana   = fecha_ayer.isocalendar()[1]
    filename = os.path.join(carpeta_destino,
                            f"Semana{semana}_{fecha_formateada}_Fiabilidad_vía.pdf")

    doc    = SimpleDocTemplate(filename, pagesize=A4,
                               leftMargin=0.75*inch, rightMargin=0.75*inch,
                               topMargin=1*inch,     bottomMargin=1*inch)
    styles = getSampleStyleSheet()
    story  = []
    fecha_ayer_str = fecha_ayer.strftime("%d-%m-%Y")

    verde_oscuro = Color(0/255, 100/255, 0/255)
    verde_claro  = Color(52/255, 207/255, 145/255)

    estilo_indice = TableStyle([
        ('BACKGROUND',  (0, 0), (-1, 0), verde_oscuro),
        ('TEXTCOLOR',   (0, 0), (-1, 0), rl_colors.white),
        ('FONTNAME',    (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',    (0, 0), (-1, 0), 12),
        ('ALIGN',       (0, 0), (-1,-1), 'CENTER'),
        ('VALIGN',      (0, 0), (-1,-1), 'MIDDLE'),
        ('BOX',         (0, 0), (-1,-1), 1, rl_colors.black),
        ('INNERGRID',   (0, 0), (-1,-1), 0.5, rl_colors.grey),
        ('LEFTPADDING', (0, 0), (-1,-1), 100),
        ('RIGHTPADDING',(0, 0), (-1,-1), 100),
    ])

    contenido_style = ParagraphStyle('Contenido', parent=styles['Normal'], spaceAfter=12)
    contenido_center = ParagraphStyle('ContenidoCenter', parent=styles['Normal'], spaceAfter=12)

    story.append(Spacer(1, 70))
    descripcion = Table([['DESCRIPCIÓN']], colWidths=[6*inch])
    descripcion.setStyle(estilo_indice)
    story.append(descripcion)
    story.append(Spacer(1, 30))
    story.append(Paragraph(
        "Este informe tiene por objeto de evaluar la fiabilidad en la asignación de vías de "
        "estacionamiento, mediante la comparación entre la vía planificada y la vía real de "
        "estacionamiento de cada circulación, según los datos registrados procedente de distintas "
        "fuentes(CTC, Sitra, Ager y Planificación).", contenido_style))
    story.append(Spacer(1, 30))

    indice = Table([['ÍNDICE DE CONTENIDO']], colWidths=[6*inch])
    indice.setStyle(estilo_indice)
    story.append(indice)
    story.append(Spacer(1, 30))
    for sd_nombre in ["CENTRO", "SUR", "NORTE", "NOROESTE", "NORESTE", "ESTE", "AV"]:
        story.append(Paragraph(f"Análisis de coincidencia de vía SD {sd_nombre}", contenido_center))

    story.append(PageBreak())
    story.append(Spacer(1, 300))

    ind_style = ParagraphStyle('Indicador', parent=styles['Heading1'],
                               fontName='Helvetica', fontSize=20,
                               spaceAfter=20, alignment=1, textColor=rl_colors.black)
    story.append(Paragraph("Indicadores", ind_style))
    story.append(Spacer(1, 50))
    story.append(Paragraph(
        "1. Planificación de vía: se muestra en gráficos, desglosados por subdirecciones, "
        "las cinco estaciones con mayor discrepancia entre la vía planificada y la vía real "
        "de llegada de los trenes.", contenido_style))
    story.append(Paragraph(
        "2. Seguimiento Semanal: se muestra en gráficos, desglosados por subdirecciones, "
        "la coincidencia de las vías en los últimos 7 días de las estaciones con mayor discrepancia.",
        contenido_style))
    story.append(PageBreak())

    ruta           = Path(r"C:\Users\xiangzhou.zhang\Documents\TEST\CoincidenciaVia")
    provincias_keys = list(provincias.keys())

    for prov_idx, (prov_nombre, imagenes_set) in enumerate(provincias.items()):
        ruta_prov = ruta / prov_nombre
        print(f"Procesando imágenes para {ruta_prov}...")
        imagenes = list(imagenes_set)
        if not imagenes:
            print(f"  - No hay imágenes para {prov_nombre}")
            continue

        for i, base in enumerate(imagenes):
            ruta_hoy     = ruta_prov / f"{base}_hoy.png"
            ruta_semanal = ruta_prov / f"{base}_semanal.png"
            existe_hoy     = ruta_hoy.exists()
            existe_semanal = ruta_semanal.exists()

            if not (existe_hoy or existe_semanal):
                print(f"  - No se encontraron imágenes para '{base}'")
                continue

            story.append(Spacer(1, 0))
            barra = Table([[f"Subdirección: {prov_nombre}"]], colWidths=[6*inch])
            barra.setStyle(estilo_indice)
            story.append(Spacer(1, 70))
            story.append(barra)
            story.append(Spacer(1, 30))

            if existe_hoy:
                story.append(Image(ruta_hoy, width=6*inch, height=3.2*inch))
                story.append(Spacer(1, 20))
            else:
                print(f"  - Imagen no encontrada: {ruta_hoy}")

            if existe_semanal:
                story.append(Image(ruta_semanal, width=6*inch, height=3.2*inch))
                story.append(Spacer(1, 20))
            else:
                print(f"  - Imagen no encontrada: {ruta_semanal}")

            es_ultimo   = (i == len(imagenes) - 1)
            es_ult_prov = (prov_idx == len(provincias_keys) - 1)
            if not es_ultimo or not es_ult_prov:
                story.append(PageBreak())

    doc.build(story, onFirstPage=add_header_footer, onLaterPages=add_header_footer)
    print(f"✅ PDF generado: {filename}")


# =============================================================================
# CARGA DE DATOS
# =============================================================================

print("Cargando datos diarios...")
fecha_ini = datetime.now().strftime("%Y-%m-%d")
fecha_fin = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
fuentes   = getFilesByDate(Path("data/FuentesVías"), fecha_ini, fecha_fin, ftype="xlsx")

resumen, computo_estacion, fuentes_vias, detalle_tren = [], [], [], []

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    for f, _ in tqdm(fuentes):
        fecha      = dateFromText(f.stem)
        excel_info = pd.read_excel(f, sheet_name=None, engine="openpyxl")

        r = excel_info["Resumen"].copy()
        r.columns = r.iloc[0].tolist()
        r = r.loc[1:, r.columns[1:]]
        r["Fecha"] = fecha
        resumen.append(r)

        ce = excel_info["CómputoEstación"].copy()
        ce.columns = ce.iloc[2].tolist()
        ce = ce.loc[3:, ce.columns[1:]]
        ce["Fecha"] = fecha
        computo_estacion.append(ce)

        ev = excel_info["CómputoEstaciónVía"].copy()
        ev.columns = ev.iloc[2].tolist()
        ev = ev.loc[3:, ev.columns[1:]]
        ev["Fecha"] = fecha
        fuentes_vias.append(ev)

        dt = excel_info["DetalleTren"].copy()
        dt.columns = dt.iloc[0].tolist()
        dt = dt.loc[1:, dt.columns[1:]]
        detalle_tren.append(dt)

resumen           = pd.concat(resumen).reset_index(drop=True)
computo_estacion  = pd.concat(computo_estacion).reset_index(drop=True)
fuentes_vias      = pd.concat(fuentes_vias).reset_index(drop=True)
detalle_tren      = pd.concat(detalle_tren).reset_index(drop=True)
detalle_tren[["Cod. Est", "Tren"]] = detalle_tren[["Cod. Est", "Tren"]].map(rellenarId)

print("Cargando datos semanales...")
fecha_ini_sem = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
fecha_fin_sem = datetime.now().strftime("%Y-%m-%d")
fuentes_sem   = getFilesByDate(Path("data/FuentesVías"), fecha_ini_sem, fecha_fin_sem, ftype="xlsx")

resumen_s, computo_estacion_s, fuentes_vias_s, detalle_tren_s = [], [], [], []

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    for f, _ in tqdm(fuentes_sem):
        fecha      = dateFromText(f.stem)
        excel_info = pd.read_excel(f, sheet_name=None, engine="openpyxl")

        r = excel_info["Resumen"].copy()
        r.columns = r.iloc[0].tolist()
        r = r.loc[1:, r.columns[1:]]
        r["Fecha"] = fecha
        resumen_s.append(r)

        ce = excel_info["CómputoEstación"].copy()
        ce.columns = ce.iloc[2].tolist()
        ce = ce.loc[3:, ce.columns[1:]]
        ce["Fecha"] = fecha
        computo_estacion_s.append(ce)

        ev = excel_info["CómputoEstaciónVía"].copy()
        ev.columns = ev.iloc[2].tolist()
        ev = ev.loc[3:, ev.columns[1:]]
        ev["Fecha"] = fecha
        fuentes_vias_s.append(ev)

        dt = excel_info["DetalleTren"].copy()
        dt.columns = dt.iloc[0].tolist()
        dt = dt.loc[1:, dt.columns[1:]]
        detalle_tren_s.append(dt)

resumen_semanal          = pd.concat(resumen_s).reset_index(drop=True)
computo_estacion_semanal = pd.concat(computo_estacion_s).reset_index(drop=True)
fuentes_vias_semanal     = pd.concat(fuentes_vias_s).reset_index(drop=True)
detalle_tren_semanal     = pd.concat(detalle_tren_s).reset_index(drop=True)
detalle_tren_semanal[["Cod. Est", "Tren"]] = detalle_tren_semanal[["Cod. Est", "Tren"]].map(rellenarId)

# =============================================================================
# FIABILIDAD
# =============================================================================

print("Cargando fiabilidad...")
if not Path("data/FiabilidadEstaciones.xlsx").exists():
    fiabilidad = []
    estaciones = pd.DataFrame(getInfoEstacionesComerciales())
    for c in tqdm(estaciones[estaciones["commercial"]]["code"].unique()):
        fiab = getInfoFiabilidadEstacion(c)
        fiab["Código"] = c
        fiabilidad.append(fiab)
    fiabilidad = pd.concat(fiabilidad)
    guardarExcel(fiabilidad, "data/FiabilidadEstaciones.xlsx", append_sheet=False)
else:
    fiabilidad = pd.read_excel("data/FiabilidadEstaciones.xlsx")
    fiabilidad["Código"] = fiabilidad["Código"].astype(str).apply(rellenarId)

fiabilidad_semanal = fiabilidad.copy()

# =============================================================================
# MATRICES DE CONFUSIÓN
# =============================================================================

print("Calculando matrices de confusión...")

def build_confusion(df_tren, extra_cols=None):
    cols = ["Provincia", "Subdirección", "Desc Delegacion/Gerencia PR",
            "Cod. Est", "Estación", "Vía Teórica", "Vía Real"]
    if extra_cols:
        cols = extra_cols + cols
    df = df_tren[cols].reset_index(drop=True).copy()
    df[["Vía Teórica", "Vía Real"]] = (
        df[["Vía Teórica", "Vía Real"]].astype(str)
        .map(lambda x: x.split(".")[0].replace("nan", "SinVía"))
    )
    df = df.groupby(by=df.columns.tolist(), as_index=False).size()
    df = df[np.invert((df[["Vía Real", "Vía Teórica"]] == "SinVía").any(axis=1))]
    df["CORRECTO"] = df["size"] * (df["Vía Real"] == df["Vía Teórica"]).astype(int)
    sv = {v: i for i, v in enumerate(sortStrNumbers(df["Vía Real"].unique()))}
    df["_ord_vias"] = df["Vía Real"].apply(sv.get)
    df.rename(columns={"Cod. Est": "Código"}, inplace=True)
    return df

df_confusion         = build_confusion(detalle_tren)
df_confusion_semanal = build_confusion(detalle_tren_semanal, extra_cols=["Fecha Origen"])

df_estaciones = (
    df_confusion.groupby(["Provincia", "Subdirección", "Código", "Estación"])
    .agg({"size": "sum", "CORRECTO": "sum"}).reset_index()
)
df_estaciones["Porcentaje_Total"] = df_estaciones["CORRECTO"] / df_estaciones["size"]

planificacion_region = (
    df_confusion.groupby(["Subdirección", "Provincia", "Estación", "Vía Real", "Vía Teórica"])
    .agg({"size": "sum", "CORRECTO": "sum"}).reset_index()
    .rename(columns={"size": "Total"})
)

planificacion_estaciones = (
    df_confusion[["Subdirección", "Código", "Estación", "size"]]
    .groupby(["Subdirección", "Código", "Estación"]).agg("sum").reset_index()
    .sort_values(by=["size"], ascending=False).reset_index(drop=True).reset_index()
)

planificacion_region_semanal = (
    df_confusion_semanal
    .groupby(["Fecha Origen", "Subdirección", "Provincia", "Estación", "Vía Real", "Vía Teórica"])
    .agg({"size": "sum", "CORRECTO": "sum"}).reset_index()
    .rename(columns={"size": "Total"})
)

# =============================================================================
# GENERACIÓN DE IMÁGENES
# =============================================================================

print("Generando imágenes...")
info_path  = Path(r"C:\Users\xiangzhou.zhang\Documents\TEST\CoincidenciaVia")
fecha_ini  = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
levels     = ["Vía Teórica", "Vía Real", "Estación", "Provincia"]
color_columns = ["CORRECTO", "Total"]
value_column  = "Total"
provincias = {
    "Centro": set(), "Sur": set(), "Norte": set(),
    "Noroeste": set(), "Noreste": set(), "Este": set(), "Alta Velocidad": set()
}

for sd in planificacion_estaciones["Subdirección"].unique():
    riv_path = info_path / str(sd)
    riv_path.mkdir(exist_ok=True, parents=True)

    # Limpiar carpeta
    for p in riv_path.iterdir():
        try:
            p.unlink() if (p.is_file() or p.is_symlink()) else shutil.rmtree(p)
        except Exception as e:
            print(f"  Warning limpiando {p}: {e}")

    df_sd = df_estaciones[df_estaciones["Subdirección"] == sd].sort_values(
        by=["Porcentaje_Total"], ascending=True
    )

    for cod, est in df_sd[["Código", "Estación"]][:5].values:
        provincias[sd].add(est)

        # Figura HOY
        aux_df = df_confusion[
            (df_confusion["Código"] == cod) &
            (df_confusion["Estación"] == est) &
            (df_confusion["Subdirección"] == sd)
        ].copy()

        fig = mostrarConfusionSankey(
            aux_df,
            f"Planificación Vías {est} (SD {sd}) {fecha_ini}",
            "Vía Teórica", "Vía Real", "size",
        )
        fig.html(
            str(riv_path / f"{est}_hoy.png"),
            format="png", width=1200, height=700, scale=2
        )
        print(f"  ✅ {est}_hoy.png")

        # Figura SEMANAL
        semanal = planificacion_region_semanal[
            (planificacion_region_semanal["Estación"] == est) &
            (planificacion_region_semanal["Subdirección"] == sd)
        ].copy()
        semanal['Coincide']   = semanal.apply(
            lambda r: r['Total'] if r['Vía Real'] == r['Vía Teórica'] else 0, axis=1)
        semanal['NoCoincide'] = semanal.apply(
            lambda r: r['Total'] if r['Vía Real'] != r['Vía Teórica'] else 0, axis=1)
        coincide_dia   = semanal.groupby(["Fecha Origen", "Vía Real"])['Coincide'].sum().reset_index()
        nocoincide_dia = semanal.groupby(["Fecha Origen", "Vía Real"])['NoCoincide'].sum().reset_index()
        final = pd.merge(coincide_dia, nocoincide_dia, how="left", on=["Fecha Origen", "Vía Real"])

        fig1 = seguimiento_semanal(final, est)
        fig1.html(
            str(riv_path / f"{est}_semanal.png"),
            format="png", width=1200, height=700, scale=2
        )
        print(f"  ✅ {est}_semanal.png")

# =============================================================================
# GENERACIÓN DEL PDF
# =============================================================================

print("Generando PDF...")
fiabilidad_PDF(provincias)

print("✅ Proceso completado.")