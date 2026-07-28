import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.express import colors

from src.visualizacion.utils import setHoverInfo, setLayoutFormat

color24 = colors.qualitative.Dark24
color12 = colors.qualitative.Set3
planificacion = ["Planificación", "XPEC", "Planif"]


###
# Recorridos
###
def trazasRecorrido(
    df: pd.DataFrame,
    prod: str,
    color: str,
    name: str,
    width: int = 2,
    mode="lines+markers",
    fill: str = None,
    showlegend=True,
    g_title: str = None,
):
    marker_map = {
        "LLEGADA": "triangle-left",
        "SALIDA": "triangle-right",
        "APROXIMACIÓN": "diamond-wide",
        "FIN": "x",
    }
    df = df[df["Movimiento"].isin(list(marker_map.keys()))]
    hmin = df["x_position"].min()
    marker = df["Movimiento"].apply(marker_map.get)
    trace = go.Scatter(
        x=[hmin] + df["x_position"].tolist(),
        y=[None] + df["y_position"].tolist(),
        visible=True,
        mode=mode,
        line=dict(color=color, dash="dot", width=width),
        marker_color=color,
        # marker_symbol=marker,
        marker_symbol=["star-diamond"] + marker.tolist(),
        marker_size=7,
        hoverinfo="text",
        hovertext=[None] + df["hover_info"].tolist(),
        name=name,
        showlegend=showlegend,
        legendgroup=prod,
        legendgrouptitle={"text": g_title},
        fill=fill,
        fillcolor=color,
    )

    return trace


def crearTrazas(
    use_df_map: dict[tuple, pd.DataFrame],
    usar_media: bool,
    map_codigo_pos: dict,
):
    traces = []
    vis_all = []
    t_names = sorted(set([prod for (prod, d, nt) in sorted(use_df_map.keys())]))
    cmap_est = dict(zip(t_names, color24))
    map_vis = {k: [] for k in t_names}
    shown = dict([(prod, False) for prod in t_names])

    for (prod, d, nt), use_df in sorted(use_df_map.items()):
        hover_cols = ["Código", "Nombre", "Movimiento", "DistanciaTotal (km)"]
        if prod == "Media":
            if not usar_media:
                continue
            color = cmap_est.get(nt)
            hover_cols = ["Hora"] + hover_cols + ["Fuente"]
            use_df["Fuente"] = nt
            width = 2

            media = use_df.copy()
            media["Fecha"] = pd.to_datetime(media["mean"], unit="s")
            lower = use_df.copy()
            lower["Fecha"] = pd.to_datetime(lower["mean"] + lower["std"], unit="s")
            upper = use_df.copy()
            upper["Fecha"] = pd.to_datetime(upper["mean"] - upper["std"], unit="s")

            # Añadir trazas
            for n, df in [("Media", media), ("Superior", upper), ("Inferior", lower)]:
                # df["FechaOrigen"] = pd.to_datetime(df["FechaOrigen"]).dt.date
                df["Hora"] = df["Fecha"].apply(lambda x: x.round("1s")).dt.time
                df["x_position"] = df["Fecha"]
                df["y_position"] = df["Código"].apply(map_codigo_pos.get)
                df["hover_info"] = setHoverInfo(df, hover_cols)

                if n == "Media":
                    df["marker"] = "star"
                    mode = "lines+markers"
                    width = 1
                    t_color = color
                    tname = "Media"
                    fill = None
                    showlegend = True
                    df["Fuente"] = prod
                    hover_cols = hover_cols + ["Resumen"]
                else:
                    df["marker"] = False
                    mode = "lines"
                    width = 0.5
                    tname = "Envolvente"
                    t_color = (
                        "rgba("
                        + ",".join(
                            [str(int(color[s + 1 : s + 3], 16)) for s in (0, 2, 4)]
                            + ["0.2"]
                        )
                        + ")"
                    )
                    if n == "Inferior":
                        fill = "tonexty"
                        showlegend = True
                    else:
                        fill = None
                        showlegend = False

                if not shown[prod]:
                    g_title = prod
                    shown[prod] = True
                else:
                    g_title = None

                trace = trazasRecorrido(
                    df,
                    prod=f"{tname}_{nt}",
                    color=t_color,
                    name=f"{tname} {nt}",
                    width=width,
                    mode=mode,
                    fill=fill,
                    showlegend=showlegend,
                    g_title=g_title,
                )
                # Actualizamos las trazas de los botones
                traces.append(trace)
                vis_all.append(True)
                for k in map_vis.keys():
                    if k == prod:
                        map_vis[k].append(True)
                    else:
                        map_vis[k].append(False)
        else:
            # use_df = use_df[use_df["Movimiento"].isin(["LLEGADA", "SALIDA"])]
            color = cmap_est.get(prod)
            hover_cols = ["FechaOrigen", "NTécnico", "Fecha"] + hover_cols
            use_df["hover_info"] = setHoverInfo(use_df, hover_cols)
            if prod in planificacion and usar_media:
                # Solamente mostramos la planificación media
                continue
            elif prod == "Sitra":
                width = 1.7
            else:
                width = 3

            # Asignamos valores por tipo
            use_df["hover_info"] = setHoverInfo(use_df, hover_cols)
            if usar_media:
                use_df["x_position"] = use_df["FechaNorm"]
            else:
                use_df["x_position"] = use_df["Fecha"]
            use_df["y_position"] = use_df["Código"].apply(map_codigo_pos.get)
            if not shown[prod]:
                g_title = prod
                shown[prod] = True
            else:
                g_title = None
            trace = trazasRecorrido(
                use_df,
                prod=f"{prod}_{d}_{nt}",
                color=color,
                name=f"{nt} {d}",
                width=width,
                g_title=g_title,
            )
            # Actualizamos las trazas de los botones
            traces.append(trace)
            vis_all.append(True)
            for k in map_vis.keys():
                if k == prod:
                    map_vis[k].append(True)
                else:
                    map_vis[k].append(False)
    return traces, vis_all, map_vis


def mostrarMarchas(
    usar_media: bool,
    traces: list[go.Scatter],
    vis_all: list[bool],
    map_vis: dict[tuple, list[bool]],
    map_tick_text: dict[int, str],
    title: str = "Marcha Media por Trayecto",
):
    # Layout
    if usar_media:
        tickformat = "%H:%M:%S"
    else:
        tickformat = None
    layout = setLayoutFormat(
        groupclick="togglegroup",
        title=title,
    )
    layout.update(
        yaxis=dict(
            tickmode="array",
            tickvals=list(map_tick_text.keys()),
            ticktext=list(map_tick_text.values()),
            linecolor="black",
            fixedrange=False,
            showgrid=True,
            zeroline=False,
            title="Dependencia",
        ),
        xaxis=dict(
            # tickangle=-40,
            fixedrange=False,
            showgrid=False,
            zeroline=False,
            title="Tiempo",
            rangeslider=dict(
                visible=True,
                thickness=0.1,
            ),
            type="date",
            tickformat=tickformat,
        ),
    )
    fig = go.Figure(data=traces, layout=layout)

    # Añadimos los botones
    buttons = []
    for k, v in map_vis.items():
        button = dict(label=k, method="restyle", args=["visible", v])
        buttons.append(button)
    buttons = [
        {
            "label": "Todo",
            "method": "restyle",
            "args": ["visible", vis_all],
        }
    ] + buttons
    fig.update_layout(
        updatemenus=[
            dict(
                type="dropdown",
                direction="down",
                buttons=buttons,
                # pad={"b": 10, "t": 10},
                showactive=True,
                # x=-0.4,
                xanchor="left",
                y=1.4,
                yanchor="top",
            )
        ],
    )
    return fig


###
# Velocidades
###
def trazasVelocidad(df: pd.DataFrame, prod: str, color: str, name: str, width: int = 2):
    trace = go.Scatter(
        x=df["x_position"].tolist(),
        y=df["y_position"].tolist(),
        visible=True,
        mode="lines+markers",
        line=dict(color=color, dash="dot", width=width),
        marker_color=color,
        # marker_symbol=marker,
        marker_symbol=df["marker"].tolist(),
        marker_size=7,
        hoverinfo="text",
        hovertext=df["hover_info"].tolist(),
        name=name,
        showlegend=True,
        legendgroup=prod,
        legendgrouptitle={"text": prod},
    )
    return trace


def mostrarVelocidades(
    use_df_map: dict[tuple, pd.DataFrame],
    map_tick_text: dict[int, str],
    title: str = "Velocidad Media por Trayecto",
):
    trace_markers = [
        "star-diamond",
        "square",
        "circle",
        "star",
    ]

    traces = []
    t_names = sorted(set([prod for (prod, d, nt) in sorted(use_df_map.keys())]))
    cmap_est = dict(zip(t_names, color24))
    mmap_est = dict(zip(t_names, trace_markers))
    max_vel = []

    for (prod, _, nt), df_rep in sorted(use_df_map.items()):
        if not prod == "Media":
            continue
        media = df_rep.copy()
        color = cmap_est.get(nt)
        marker = mmap_est.get(nt)
        hover_cols = [
            "Fuente",
            "FechaOrigen",
            "NTécnico",
            "Hora",
            "Código",
            "Nombre",
            "Movimiento",
            "DistanciaTotal (km)",
            "VelocidadMedia (km/h)",
        ]
        media["hover_info"] = setHoverInfo(media, hover_cols)
        media["marker"] = marker
        # media["x_position"] = media["Código"].apply(map_codigo_tick.get)
        media["x_position"] = media["pos"]
        media["y_position"] = media["VelocidadMedia (km/h)"]
        max_vel.append(media["VelocidadMedia (km/h)"].max())
        # display(media.head())

        if nt in planificacion:
            width = 2
        elif nt == "Sitra":
            width = 1.7
        else:
            width = 3
        trace = trazasVelocidad(
            media, prod="", color=color, name=f"Media {nt}", width=width
        )
        # Actualizamos las trazas
        traces.append(trace)

    max_vel = np.round(np.array(max_vel) / 5) * 5 + 5

    # Layout
    layout = setLayoutFormat(groupclick="toggleitem", title=title)
    layout.update(
        yaxis=dict(
            linecolor="black",
            fixedrange=False,
            showgrid=True,
            zeroline=False,
            title="Velocidad (km/h)",
            range=(0, max_vel),
        ),
        xaxis=dict(
            tickmode="array",
            tickvals=list(map_tick_text.keys()),
            ticktext=list(map_tick_text.values()),
            fixedrange=False,
            showgrid=False,
            zeroline=False,
            title="Estación",
            # autorange=True,
        ),
    )
    fig = go.Figure(data=traces, layout=layout)
    return fig
