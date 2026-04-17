# pd.set_option("future.no_silent_downcasting", True)
import warnings

warnings.simplefilter(action="ignore", category=FutureWarning)

import argparse
from datetime import timedelta
from pathlib import Path
import plotly.io as pio
import numpy as np
import pandas as pd
import regex
import yaml
from tqdm.auto import tqdm
import os

from src.api import cargarHistorico, getHistoricoMOW
from src.processor import LogProcessor
# pd.set_option("future.no_silent_downcasting", True)
import warnings

warnings.simplefilter(action="ignore", category=FutureWarning)
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path
from datetime import datetime
from reportlab.lib.units import inch
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import regex
import yaml
from lxml import etree
from tqdm.auto import tqdm
from src.api.APIs import (
    getEstadoCirculacionesTecnicas,
    getPlanificacionCirculacionesTecnicas,
    getCirculacionesPlanificadas)
from src.api import cargarHistorico, getHistoricoMOW
from src.processor import LogProcessor
from src.processor import SitraProcessor
from src.utils import formatTimedelta
from src.utils import (
    dateFromText,
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
    loadEstacionSinCTC,
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
from src.visualizacion.visualizaciones import (
    build_hierarchical_dataframe,
    sample_random_colors,
    setHoverInfo,
)
from src.visualizacion.utils import (
    crear_grafico_provincias
)

# Orden lógico de movimientos
mov_sorter = {
    v: k
    for k, v in enumerate(
        [
            "PREVISIÓN",
            "APROXIMACIÓN",
            "MANIOBRALLEGADA",
            "EXIT",
            "LLEGADA",
            "FIN",
            "BAJA",
            "ALTA",
            "SALIDA",
            "EXIT",
            "MANIOBRASALIDA",
            "MANIOBRA",
        ]
    )
}

from datetime import datetime
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from pathlib import Path
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.units import inch
from datetime import datetime
from reportlab.lib.colors import Color
from datetime import timedelta
from reportlab.platypus import PageBreak

def parse_date(date_str):
    return datetime.strptime(date_str, '%Y-%m-%d')
def conteo_sin_ctc (df):
    estaciones_sin_ctc = loadEstacionSinCTC()
    merge = pd.merge(
        estaciones_sin_ctc, 
        df, 
        how="left", 
        on="Código"
    )
    sin_ctc = merge[~merge["Nombre_x"].isna()]
    agrupado = sin_ctc.groupby(["Código", "FuenteMovimiento"]).size().reset_index(name="count").sort_values(by="count", ascending=False)
    agrupado.rename(columns={"Nombre_y":"Nombre"}, inplace=True)
    final = agrupado[agrupado["FuenteMovimiento"].isin(["SITRA","CTC_MIE","MSE"])].copy()
    final.sort_values(by=["Código"],inplace=True)
    if final.empty:
        counts = pd.DataFrame(columns=["Código"])  # evitar errores si final está vacío
    else:
        counts = (
        final.groupby(["Código", "FuenteMovimiento"])["count"]
        .sum()
        .unstack(fill_value=0)  # una columna por cada FuenteMovimiento
        .reset_index()
        )
    counts.columns.name = None

    # unir con la lista completa de estaciones (solo Código y Nombre para evitar columnas textuales extras)
    final_counts = pd.merge(
        estaciones_sin_ctc[["Código", "Nombre"]],
        counts,
        on="Código",
        how="left",
    ).fillna(0)

    # asegurar tipo entero en los contadores (solo para las columnas que realmente representan conteos)
    for c in final_counts.columns:
        if c not in ("Código", "Nombre"):
        # convertir valores numéricos; si hay valores no convertibles, rellenar con 0
            final_counts[c] = pd.to_numeric(final_counts[c], errors="coerce").fillna(0).astype(int)

    # actualizar final_1 con la tabla resultado para que se use al guardar
    final_2 = final_counts.copy()
    final_3 = pd.merge(
        final_2,
        estaciones_sin_ctc,
        on="Código",
        how="left",
    )
    final_3.drop(columns=["Nombre_y"], inplace=True)
    final_3.rename(columns={"Nombre_x":"Nombre"}, inplace=True)
    return final_3



def procesar_rm_k (fecha):
    fname = Path(r"C:\Users\xiangzhou.zhang\Documents\Data\graylog\RM")/f"{fecha.strftime('%Y-%m-%d')}_rm.csv"
    sitraproceso = SitraProcessor()
    log_rm = sitraproceso.readLogFile(fname)
    df_rm = sitraproceso.loadRealMovement(log_rm)
    conteo = df_rm.groupby(["Fuente","Código"]).size().reset_index(name="Conteo")
    K = conteo[conteo["Fuente"] == "STACrail"].copy()
    estaciones_con_ctc = loadEstaciones()
    estaciones_sin_ctc = loadEstacionSinCTC()
    estaciones = pd.concat([estaciones_con_ctc, estaciones_sin_ctc], ignore_index=True)
    K_count = pd.merge(
    K,
    estaciones[["Delegación","Código","Nombre"]],
    on="Código",
    how = "left"
    )
    K_count =K_count[["Delegación","Código","Nombre","Conteo"]] 
    return K_count





def eliminar_duplicado(estaciones):
    estaciones.drop_duplicates(subset=["Código","Nombre"],inplace=True)
    duplicados = estaciones[estaciones.duplicated('Código', keep=False)]
    estaciones = estaciones[~((estaciones['Código'].isin(duplicados['Código'])) & 
                          (estaciones['Nombre'].str.endswith('RAM')))]
    estaciones = estaciones[~((estaciones['Código'].isin(duplicados['Código'])) & 
                          (estaciones['Nombre'].str.endswith('AV')))]
    estaciones.drop_duplicates(subset=["Código"],keep="first",inplace=True)
    return estaciones


##################################################################################################
##INFORME DE Rotulaciones
###################################################################################################
def procesar_rotulacion(historico_pro,start_date):
    historico_pro = historico_pro[historico_pro["FuenteVía"] != 'SITRA_PROVIDED']
    print(historico_pro)
    aux_df = historico_pro[
        historico_pro["FechaOrigen"] == historico_pro["Fecha"].dt.date
    ].copy()

    aux_split = np.split(
        aux_df,
        np.where(
            (~aux_df["NTécnico"].eq(aux_df["NTécnico"].shift()))
            | (~aux_df["FechaOrigen"].eq(aux_df["FechaOrigen"].shift()))
        )[0][1:],
    )
    cols = [
        "FechaOrigen",
        "CTC",
        "NTécnico",
        "LíneaComercial",
        "Secuencia",
        "Código",
        "Nombre",
        "Movimiento",
        "CategoríaCirculación",
        # "Producto",
        "Empresa",
    ]
    origenes = []
    destinos = []
    continuaciones =  []
    ultimos = []
    destinos_noerronea= {"05485":"05482","51419":"51406","60913":"60914","05361":"15206","05485":"05482","72303":"B7173"}
    for df in aux_split:
        # Comprobamos las que no tienen origen correcto
        if not any(df["Secuencia"] == 1) and not (
            df.shape[0] == 1 and df["Movimiento"].iloc[0] == "PÉRDIDA_SEGUIMIENTO"
        ):
            # Si es una pérdida de seguimiento, la descartamos
            # continue
            # origenes.append(df)
            if not (df["Movimiento"] == "ORIGEN").any():
                df.sort_values(by=["Fecha","Secuencia"], inplace=True)
                origenes.append(df.iloc[0][cols])
        # Comprobamos que la circulación llegue a destino
        if any(
            (df["Movimiento"].isin(["FIN", "BAJA"])) & (df["Código"] == df["CódigoDestino"])
        ):
            # Nos quedamos con el destino
            df = df.reset_index(drop=True)
            destino = df[
                (df["Movimiento"].isin(["FIN", "BAJA"]))
                & (df["Código"] == df["CódigoDestino"])
            ].iloc[-1]
            # Si el último movimiento es la finalización, pasamos
            if df.shape[0] == destino.name + 1:
                continue
            continuacion = df.iloc[destino.name + 1 :]
            if any(
                np.invert(continuacion["Código"] == destino["Código"])
                & (continuacion["Secuencia"] == -1)
            ): 
                if any(continuacion["Movimiento"].isin(["MANIOBRA_ENTRADA", "MANIOBRA_SALIDA"])):
                    continuaciones.append(continuacion)
                    continuacion_maniobra = continuacion[
                        (continuacion["Movimiento"] != "MANIOBRA_APROXIMACION")& (continuacion["Secuencia"] == -1)
                    ]
                    if not continuacion_maniobra.empty:
                        ultimo = continuacion_maniobra.iloc[-1]
                        destino_codigo = destino["Código"]
                        ultimo_codigo = ultimo["Código"]
                        if destinos_noerronea.get(destino_codigo) != ultimo_codigo:
                            if ultimo["Código"] != destino["Código"]:
                                ultimos.append(ultimo)
                                destinos.append(destino)
    destinos = pd.DataFrame(destinos)
    # print(destinos)
    destinos = destinos[["FechaOrigen", "CTC", "NTécnico", "LíneaComercial","Secuencia", "Código", "Nombre","Movimiento","CategoríaCirculación","Producto","Empresa"]]
    destinos.sort_values(by=["Secuencia"], inplace=True)
    ultimos = pd.DataFrame(ultimos)
    ultimos.reset_index(drop=True, inplace=True)
    info_extra = ultimos[["NTécnico", "FechaOrigen", "Nombre", "Código"]]
    info_extra = info_extra.rename(columns={"Nombre": "ESTACIÓN HASTA LA QUE SIGUE ROTULADO", "Código":"CÓDIGO ESTACIÓN DESROTULAN"})
    rename_column = {"Secuencia": "SECUENCIA DONDE FINALIZA","Nombre": "ESTACIÓN EN LA QUE FINALIZA", "Código":"CÓDIGO ESTACIÓN FINALIZA"}
    destinos.rename(columns=rename_column, inplace=True)
    df_merge = pd.merge(
        destinos,
        info_extra,
        how="left",
        on = ["NTécnico", "FechaOrigen"]
    )
    df_merge.sort_values(by=["NTécnico"], inplace=True)
    estaciones=pd.read_csv("data/Subdirección.csv")
    origenes= pd.DataFrame(origenes)
    origenes = pd.merge(
        estaciones,
        origenes,
        how="right",
        on=["Código"]
    )
    estaciones.rename(columns={"Código":"CÓDIGO ESTACIÓN FINALIZA" },inplace=True)
    df_merge = pd.merge(
        df_merge,
        estaciones,
        how="left",
        on = ["CÓDIGO ESTACIÓN FINALIZA"]
    )
    origenes.rename(columns={"Subdirección":"Delegación"},inplace=True)
    origenes = pd.DataFrame(origenes)
    rename_column_origen = {"Secuencia":"Secuencia en la que se rotula"}
    origenes.rename(columns=rename_column_origen,inplace=True)
    origenes = origenes[['FechaOrigen','Delegación','CTC','NTécnico','LíneaComercial','Secuencia en la que se rotula','Código','Nombre','Movimiento','CategoríaCirculación','Empresa']]
    origenes.sort_values(by=["Nombre"], inplace=True)
    origenes.reset_index(drop=True, inplace=True)
    df_merge.rename(columns={"Subdirección":"Delegación"},inplace=True)
    df_merge=df_merge[['FechaOrigen','Delegación','CTC','NTécnico','LíneaComercial','Producto','SECUENCIA DONDE FINALIZA','CÓDIGO ESTACIÓN FINALIZA','ESTACIÓN EN LA QUE FINALIZA','ESTACIÓN HASTA LA QUE SIGUE ROTULADO','CÓDIGO ESTACIÓN DESROTULAN','Movimiento','CategoríaCirculación','Empresa']]
    df_merge.sort_values(by=["ESTACIÓN EN LA QUE FINALIZA"], inplace=True)
    df_merge.reset_index(drop=True, inplace=True)   
    planifi = getPlanificacionCirculacionesTecnicas(start_date)
    comercial = planifi[planifi["esComercial"]== True]
    comercial = comercial[["NTécnico","Fecha","esComercial"]].copy()
    comercial.rename(columns={"Fecha":"FechaOrigen"}, inplace=True)
    comercial["FechaOrigen"] = pd.to_datetime(comercial["FechaOrigen"]).dt.strftime('%Y-%m-%d')
    origenes["FechaOrigen"] = pd.to_datetime(comercial["FechaOrigen"]).dt.strftime('%Y-%m-%d')
    origenes = pd.merge(
        origenes,
        comercial,
        on=["FechaOrigen","NTécnico"],
        how="left"
    )
    origenes = origenes[origenes["esComercial"] == True]
    df_merge["FechaOrigen"] = pd.to_datetime(comercial["FechaOrigen"]).dt.strftime('%Y-%m-%d')
    df_merge = pd.merge(
        df_merge,
        comercial,
        on=["FechaOrigen","NTécnico"],
        how="left"
    )
    df_merge = df_merge[df_merge["esComercial"] == True]
    planificacion = getCirculacionesPlanificadas(start_date)
    planificacion["Secuencia"] = pd.to_numeric(planificacion["Secuencia"], errors="coerce")
    planificacion["FechaOrigen"] = pd.to_datetime(planificacion["FechaOrigen"], errors="coerce")
    idx_min = planificacion.groupby("NTécnico")["Secuencia"].idxmin().dropna()
    min_secuencia_por_tecnico = planificacion.loc[idx_min].reset_index(drop=True)
    min_secuencia_por_tecnico =min_secuencia_por_tecnico[["NTécnico","FechaOrigen","Secuencia","Código"]]
    estaciones = loadEstaciones()
    estaciones1 = loadEstacionSinCTC()
    estaciones = pd.concat([estaciones,estaciones1],ignore_index=True)
    estaciones = eliminar_duplicado(estaciones)
    estaciones = estaciones[["Código","Nombre"]]
    min_secuencia_por_tecnico = pd.merge(
    min_secuencia_por_tecnico,
    estaciones,
    how = "left",
    on = ["Código"]
    )
    min_secuencia_por_tecnico.rename(columns={"Código":"Código_origen","Nombre":"Nombre_origen"}, inplace=True)
    origenes["FechaOrigen"] = pd.to_datetime(origenes["FechaOrigen"], errors="coerce").dt.strftime("%Y-%m-%d")
    min_secuencia_por_tecnico["FechaOrigen"] = pd.to_datetime(min_secuencia_por_tecnico["FechaOrigen"], errors="coerce").dt.strftime("%Y-%m-%d")
    origenes= pd.merge(
        origenes,
        min_secuencia_por_tecnico,
        on=["NTécnico","FechaOrigen"],
        how="left",
    )
    origenes["Secuencia en la que se rotula"]  = origenes["Secuencia en la que se rotula"].astype("Int64")
    origenes["Nombre_origen"] = origenes["Nombre_origen"].str.upper()
    origenes["Secuencia en la que se rotula"] = (
        pd.to_numeric(origenes["Secuencia en la que se rotula"].replace("", pd.NA), errors="coerce")
        .fillna(0)
        .astype("Int64")
    )
    if {"Nombre","Código","Código_origen","Nombre_origen"}.issubset(origenes.columns):
        mask = origenes["Nombre"].isna() & (origenes["Código"] == origenes["Código_origen"])
        if mask.any():
            origenes.loc[mask, "Nombre"] = origenes.loc[mask, "Nombre_origen"]
    # no_errroneas_origen= {"77310":"77309","A7510":"78400","05571":["05534","05533"]}
    return origenes,df_merge
def add_header(canvas, doc,name):
    canvas.saveState()
    fecha_actual = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")

    # Logo
    try:
        logo_path = Path("data/logo.png")
        canvas.drawImage(str(logo_path), doc.leftMargin, doc.height + doc.topMargin - 0.75*inch,
                         width=2*inch, height=0.75*inch, preserveAspectRatio=True)
    except:
        canvas.setFont("Helvetica", 10)
        canvas.drawString(doc.leftMargin, doc.height + doc.topMargin - 0.5*inch, "[LOGO NO ENCONTRADO]")

    # Posición base del título
    center_x = doc.width / 2.0 + doc.leftMargin
    y = doc.height + doc.topMargin - 0.4*inch

    # Títulos centrados
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawCentredString(center_x, y, "INDICADORES CALIDAD MSE")
    canvas.drawCentredString(center_x, y - 12, name)
    canvas.drawCentredString(center_x, y - 24, f"Análisis de datos {fecha_actual}")

    # Fecha a la derecha
    canvas.setFont("Helvetica", 10)
    canvas.drawRightString(doc.width + doc.leftMargin, doc.height + doc.topMargin - 0.3*inch,
                           f"Fecha: {fecha_actual}")

    canvas.restoreState()
def add_footer(canvas, doc):
        canvas.saveState()
        
        # Obtener fecha actual
        fecha = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")
        styles = getSampleStyleSheet()
        
        # Configurar pie de página
        footer_text_left = "SD. de Sistemas y Medios Operacionales<br/>D. de Circulación y Gestión de Capacidad<br/>DG. de OPERACIONES Y EXPLOTACIÓN"
        footer_text_center = f"Página {doc.page}"
        footer_text_right = f"Fecha: {fecha}"
        footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=7,
        leading=10,
        spaceBefore=5,
        alignment=0  # Alineación izquierda
    )
        left_paragraph = Paragraph(footer_text_left, footer_style)
        left_paragraph.wrapOn(canvas, 3.5*inch, 0.5*inch)
        left_paragraph.drawOn(canvas, 0.5*inch, 0.3*inch)
        canvas.setFont("Helvetica", 9)
    
        
        # Centro (calculamos la posición central)
        text_width = canvas.stringWidth(footer_text_center, "Helvetica", 9)
        canvas.drawString((doc.width + doc.leftMargin + doc.rightMargin - text_width) / 2, 
                          0.5*inch, footer_text_center)
        
        # Derecha
        right_pos = doc.width + doc.leftMargin - canvas.stringWidth(footer_text_right, "Helvetica", 9) - 0.75*inch
        canvas.drawString(right_pos, 0.5*inch, footer_text_right)
        
        # Línea separadora
        canvas.setStrokeColor(colors.gray)
        canvas.setLineWidth(0.5)
        # canvas.line(0.75*inch, 0.7*inch, doc.width + doc.leftMargin - 0.75*inch, 0.7*inch)
        
        canvas.restoreState()
def add_header_footer(canvas, doc):
    add_header(canvas, doc)
    add_footer(canvas, doc)

def tabla_resumen(df, story, filas_por_pagina=30, col_widths=None, rotulacion = True):
    """
    Agrupa `df` por Código_origen, Nombre_origen, Código y Nombre (si existen),
    cuenta trenes por grupo y añade tablas paginadas a `story`.
    Renombra 'Código' -> 'codigo donde\nse rotula' y 'Nombre' -> 'nombre donde\nse rotula'.
    Añade título "Tabla resumen" y muestra la página de la tabla cuando ésta ocupa varias páginas.

    Devuelve True si la última página de la tabla contiene más de la mitad
    de las filas permitidas por página (filas_por_pagina), False en caso contrario.
    """
    if df is None or df.empty:
        return False

    # columnas objetivo para agrupar (si existen en df)
    if rotulacion == True:
        target_cols = [c for c in ("Código_origen", "Nombre_origen", "Código", "Nombre") if c in df.columns]
        if not target_cols:
            # fallback: usa las primeras hasta 4 columnas del df
            target_cols = list(df.columns[: min(4, len(df.columns))])

        # agrupación y conteo
        df_group = df.groupby(target_cols).size().reset_index(name="Nº trenes")

        # renombrar las columnas solicitadas, con salto de línea después de "donde"
        rename_map = {}
        if "Código" in df_group.columns:
            rename_map["Código"] = "Código donde\nse rotula"
        if "Nombre" in df_group.columns:
            rename_map["Nombre"] = "Nombre donde\nse rotula"
        if "Código_origen" in df_group.columns:
            rename_map["Código_origen"] = "Código\norigen"
        if "Nombre_origen" in df_group.columns:
            rename_map["Nombre_origen"] = "Nombre\norigen"
        if rename_map:
            df_group = df_group.rename(columns=rename_map)


        # garantizar columnas únicas para evitar solapamiento (manteniendo saltos de línea)
    else:
        target_cols = [c for c in ("CÓDIGO ESTACIÓN FINALIZA", "ESTACIÓN EN LA QUE FINALIZA", "CÓDIGO ESTACIÓN DESROTULAN","ESTACIÓN HASTA LA QUE SIGUE ROTULADO") if c in df.columns]
        if not target_cols:
            # fallback: usa las primeras hasta 4 columnas del df
            target_cols = list(df.columns[: min(4, len(df.columns))])

        # agrupación y conteo
        df_group = df.groupby(target_cols).size().reset_index(name="Nº trenes")

        # renombrar las columnas solicitadas, con salto de línea después de "donde"
        rename_map = {}
        if "CÓDIGO ESTACIÓN FINALIZA" in df_group.columns:
            rename_map["CÓDIGO ESTACIÓN FINALIZA"] = "CÓDIGO ESTACIÓN\nFINALIZA"
        if "ESTACIÓN EN LA QUE FINALIZA" in df_group.columns:
            rename_map["ESTACIÓN EN LA QUE FINALIZA"] = "ESTACIÓN EN LA QUE\nFINALIZA"
        if "CÓDIGO ESTACIÓN DESROTULAN" in df_group.columns:
            rename_map["CÓDIGO ESTACIÓN DESROTULAN"] = "CÓDIGO ESTACIÓN\nDESROTULAN"
        if "ESTACIÓN HASTA LA QUE SIGUE ROTULADO" in df_group.columns:
            rename_map["ESTACIÓN HASTA LA QUE SIGUE ROTULADO"] = "ESTACIÓN HASTA LA QUE\nSIGUE ROTULADO"
        if rename_map:
            df_group = df_group.rename(columns=rename_map)
    sec_col = None
    for c in df_group.columns:
        low = c.lower()
        if "codigo" in low and ("origen" in low or "finaliza" in low):
            sec_col = c
            break
    if "Nº trenes" in df_group.columns:
        if sec_col:
            df_group = df_group.sort_values(by=["Nº trenes", sec_col], ascending=[False, True]).reset_index(drop=True)
        else:
            df_group = df_group.sort_values(by="Nº trenes", ascending=False).reset_index(drop=True)
    cols = list(df_group.columns)
    seen = {}
    unique_cols = []
    for c in cols:
        if c not in seen:
            seen[c] = 0
            unique_cols.append(c)
        else:
            seen[c] += 1
            unique_cols.append(f"{c}_{seen[c]}")
    df_group.columns = unique_cols

    # preparar estilos para permitir wrapping en celdas
    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        alignment=1,  # centered header
        leading=11,
    )
    cell_style = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        wordWrap="CJK",  # permite wrap en palabras largas y respeta saltos de línea \n
    )

    # convertir a texto y envolver (Paragraph) para que ReportLab haga wrap automático
    df_clean = df_group.fillna("").astype(str).reset_index(drop=True)

    # dividir en partes para paginar
    partes = [df_clean.iloc[i : i + filas_por_pagina] for i in range(0, len(df_clean), filas_por_pagina)]
    total_partes = max(1, len(partes))

    estilo_tabla = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ]
    )

    title_style = ParagraphStyle("TableTitle", parent=styles["Heading3"], alignment=1, spaceAfter=6, fontSize=10)

    for i, parte in enumerate(partes):
        # Título con número de página de la tabla si hay varias partes
        pagina_tabla = i + 1
        if total_partes > 1:
            titulo = f"Tabla resumen — página {pagina_tabla} / {total_partes}"
        else:
            titulo = "Tabla resumen"
        story.append(Paragraph(titulo, title_style))

        # construir datos como Paragraphs para permitir wrap
        header_row = [Paragraph(h, header_style) for h in list(parte.columns)]
        data_rows = []
        for _, row in parte.iterrows():
            data_row = [Paragraph(cell.replace("\n", "<br/>"), cell_style) for cell in row.values]
            data_rows.append(data_row)

        datos = [header_row] + data_rows
        ncols = len(parte.columns)

        # calcular col_widths si no proporcionadas
        if col_widths is None:
            # Ancho aproximado por columna: distribuir la página útil entre columnas
            total_width = 6 * inch
            col_widths_calc = [total_width / ncols] * ncols
        else:
            col_widths_calc = col_widths if len(col_widths) == ncols else [col_widths[0]] * ncols

        tabla = Table(datos, repeatRows=1, colWidths=col_widths_calc)
        tabla.setStyle(estilo_tabla)

        story.append(tabla)

        # salto de página si no es la última parte
        if i < len(partes) - 1:
            story.append(PageBreak())
            story.append(Spacer(1, 70))

    # Determinar si la última parte ocupa más de la mitad de la página.
    # Consideramos solo el número de filas de datos (sin contar la cabecera).
    if partes:
        ultima_longitud = len(partes[-1])
        # Si la última parte tiene más de la mitad de filas_por_pagina, devolvemos True
        return ultima_longitud > (filas_por_pagina / 4)
    return False

def tabla_detalle(df, story, filas_por_pagina=30, col_widths=None,rotulacion = True):
    """
    Muestra una tabla detallada paginada con las columnas:
    NTécnico, Código_origen, Nombre_origen, Secuencia, codigo donde\nse rotula, nombre donde\nse rotula
    Ordenada por Código_origen cuando exista.
    """
    if df is None or df.empty:
        return

    # columnas deseadas en el orden solicitado (filtramos las que realmente existen)
    if rotulacion == True:
        desired = [
            "NTécnico",
            "Código_origen",
            "Nombre_origen",
            "Secuencia en la que se rotula",
            "Código", 
            "Nombre", 
        ]
    else:
        desired = [
            "NTécnico",
            "CÓDIGO ESTACIÓN FINALIZA",
            "ESTACIÓN EN LA QUE FINALIZA",
            "CÓDIGO ESTACIÓN DESROTULAN", 
            "ESTACIÓN HASTA LA QUE SIGUE ROTULADO"
        ]
    cols = [c for c in desired if c in df.columns]
    if not cols:
        # fallback: coger hasta 6 primeras columnas
        cols = list(df.columns[: min(6, len(df.columns))])

    # seleccionar y ordenar por Código_origen si existe
    df_sel = df[cols].copy()
    if "Código_origen" in df_sel.columns:
        df_sel = df_sel.sort_values(by=["Código_origen"]).reset_index(drop=True)
    else:
        df_sel = df_sel.reset_index(drop=True)

    # renombrar las columnas solicitadas (manteniendo saltos de línea)
    rename_map = {}
    if "Código" in df_sel.columns:
        rename_map["Código"] = "Código donde\nse rotula"
    if "Nombre" in df_sel.columns:
        rename_map["Nombre"] = "Nombre donde\nse rotula"
    if "Código_origen" in df_sel.columns:
        rename_map["Código_origen"] = "Código\norigen"
    if "Nombre_origen" in df_sel.columns:
        rename_map["Nombre_origen"] = "Nombre\norigen"
    if "NTécnico" in df_sel.columns:
        rename_map["NTécnico"] = "Nº Técnico"     
    if rename_map:
        df_sel = df_sel.rename(columns=rename_map)

    # preparar estilos para permitir wrapping en celdas
    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        alignment=1,  # centered header
        leading=11,
    )
    cell_style = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        wordWrap="CJK",
    )

    # convertir a texto para evitar problemas y reset index
    df_clean = df_sel.fillna("").astype(str).reset_index(drop=True)

    # paginar
    partes = [df_clean.iloc[i : i + filas_por_pagina] for i in range(0, len(df_clean), filas_por_pagina)]
    total_partes = max(1, len(partes))

    estilo_tabla = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ]
    )

    title_style = ParagraphStyle("TableTitle", parent=styles["Heading3"], alignment=1, spaceAfter=6, fontSize=10)

    for i, parte in enumerate(partes):
        pagina_tabla = i + 1
        if total_partes > 1:
            titulo = f"Tabla detalle — página {pagina_tabla} / {total_partes}"
        else:
            titulo = "Tabla detalle"
        story.append(Paragraph(titulo, title_style))

        # cabecera (reemplazando saltos de línea por <br/>)
        header_row = [Paragraph(h.replace("\n", "<br/>"), header_style) for h in list(parte.columns)]

        data_rows = []
        for _, row in parte.iterrows():
            data_row = [Paragraph(cell.replace("\n", "<br/>"), cell_style) for cell in row.values]
            data_rows.append(data_row)

        datos = [header_row] + data_rows
        ncols = len(parte.columns)

        # calcular anchos de columna
        if col_widths is None:
            total_width = 6 * inch
            col_widths_calc = [total_width / ncols] * ncols
        else:
            col_widths_calc = col_widths if len(col_widths) == ncols else [col_widths[0]] * ncols

        tabla = Table(datos, repeatRows=1, colWidths=col_widths_calc)
        tabla.setStyle(estilo_tabla)

        story.append(tabla)

        # salto de página si no es la última parte
        if i < len(partes) - 1:
            story.append(PageBreak())
            story.append(Spacer(1, 70))


def procesar_pdf_rotulacion(origenes,df_merge):
    dir1 = {"RC CENTRO":"SD CENTRO","RC NORTE":"SD NORTE","RC SUR":"SD SUR","RED DE ALTA VELOCIDAD (RAV)":"SD ALTA VELOCIDAD","RC ESTE":"SD ESTE","RC NOROESTE":"SD NOROESTE","RC NORESTE":"SD NORESTE"}
    origenes["Delegación"] = origenes["Delegación"].map(lambda x: dir1[x] if pd.notnull(x) and x in dir1 else x)
    df_merge["Delegación"] = df_merge["Delegación"].map(lambda x: dir1[x] if pd.notnull(x) and x in dir1 else x)
    info_path = Path(r"C:\Users\xiangzhou.zhang\Documents\TEST\Rotulaciones")
    info_path.mkdir(parents=True, exist_ok=True)
    figrot_path = info_path / "Total_Delegación_hoy.png"
    fig = crear_grafico_provincias(origenes,rotulacion=True)
    if fig is not None:
        pio.write_image(fig,figrot_path,width=1000,height=800,scale=2,format='png')
    info_path = Path(r"C:\Users\xiangzhou.zhang\Documents\TEST\Desrotulaciones")
    info_path.mkdir(parents=True, exist_ok=True)
    figdes_path  = info_path / "Total_Delegación_hoy.png"
    fig = crear_grafico_provincias(df_merge,rotulacion=False)
    fecha_formateada = fecha_ayer.strftime("%Y-%m-%d")
    carpeta_destino = r"C:\Users\xiangzhou.zhang\Documents\Data\Informe\Rotulacion"
    os.makedirs(carpeta_destino, exist_ok=True)
    filename = os.path.join(carpeta_destino, f"{fecha_formateada}_rotulaciones.pdf")
    doc = SimpleDocTemplate(
        filename,
        pagesize=A4,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=1 * inch,
        bottomMargin=1 * inch,
    )

    styles = getSampleStyleSheet()
    story = []

    fecha_ayer = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")

    story.append(Spacer(1, 70))
    # titulo = Paragraph("Análisis de Rotulaciones y Desrotulaciones " + fecha_ayer, titulo_style)
    # story.append(titulo)

    verde_oscuro = Color(0 / 255, 100 / 255, 0 / 255)
    verde_claro = Color(52 / 255, 207 / 255, 145 / 255)
    barra_contenido = [["ÍNDICE DE CONTENIDO"]]
    barra_descipción = [["DESCRIPCIÓN"]]
    estilo_indice = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), verde_oscuro),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 12),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOX", (0, 0), (-1, -1), 1, colors.black),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("LEFTPADDING", (0, 0), (-1, -1), 100),
            ("RIGHTPADDING", (0, 0), (-1, -1), 100),
        ]
    )
    contenido_style = ParagraphStyle("Contenido", parent=styles["Normal"], spaceAfter=12)
    # contenido_center = ParagraphStyle(
    #     'ContenidoCenter',
    #     parent=styles['Normal'],
    #     alignment=1,  # 1 = center
    #     spaceAfter=12
    # )
    descripcion = Table(barra_descipción, colWidths=[6 * inch])
    descripcion.setStyle(estilo_indice)
    story.append(descripcion)
    indice_contenido = Table(barra_contenido, colWidths=[6 * inch])
    indice_contenido.setStyle(estilo_indice)
    story.append(Spacer(1, 30))
    story.append(Paragraph(
    "El presente informe tiene como objetivo analizar las incidencias detectadas en los procesos de rotulación y desrotulación de trenes, "
    "específicamente aquellos que no se rotulan en su estación de origen o no se desrotulan en su estación de destino.",
    contenido_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph(
    "<b>1. Rotulaciones incorrectas en estaciones no origen</b>", 
    contenido_style))
    story.append(Paragraph(
    "Se presentan los casos en los que los trenes no son rotulados correctamente en su estación de origen. "
    "En primer lugar se ofrece una visión global que abarca todas las subdirecciones, seguida de un desglose detallado por cada una de ellas. "
    "Finalmente, se incluyen tablas con un mayor nivel de detalle, en las que se identifican las estaciones específicas donde se han detectado estas incidencias.",
    contenido_style))
    story.append(Spacer(1, 12))
    story.append(Paragraph(
    "<b>2. Desrotulaciones en estaciones que no son destino</b>", 
    contenido_style))
    story.append(Paragraph(
    "Se analizan los casos en los que las circulaciones son desrotuladas en estaciones distintas a su destino final. "
    "Al igual que en el caso de las rotulaciones, se proporciona una visión general por subdirección, seguida de un desglose específico por cada una de ellas, y por último se incluye una tabla detallada con información de cada circulación donde se ha identificado esta incidencia.",
    contenido_style))
    story.append(PageBreak())
    story.append(Spacer(1, 70))
    story.append(indice_contenido)
    story.append(Spacer(1,30))
    story.append(Paragraph("Análisis Global", contenido_style))
    story.append(Paragraph("Análisis de rotulación SD AV", contenido_style))
    story.append(Paragraph("Análisis de rotulación de vía SD CENTRO", contenido_style))
    story.append(Paragraph("Análisis de rotulación de vía SD ESTE", contenido_style))
    story.append(Paragraph("Análisis de rotulación de vía SD NORESTE", contenido_style))
    story.append(Paragraph("Análisis de rotulación de vía SD NOROESTE", contenido_style))
    story.append(Paragraph("Análisis de rotulación de vía SD NORTE", contenido_style))
    story.append(Paragraph("Análisis de rotulación de vía SD SUR", contenido_style))
    story.append(Paragraph("Análisis de desrotulación SD AV", contenido_style))
    story.append(Paragraph("Análisis de desrotulación de vía SD CENTRO", contenido_style))
    story.append(Paragraph("Análisis de desrotulación de vía SD ESTE", contenido_style))
    story.append(Paragraph("Análisis de desrotulación de vía SD NORESTE", contenido_style))
    story.append(Paragraph("Análisis de desrotulación de vía SD NOROESTE", contenido_style))
    story.append(Paragraph("Análisis de desrotulación de vía SD NORTE", contenido_style))
    story.append(Paragraph("Análisis de desrotulación de vía SD SUR", contenido_style))

    
    Indicador_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        fontName="Helvetica",
        fontSize=20,
        spaceAfter=20,
        alignment=1,
        textColor=colors.black,
    )
    story.append(PageBreak())
    # Indicadores = Paragraph("Indicadores", Indicador_style)
    story.append(Spacer(1,70))
    barra_indicadores = [["INDICADORES"]]
    indicador = Table(barra_indicadores, colWidths=[6 * inch])
    indicador.setStyle(estilo_indice)
    story.append(indicador)
    # story.append(Indicadores)
    story.append(Spacer(1, 30))
    story.append(
        Paragraph(
            "<b>Distribución por delegación:</b> se presentan gráficos que reflejan el número total de trenes, así como el porcentaje de trenes con errores de rotulación o desrotulación, desglosados por subdirección.",
            contenido_style,
        )
    )
    story.append(
        Paragraph(
            "<b>Tabla resumen:</b> se incluye una tabla consolidada que recoge el número total de trenes incorrectamente rotulados o desrotulados, clasificados según su estación de origen y destino.",
            contenido_style,
        )
    )
    story.append(
        Paragraph(
           "<b>Tabla de detalle:</b> se proporciona una tabla detallada que contiene el número técnico de los trenes identificados con errores de rotulación o desrotulación, junto con la secuencia específica en la que se produce la incidencia en cada caso.",
            contenido_style,
        )
    )

    # Rotulaciones: iterar sobre subdirecciones presentes en origenes y empezar nueva página al cambiar
    story.append(PageBreak())
    story.append(Spacer(1, 60))
    Rotulaciones = Paragraph("Rotulaciones erróneas", Indicador_style)
    story.append(Rotulaciones)
    story.append(Spacer(1, 50))
    barra_centro = [["Distribución total de errores de rotulación por delegación"]]
    centro = Table(barra_centro, colWidths=[6 * inch])
    centro.setStyle(estilo_indice)
    story.append(centro)
    story.append(Spacer(1, 40))
    story.append(Image(figrot_path, width=6 * inch, height=5 * inch))
    story.append(PageBreak())

    # obtener lista de subdirecciones desde origenes (si existe) en orden
    if "origenes" in globals() and not origenes.empty:
        direcciones_rot = sorted(origenes["Delegación"].dropna().unique().tolist())
    else:
        direcciones_rot = []

    for idx, direc in enumerate(direcciones_rot):
        # empezar siempre en página nueva para cada subdirección (excepto si es la primera y ya estamos en nueva página)
        if idx > 0:
            story.append(PageBreak())
        story.append(Spacer(1, 70))
        barra_sub = [[f"Subdirección: {direc}"]]
        tabla_sub = Table(barra_sub, colWidths=[6 * inch])
        tabla_sub.setStyle(estilo_indice)
        story.append(tabla_sub)
        story.append(Spacer(1, 12))

        df_dir = origenes[origenes["Delegación"] == direc] if "origenes" in globals() else pd.DataFrame()
        if not df_dir.empty:
            necesita_salto = tabla_resumen(df_dir, story, filas_por_pagina=16, col_widths=[1.0 * inch] * len(df_dir.columns), rotulacion=True)
            if necesita_salto:
                story.append(PageBreak())
                story.append(Spacer(1, 70))
            detalle_ocupa_pagina = tabla_detalle(df_dir, story, filas_por_pagina=15, col_widths=[1.0 * inch] * len(df_dir.columns), rotulacion=True)
            if detalle_ocupa_pagina:
                story.append(PageBreak())
                story.append(Spacer(1, 70))
        else:
            story.append(Paragraph("Sin datos de rotulaciones para esta subdirección.", contenido_style))

    # Desrotulaciones: similar, iterar por subdirecciones presentes en df_merge
    story.append(PageBreak())
    story.append(Spacer(1, 60))
    Desrotulaciones = Paragraph("Desrotulaciones erróneas", Indicador_style)
    story.append(Desrotulaciones)
    story.append(Spacer(1, 50))
    barra_centro = [["Distribución total de desrotulados por delegaciones"]]
    centro = Table(barra_centro, colWidths=[6 * inch])
    centro.setStyle(estilo_indice)
    story.append(centro)
    story.append(Spacer(1, 40))
    # usar la imagen de desrotulaciones si existe (figdes_path) si no, caer de nuevo a figrot_path
    try:
        story.append(Image(figdes_path, width=6 * inch, height=5 * inch))
    except Exception:
        story.append(Image(figrot_path, width=6 * inch, height=5 * inch))
    story.append(PageBreak())

    if "df_merge" in globals() and not df_merge.empty:
        direcciones_des = sorted(df_merge["Delegación"].dropna().unique().tolist())
    else:
        direcciones_des = []

    for idx, direc in enumerate(direcciones_des):
        if idx > 0:
            story.append(PageBreak())
        story.append(Spacer(1, 70))
        barra_sub = [[f"Subdirección: {direc}"]]
        tabla_sub = Table(barra_sub, colWidths=[6 * inch])
        tabla_sub.setStyle(estilo_indice)
        story.append(tabla_sub)
        story.append(Spacer(1, 12))

        df_dir = df_merge[df_merge["Delegación"] == direc] if "df_merge" in globals() else pd.DataFrame()
        if not df_dir.empty:
            # Aplicamos la misma lógica que en rotulaciones: si tabla_resumen ocupa > mitad, forzamos salto antes del detalle
            necesita_salto = tabla_resumen(df_dir, story, filas_por_pagina=10, col_widths=[1.0 * inch] * len(df_dir.columns), rotulacion=False)
            if necesita_salto:
                story.append(PageBreak())
                story.append(Spacer(1, 70))
            detalle_ocupa_pagina = tabla_detalle(df_dir, story, filas_por_pagina=15, col_widths=[1.0 * inch] * len(df_dir.columns), rotulacion=False)
            if detalle_ocupa_pagina:
                story.append(PageBreak())
                story.append(Spacer(1, 70))
        else:
            story.append(Paragraph("Sin datos de rotulaciones para esta subdirección.", contenido_style))

    doc.build(story, onFirstPage=add_header_footer, onLaterPages=add_header_footer)
    print(f"PDF con encabezado en tabla creado: {filename}")
    
    

def main():
    parser = argparse.ArgumentParser(description="Process a date range.")
    # parser.add_argument('-s', '--start', required=True, help='Start date in YYYY-MM-DD format.')
    # parser.add_argument('-e', '--end', required=True, help='End date in YYYY-MM-DD format.')
    # parser.add_argument('-f', '--file_dir', help = "Direcotrio de archivo Sitra")

    args = parser.parse_args()

    # start_date = parse_date(args.start)
    # end_date = parse_date(args.end)

    # directorio_Stira = Path(args.file_dir)
    # if directorio_Stira is None:
    #     print("Error: Debe especificar el directorio de archivo Sitra")
    #     return

    # if start_date > end_date:
    #     print("Error: Fecha de comienzo debe ser mayor que fecha final.")
    #     return
    start_date = pd.Timestamp(datetime.now().date() - timedelta(days=1))
    end_date = pd.Timestamp(datetime.now().date())
    print(f"Procesar conteo sin ctc de trenes de días{start_date.date()} to {end_date.date()}")
    estaciones =[]
    trenes = [rellenarId(el) for el in np.arange(100000)]
    df_logs = cargarHistorico(start_date, end_date, estaciones, trenes)
    df_logs = df_logs.sort_values(
    by=["FechaOrigen", "NTécnico", "Fecha", "mov_ord"]
    ).reset_index(drop=True)
    df_logs["Día"] = df_logs["Fecha"].dt.date
    df_logs["day_of_week"] = df_logs["Fecha"].dt.day_of_week
    df_logs["day_of_year"] = df_logs["Fecha"].dt.day_of_year
    df_logs["week_of_year"] = (df_logs["day_of_year"] / 7).astype(int)
    sin_ctc = conteo_sin_ctc(df_logs)
    fname = Path(r"C:\Users\xiangzhou.zhang\Documents\Data\Informe\conteo_sin_ctc") / f"{start_date.strftime('%Y-%m-%d')}_conteo_sin_ctc.xlsx"
    data = {
    "Informe_sin_ctc": sin_ctc
    }
    print(f"guardando  conteo sin ctc de trenes de días{start_date.date()} to {end_date.date()}")
    guardarExcelMulti(data, fname) 
    # print(f"Procesar realmovementde estaciones fuente k de día{start_date.date()} to {end_date.date()}")
    # conteo = procesar_rm_k(start_date)
    # print("RM para estaciones sin ctc procesado correctamene")
    # print(f"guardando  RM para estciones sin ctc de trenes de días{start_date.date()} to {end_date.date()}")
    # fname = Path(r"C:\Users\xiangzhou.zhang\Documents\Data\Informe\rm_sin_ctc") / f"{start_date.strftime('%Y-%m-%d')}_Conteo_K_STACrail.xlsx"
    # conteo["Código"] = conteo["Código"].astype(str)

    # data ={
    #     "Conteo": conteo,
    # }
    # guardarExcelMulti(data,fname) 
    # print("Procensado informe de rotulaciones")
    # origenes, fin = procesar_rotulacion(df_logs,start_date)
    # print("Rotulacion procesado correctamente")
    # print("Guardando excel de rotulación")
    # fname = Path(r"C:\Users\xiangzhou.zhang\Documents\Data\Informe\Rotulacion") / f"{start_date.strftime('%Y-%m-%d')}_rotulaciones_erroneas.xlsx"
    # data ={
    #     "trenes_no_rotulado_en_el_origen": origenes, "trenes_que_no_se_desrotulan": fin
    # }
    # guardarExcelMulti(data,fname)
    # print("Excel rotulación guardado con exito")
    # print("generando informe PDF de rotulación")
    # procesar_pdf_rotulacion()
    # print("procesado con exito")
    
    print("Finalizado con exito")
    
    
    
    # gct = getPlanificacionCirculacionesTecnicas (start_date.date())
    # pathSitra = Path(r"C:\Users\xiangzhou.zhang\Documents\Data\BI\Sitra") /f"Circulación_sitra_{start_date.date()}.csv"
    # sitra = pd.read_csv(pathSitra, encoding='utf-8')
    # print(f"procesando comparación de circulación de días{start_date.date()} to {end_date.date()}")
    # print("fichero Crculacion sitra a procesar:", pathSitra)
    # solo_gct,solo_sitra,totales = process_comparacion(gct,sitra)
    # guarda_comparacion(solo_gct,solo_sitra,totales)
    
    
    # print(f"procesando previsiones de trenes de días{start_date.date()} to {end_date.date()}")
    # directorio_Stira = Path(r"c:\Users\xiangzhou.zhang\Documents\Data\graylog\sitra") / str(directorio_Stira)
    # sitra_processor = SitraProcessor()
    # logs_rm  = sitra_processor.readLogFile(directorio_Stira)
    # df_rm = sitra_processor.loadRealMovement(logs_rm)
    # df_ru = sitra_processor.loadRUOperationRequest(logs_rm)
    # df_supresion = df_rm[df_rm["Movimiento"] == "SUPRESIÓN"].copy()
    # df_supresion.sort_values(by=["FechaOrigen", "NTécnico"], inplace=True)
    # df_supresion["HoraPlanificada"] = df_supresion["HoraPlanificada"].apply(
    #     lambda x: f"{str(int(x)).zfill(6)[:2]}:{str(int(x)).zfill(6)[2:4]}:{str(int(x)).zfill(6)[4:]}"
    # )
    # ProcesarPrevisiones(df_ru, df_supresion, df_logs)

if __name__ == "__main__":
    main()