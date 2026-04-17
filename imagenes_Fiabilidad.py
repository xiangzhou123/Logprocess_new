# generar_imagenes.py
import sys
sys.path.insert(0, r"C:\Users\xiangzhou.zhang\Documents\Codigo\LogProcess")

import warnings
import re
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime, timedelta
from plotly.express import colors

from src.utils import getFilesByDate, dateFromText, guardarExcel, isEmpty, rellenarId, sortStrNumbers
from src.visualizacion.visualizaciones import mostrarConfusionSankey, mostrarConfusionTree, setHoverInfo, setLayout, build_hierarchical_dataframe
from src.utils.util import range_normalization, sortElements
from src.visualizacion.color_maps import sample_random_colors

color24 = colors.qualitative.Dark24

# ── Recibe argumentos: ruta_pickle ──────────────────────────────────────────
import pickle, argparse

parser = argparse.ArgumentParser()
parser.add_argument("--data", required=True, help="Ruta al fichero pickle con los datos")
parser.add_argument("--output", required=True, help="Ruta base de salida de imágenes")
args = parser.parse_args()

with open(args.data, "rb") as f:
    datos = pickle.load(f)

df_confusion             = datos["df_confusion"]
df_estaciones            = datos["df_estaciones"]
planificacion_region     = datos["planificacion_region"]
planificacion_estaciones = datos["planificacion_estaciones"]
planificacion_region_semanal = datos["planificacion_region_semanal"]
fecha_ini                = datos["fecha_ini"]
fecha_fin                = datos["fecha_fin"]
provincias_out           = datos["provincias"]   # se rellenará y se devolverá

# ── Funciones ────────────────────────────────────────────────────────────────

def seguimiento_semanal(df: pd.DataFrame, est: str):
    grouped = (
        df.groupby(['Fecha Origen', 'Vía Real'])[['Coincide', 'NoCoincide']]
        .sum().reset_index()
    )
    grouped['Fecha Origen'] = pd.to_datetime(grouped['Fecha Origen'])
    vias_unicas_str = grouped['Vía Real'].astype(str).unique().tolist()

    def nat_key(s):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]

    orden_vias   = sorted(vias_unicas_str, key=nat_key)
    fechas_unicas = grouped['Fecha Origen'].drop_duplicates().sort_values()
    full_idx = pd.MultiIndex.from_product([fechas_unicas, orden_vias], names=['Fecha Origen', 'Vía Real'])
    grouped = (
        grouped.assign(**{'Vía Real': grouped['Vía Real'].astype(str)})
        .set_index(['Fecha Origen', 'Vía Real'])
        .reindex(full_idx, fill_value=0).reset_index()
    )
    map_via_orden = {v: i for i, v in enumerate(orden_vias)}
    grouped['__via_order__'] = grouped['Vía Real'].map(map_via_orden).fillna(len(orden_vias))
    grouped = grouped.sort_values(['Fecha Origen', '__via_order__']).drop(columns='__via_order__')
    grouped['fecha_key']   = grouped['Fecha Origen'].dt.strftime('%Y-%m-%d')
    grouped['fecha_label'] = grouped['Fecha Origen'].dt.strftime('%d-%m')
    grouped['via_str']     = grouped['Vía Real'].astype(str)
    orden_fechas = fechas_unicas.dt.strftime('%Y-%m-%d').tolist()
    grouped['fecha_key'] = pd.Categorical(grouped['fecha_key'], categories=orden_fechas, ordered=True)
    grouped['via_str']   = pd.Categorical(grouped['via_str'],   categories=orden_vias,  ordered=True)
    x_multi    = [grouped['fecha_key'], grouped['via_str']]
    customdata = np.stack([grouped['fecha_label'].astype(str), grouped['via_str'].astype(str)], axis=-1)

    fig = go.Figure()
    fig.add_bar(name='Coincide',    x=x_multi, y=grouped['Coincide'],
                marker_color='#2E8B57', text=grouped['Coincide'].apply(lambda v: f'{v}' if v > 0 else ''),
                textposition='inside', textfont=dict(size=9, color='white'),
                customdata=customdata,
                hovertemplate='Fecha: %{customdata[0]}<br>%{customdata[1]}<br>Coincide: %{y}<extra></extra>')
    fig.add_bar(name='No Coincide', x=x_multi, y=grouped['NoCoincide'],
                marker_color='#CD5C5C', text=grouped['NoCoincide'].apply(lambda v: f'{v}' if v > 0 else ''),
                textposition='inside', textfont=dict(size=9, color='white'),
                customdata=customdata,
                hovertemplate='Fecha: %{customdata[0]}<br>%{customdata[1]}<br>No Coincide: %{y}<extra></extra>')
    fig.update_layout(
        barmode='stack',
        title={'text': f'Seguimiento semanal coincidencia vía — {est}', 'x': 0.5, 'font': {'size': 20}},
        xaxis_title='Fecha  -  Vía real', yaxis_title='Nº de trenes',
        xaxis=dict(type='multicategory', categoryorder='array', categoryarray=orden_fechas,
                   tickangle=0, tickfont=dict(size=10), showgrid=True, gridwidth=1, gridcolor='lightgray'),
        yaxis=dict(showgrid=True, gridwidth=1, gridcolor='lightgray', zeroline=True,
                   zerolinewidth=1, zerolinecolor='gray', tickfont=dict(size=11)),
        height=700, width=1200, margin=dict(b=140, l=80, r=180, t=80),
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02, font=dict(size=10)),
        plot_bgcolor='white', paper_bgcolor='white',
        bargap=0.25, bargroupgap=0.08,
        uniformtext_minsize=8, uniformtext_mode='hide',
        font=dict(family="DejaVu Sans")
    )
    return fig

# ── Bucle principal ──────────────────────────────────────────────────────────
info_path = Path(args.output)

for sd in planificacion_estaciones["Subdirección"].unique():
    riv_path = info_path / str(sd)
    riv_path.mkdir(exist_ok=True, parents=True)

    df_sd = df_estaciones[df_estaciones["Subdirección"] == sd].sort_values(by=["Porcentaje_Total"], ascending=True)

    for cod, est in df_sd[["Código", "Estación"]][:5].values:
        provincias_out[sd].add(est)

        aux_df = df_confusion[
            (df_confusion["Código"] == cod) &
            (df_confusion["Estación"] == est) &
            (df_confusion["Subdirección"] == sd)
        ].copy()

        fig = mostrarConfusionSankey(aux_df, f"Planificación Vías {est} (SD {sd}) {fecha_ini}",
                                     "Vía Teórica", "Vía Real", "size")
        fig.write_image(str(riv_path / f"{est}_hoy.png"), format="png", width=1200, height=700, scale=2)
        print(f"  ✅ {est}_hoy.png")

        semanal = planificacion_region_semanal[
            (planificacion_region_semanal["Estación"] == est) &
            (planificacion_region_semanal["Subdirección"] == sd)
        ].copy()
        semanal['Coincide']   = semanal.apply(lambda r: r['Total'] if r['Vía Real'] == r['Vía Teórica'] else 0, axis=1)
        semanal['NoCoincide'] = semanal.apply(lambda r: r['Total'] if r['Vía Real'] != r['Vía Teórica'] else 0, axis=1)
        coincide_dia    = semanal.groupby(["Fecha Origen","Vía Real"])['Coincide'].sum().reset_index()
        nocoincide_dia  = semanal.groupby(["Fecha Origen","Vía Real"])['NoCoincide'].sum().reset_index()
        final = pd.merge(coincide_dia, nocoincide_dia, how="left", on=["Fecha Origen","Vía Real"])

        fig1 = seguimiento_semanal(final, est)
        fig1.write_image(str(riv_path / f"{est}_semanal.png"), format="png", width=1200, height=700, scale=2)
        print(f"  ✅ {est}_semanal.png")

# Guardar provincias actualizadas
with open(args.data.replace(".pkl", "_provincias.pkl"), "wb") as f:
    pickle.dump(provincias_out, f)

print("✅ Todas las imágenes generadas correctamente")