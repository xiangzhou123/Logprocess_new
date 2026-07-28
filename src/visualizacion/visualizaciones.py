from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import regex

from src.utils import (
    formatTimedelta,
    isValidCode,
    range_normalization,
    rellenarId,
    setEF,
    sortElements,
    sortStrNumbers,
)

from .color_maps import (
    color_sorter,
    map_color_anticipacion,
    map_color_EF,
    map_color_ocupacion,
    map_color_saturacion,
    map_shape,
    sample_random_colors,
    set_color_anticipacion,
    set_color_ocupacion,
    set_color_saturacion,
    set_name,
    set_shape,
    shape_sorter,
)
from .utils import named_color_to_rgba

pd.set_option("future.no_silent_downcasting", True)


BGCOLOR = "#F6F6F6"


###
# INFO GENERAL
###


def setHoverInfo(df: pd.DataFrame, hover_cols: list[str]):
    hover_cols = [c for c in hover_cols if c in df.columns]
    # Máxima longitud de nombre de columnas
    c_len = max([len(c) for c in hover_cols]) + 2
    # Fijamos la longitud máxima del valor, si se supera, hay un salto de línea
    fd_len = min(
        df[hover_cols]
        .fillna("")
        .map(lambda x: len(f"{x}"), na_action="ignore")
        .max()
        .max(),
        25,
    )

    def fitLen(value, sep):
        """
        Ajusta el tamaño del recuadro que muestra la info
        """
        if isinstance(value, list):
            row_sep = []  # lista de filas
            split = []  # elementos de cada fila
            for el in value:
                # Si la concatenación es menor que fd_len lo unimos
                if len(regex.sub("<.+?>", "", sep.join(split + [el]))) < fd_len:
                    split.append(el)
                else:
                    # sino, añadimos la fila y creamos una nueva
                    if split:
                        row_sep.append(sep.join(split))
                    split = [el]
            row_sep.append(sep.join([f"{el.strip()}" for el in split]))
            fit = []
            # Para cada fila, rellenamos el "vacío" a su izquierda para que quede justificado a la derecha
            for i, el in enumerate(row_sep):
                # El título de la columna va en la primera fila
                if i:
                    fit.append(
                        f"<b>{'':<{c_len}}</b>{sep+el:>{fd_len+len(''.join(regex.findall('<.+?>', el)))}}"
                    )
                else:
                    fit.append(
                        f"{el:>{fd_len+len(''.join(regex.findall('<.+?>', el)))}}"
                    )
            return "<br>".join(fit)
        elif isinstance(value, str):
            # Separamos por palabras
            if "→" in value:
                fit = fitLen(regex.split(r"→+", value), sep="→")
            else:
                fit = fitLen(regex.split(r"\s+", value), sep=" ")
            # fit = regex.sub(r"(?<=<br>.+)\s(?=\w)", "→", fit.replace(",", "→"))
            return fit
        return (
            f"{str(value):>{fd_len+len(''.join(regex.findall('<.+?>', str(value))))}}"
        )

    hov_info = df.fillna("").apply(
        lambda row: "<br>".join(
            [f"<b>{c:<{c_len}}</b>{fitLen(row[c], sep=' ')}" for c in hover_cols]
        ),
        axis=1,
    )
    if hov_info.empty:
        return None
    return hov_info


def setLayoutFormat(groupclick: str = "togglegroup", title: str = ""):
    """
    groupclick: {"togglegroup", "toggleitem"}
    """
    layout = go.Layout(
        title=title,
        modebar=dict(add="v1hovermode"),
        # Leyenda
        showlegend=True,
        legend=dict(
            groupclick=groupclick,
            yanchor="middle",
            y=0.5,
        ),
        # Hover
        hoverlabel=dict(bgcolor="white", font_size=16, font_family="consolas"),
        hoverdistance=20,
        paper_bgcolor=BGCOLOR,
        plot_bgcolor=BGCOLOR,
    )
    return layout


def setLayout(
    groupclick: str = "togglegroup",
    title: str = "",
    category_map: dict = dict(),
    x_range: tuple = None,
    x_axis_rangeslider: bool = True,
):
    """
    groupclick: {"togglegroup", "toggleitem"}
    """
    if category_map:
        tickmode = "array"
        tickvals = list(category_map.values())
        ticktext = list(category_map.keys())
    else:
        tickmode = "auto"
        tickvals = None
        ticktext = None
    if x_range:
        xautorange = False
    else:
        xautorange = True

    if x_axis_rangeslider:
        xaxis = dict(
            rangeslider=dict(
                visible=x_axis_rangeslider,
                thickness=0.1,
            ),
            type="date",
            range=x_range,
            autorange=xautorange,
            fixedrange=False,
        )
    else:
        xaxis = dict(type="category")
    layout = setLayoutFormat(groupclick, title)
    layout.update(
        # Ejes
        yaxis=dict(
            # categoryorder="array",
            # categoryarray=categoryarray,
            tickmode=tickmode,
            tickvals=tickvals,
            ticktext=ticktext,
            autorange=True,
            fixedrange=False,
        ),
        xaxis=xaxis,
    )
    return layout


def getSortedPlatforms(vias: list[str], cod: str):
    # Separamos AM de convencional
    am = sortStrNumbers([v for v in vias if "AM" in v])
    rc = sortStrNumbers([v for v in vias if "AM" not in v])
    sorted_platforms = rc + am
    if cod == "17000":
        sorted_platforms = [
            v
            for v in (
                ["1", "2", "3", "4", "5", "6", "7"]
                + ["8", "9B", "9", "10", "10B", "11", "12", "13"]
                + ["14", "15", "16", "17A", "17B", "18A", "18B"]
                + ["19A", "19B", "20", "21", "22A", "22B"]
                + ["23A", "23B", "24A", "24B", "25A", "25B"]
            )
            if v in sorted_platforms
            if v
        ]
    platform_map = {v: i for i, v in enumerate(sorted_platforms)}
    return platform_map


###
# OCUPACIONES
###


def addRectTrace(
    inicio,
    fin,
    y_inicio,
    y_fin,
    dy,
    mode,
    color,
    name,
    hover_info,
    showlegend: bool = True,
    opacity: float = 1,
    width: float = 0,
    dash=None,
):
    if mode == "markers":
        x = (inicio or fin,)
        y = (y_inicio or y_fin,)
        fill = None
    else:
        x = (inicio, inicio, fin, fin, inicio)
        y = (y_inicio + dy, y_inicio - dy, y_fin - dy, y_fin + dy, y_inicio + dy)
        fill = "toself"

    trace = go.Scatter(
        x=x,
        y=y,
        mode=mode,
        line=dict(color=color, width=width, dash=dash),
        visible=True,
        fill=fill,
        fillcolor=color,
        hoverinfo="text",
        hovertext=hover_info,
        # hoveron="points+fills",
        hoveron="points",
        textfont=dict(family="calibri", size=18),
        name=name,
        showlegend=showlegend,
        legendgroup=name,
        opacity=opacity,
    )
    return trace


def incluirOcupaciones(df: pd.DataFrame, platform_map: dict, criterio: str = "EF"):
    if df.empty:
        return []

    df_rep = (
        df.copy()
        # .sort_values(by=["LlegadaPlanificada"])
        .reset_index(drop=True)
    )

    # Definimos el color
    if criterio == "EF":
        map_color = map_color_EF
        if "EF" not in df_rep.columns:
            df_rep["EF"] = df_rep["Producto"].apply(setEF)
        df_rep["color"] = df_rep["EF"].apply(
            lambda x: set_color_ocupacion(x, criterio=criterio)
        )
        # Si el movimiento es incorrecto, independientemente de EF, lo marcamos como tal
        df_rep.loc[df_rep["Movimiento"] == "INCORRECTO", "color"] = set_color_ocupacion(
            "INCORRECTO", criterio=criterio
        )
    elif criterio == "tiempo":
        map_color = map_color_ocupacion
        df_rep["color"] = df_rep["Ocupación (segundos)"].apply(
            lambda x: (
                set_color_ocupacion(x, criterio=criterio)
                if pd.notna(x)
                else set_color_ocupacion(0, criterio=criterio)
            )
        )

    # Info para mostrar
    df_rep[["HoraInicioOcupación", "HoraFinOcupación"]] = df_rep[
        ["InicioOcupación", "FinOcupación"]
    ].map(lambda x: x.strftime("%H:%M:%S") if pd.notna(x) else "")
    hover_cols = [
        "T1",
        "T2",
        "T_seq",
        "Mov_seq",
        "EF",
        "Producto",
        "Movimiento",
        "HoraInicioOcupación",
        "HoraFinOcupación",
        "Ocupación",
        "Vía",
        "TipoVía",
    ]
    df_rep["hover_info"] = setHoverInfo(df_rep, hover_cols)

    # Ordenamos las vías
    df_rep["Vía_order"] = df_rep["Vía"].apply(platform_map.get)

    traces = []
    used_names = []
    for c in sorted(df_rep["color"].unique(), key=color_sorter.get):
        df_aux = df_rep[df_rep["color"] == c]
        name = set_name(color=c, map_color=map_color)

        trace = addRectTrace(
            inicio=(None,),
            fin=(None,),
            y_inicio=0,
            y_fin=0,
            dy=0,
            mode="lines",
            color=c,
            name=name,
            hover_info=None,
            showlegend=True,
            opacity=1,
            width=0,
        )
        if name not in used_names:
            used_names.append(name)
        traces.append(trace)

        for _, row in df_aux.sort_values(by=["InicioOcupación"]).iterrows():
            if row["Vía"] not in platform_map:
                continue
            if row["Ocupación (segundos)"] < 5:
                inicio = row["InicioOcupación"] or row["FinOcupación"]
                fin = row["InicioOcupación"] or row["FinOcupación"]
                opacity = 1
                dy = 0
                mode = "markers"
                width = 0
            else:
                inicio = row["InicioOcupación"]
                fin = row["FinOcupación"]
                dy = 0.15
                opacity = 1
                mode = "lines"
                width = 1

            trace = addRectTrace(
                inicio=inicio,
                fin=fin,
                y_inicio=row["Vía_order"],
                y_fin=row["Vía_order"],
                dy=dy,
                mode=mode,
                color=c,
                name=name,
                hover_info=row["hover_info"],
                showlegend=True if name not in used_names else False,
                opacity=opacity,
                width=width,
            )
            traces.append(trace)
            if name not in used_names:
                used_names.append(name)
    return traces


def incluirCambioVia(df: pd.DataFrame, platform_map: dict, criterio: str = "EF"):
    if df.empty:
        return []

    df_rep = (
        df.copy()
        # .sort_values(by=["LlegadaPlanificada"])
        .reset_index(drop=True)
    )

    # Definimos el color
    map_color = map_color_EF
    if "EF" not in df_rep.columns:
        df_rep["EF"] = df_rep[["ProductoT1", "ProductoT2"]].apply(
            lambda x: setEF(
                "".join([el if el and pd.notna(el) else "" for el in x]),
            ),
            axis=1,
        )
    df_rep["color"] = df_rep["EF"].apply(
        lambda x: set_color_ocupacion(x, criterio=criterio)
    )

    # Ordenamos las vías
    df_rep["Vía_order"] = df_rep["Vía"].apply(platform_map.get)

    traces = []
    used_names = []
    for c in sorted(df_rep["color"].unique(), key=color_sorter.get):
        df_aux = df_rep[df_rep["color"] == c]
        name = set_name(color=c, map_color=map_color)
        for _, row in df_aux.sort_values(by=["InicioOcupación"]).iterrows():
            if row["Vía"] not in platform_map:
                continue
            # La ocupación empieza en el fin de esta vía y termina en el inicio de la siguiente
            inicio = row["FinOcupación"]
            fin = row["cambio_vía"]["Fecha"]
            y_fin = platform_map.get(row["cambio_vía"]["Vía"])
            dy = 0.15
            opacity = 0.75
            mode = "lines"
            width = 1

            trace = addRectTrace(
                inicio=inicio,
                fin=fin,
                y_inicio=row["Vía_order"],
                y_fin=y_fin,
                dy=dy,
                mode=mode,
                color=c,
                name=name,
                hover_info=None,
                showlegend=False,
                opacity=opacity,
                width=width,
            )
            traces.append(trace)
            if name not in used_names:
                used_names.append(name)
    return traces


def incluirLibre(df: pd.DataFrame, platform_map: dict, margen=10, t_min=10):
    """
    margen: int
        Tiempo de seguridad mínimo (en minutos) entre ocupaciones.
    t_min: int
        Duración mínima de ocupación (en minutos).
    """
    if df.empty:
        return []

    df_rep = df.copy()

    # Info para mostrar
    hover_cols_free = [
        "Vía",
        "TipoVía",
        "HoraInicioLibre",
        "HoraFinLibre",
    ]
    df_rep["hover_info"] = setHoverInfo(df_rep, hover_cols_free)

    # Ordenamos las vías
    df_rep["Vía_order"] = df_rep["Vía"].apply(platform_map.get)

    opacity = 1
    width = 1
    mode = "lines"
    name = f"Libre (>{t_min} min)"

    traces = []
    for i, row in df_rep.iterrows():
        if row["Vía"] not in platform_map:
            continue
        trace = addRectTrace(
            inicio=row["InicioLibre"],
            fin=row["FinLibre"],
            y_inicio=row["Vía_order"],
            y_fin=row["Vía_order"],
            dy=0.35,
            mode=mode,
            color=named_color_to_rgba("silver", 0.35),
            name=name,
            hover_info=row["hover_info"],
            showlegend=True if not i else False,
            opacity=opacity,
            width=width,
        )
        traces.append(trace)
    return traces


def incluirFallos(df: pd.DataFrame, platform_map: dict):
    if df.empty:
        return []

    df_rep = (
        df.copy()
        # .sort_values(by=["InicioOcupación"])
        .reset_index(drop=True)
    )

    # Info para mostrar
    df_rep[["HoraInicioOcupación", "HoraFinOcupación"]] = df_rep[
        ["InicioOcupación", "FinOcupación"]
    ].map(lambda x: x.strftime("%H:%M:%S") if pd.notna(x) else "")
    hover_cols = [
        "T1",
        "T2",
        "T_seq",
        "Movimiento",
        "HoraInicioOcupación",
        "HoraFinOcupación",
        "Ocupación",
        "Vía",
        "TipoVía",
    ]
    df_rep["hover_info"] = setHoverInfo(df_rep, hover_cols)

    # Ordenamos las vías
    df_rep["Vía_order"] = df_rep["Vía"].apply(platform_map.get)

    name = "Fallo"
    color = "RoyalBlue"
    opacity = 0.5
    name = "Fallo"
    dy = 0.05
    width = 1
    mode = "lines"

    traces = []
    for i, row in df_rep.iterrows():
        if row["Vía"] not in platform_map:
            continue
        inicio = row["InicioOcupación"]
        fin = row["FinOcupación"]
        y = row["Vía_order"]
        trace = addRectTrace(
            inicio=inicio,
            fin=fin,
            y_inicio=row["Vía_order"],
            y_fin=row["Vía_order"],
            dy=dy,
            mode=mode,
            color=color,
            name=name,
            hover_info=row["hover_info"],
            showlegend=True if not i else False,
            opacity=opacity,
            width=width,
        )
        traces.append(trace)

    return traces


def incluirPlanificacion(df: pd.DataFrame, platform_map: dict, criterio: str = "EF"):
    if df.empty:
        return []

    if "OcupaciónPlanificada" not in df.columns or "VíaPlanificada" not in df.columns:
        return []

    df_rep = (
        df.copy()
        # .sort_values(by=["LlegadaPlanificada"])
        .reset_index(drop=True)
    )

    # Definimos el color
    if criterio == "EF":
        map_color = map_color_EF
        if "EF" not in df_rep.columns:
            df_rep["EF"] = df_rep[["ProductoT1", "ProductoT2"]].apply(
                lambda x: setEF(
                    "".join([el if el and pd.notna(el) else "" for el in x]),
                ),
                axis=1,
            )
        df_rep["color"] = df_rep["EF"].apply(
            lambda x: set_color_ocupacion(x, criterio=criterio)
        )
    elif criterio == "tiempo":
        map_color = map_color_ocupacion
        df_rep["color"] = df_rep["Ocupación planificada (segundos)"].apply(
            lambda x: (
                set_color_ocupacion(x, criterio=criterio)
                if pd.notna(x)
                else set_color_ocupacion(0, criterio=criterio)
            )
        )

    # Info para mostrar
    df_rep[["HoraInicioPlanificada", "HoraFinPlanificada"]] = df_rep[
        ["Llegadaplanificada", "Salidaplanificada"]
    ].map(lambda x: x.strftime("%H:%M:%S") if x and pd.notna(x) else "")
    hover_cols_plan = [
        "T1",
        "T2",
        "Movimiento",
        "HoraInicioPlanificada",
        "HoraFinPlanificada",
        "OcupaciónPlanificada",
        "VíaPlanificada",
    ]
    df_rep["hover_info_plan"] = setHoverInfo(df_rep, hover_cols_plan)

    # Ordenamos las vías
    df_rep["Vía_order"] = df_rep["VíaPlanificada"].apply(platform_map.get)

    dy = 0.35
    opacity = 0.3
    width = 2
    mode = "lines"
    dash = "dot"

    used_names = []
    traces = []
    for c in sorted(df_rep["color"].unique(), key=color_sorter.get):
        df_aux = df_rep[df_rep["color"] == c]
        name = set_name(color=c, map_color=map_color) + " (planificación)"

        trace = addRectTrace(
            inicio=(None,),
            fin=(None,),
            y_inicio=0,
            y_fin=0,
            dy=0,
            mode="lines",
            color=c,
            name=name,
            hover_info=None,
            showlegend=True,
            opacity=opacity,
            width=0,
        )
        if name not in used_names:
            used_names.append(name)
        traces.append(trace)

        for _, row in (
            df_aux[
                df_aux["LlegadaPlanificada"].apply(bool)
                & df_aux["SalidaPlanificada"].apply(bool)
            ]
            .sort_values(by=["LlegadaPlanificada"])
            .iterrows()
        ):
            if row["VíaPlanificada"] not in platform_map:
                continue
            trace = addRectTrace(
                inicio=row["LlegadaPlanificada"],
                fin=row["SalidaPlanificada"],
                y_inicio=row["Vía_order"],
                y_fin=row["Vía_order"],
                dy=dy,
                mode=mode,
                color=c,
                name=name,
                hover_info=row["hover_info_plan"],
                showlegend=True if name not in used_names else False,
                opacity=opacity,
                width=width,
                dash=dash,
            )
            traces.append(trace)
            if name not in used_names:
                used_names.append(name)
    return traces


def incluirMargenes(df: pd.DataFrame, platform_map: dict, margen=10):
    """
    margen: int
        Tiempo de seguridad mínimo (en minutos) entre ocupaciones.
    t_min: int
        Duración mínima de ocupación (en minutos).
    """
    if df.empty:
        return []

    df_rep = df.copy()

    df_rep[["HoraInicioOcupación", "HoraFinOcupación"]] = df_rep[
        ["InicioOcupación", "FinOcupación"]
    ].map(lambda x: x.strftime("%H:%M:%S") if x and pd.notna(x) else "")

    # Info para mostrar
    hover_cols_free = [
        "Vía",
        "TipoVía",
        "HoraInicioOcupación",
        "HoraFinOcupación",
    ]
    df_rep["hover_info"] = setHoverInfo(df_rep, hover_cols_free)

    # Ordenamos las vías
    df_rep["Vía_order"] = df_rep["Vía"].apply(platform_map.get)

    opacity = 1
    width = 1
    mode = "lines"
    name = f"Margen ({margen} min)"

    traces = []
    for i, row in df_rep.iterrows():
        if row["Vía"] not in platform_map:
            continue
        trace = addRectTrace(
            inicio=row["InicioOcupación"],
            fin=row["FinOcupación"],
            y_inicio=row["Vía_order"],
            y_fin=row["Vía_order"],
            dy=0.1,
            mode=mode,
            color="green",
            name=name,
            hover_info=row["hover_info"],
            showlegend=True if not i else False,
            opacity=opacity,
            width=width,
        )
        traces.append(trace)
    return traces


def visualizacionOcupacionVia(
    df: pd.DataFrame,
    title: str = "",
    df_free: pd.DataFrame = None,
    margenes: pd.DataFrame = None,
    show_plan: bool = True,
    show_fail: bool = True,
):
    # En principio el criterio de colores es la empresa ferroviaria
    criterio = "EF"

    if df is None or df.empty:
        return

    traces = []
    use_df = df.copy().replace([pd.NA], [None])
    # Ordenamos las vías por número
    platform_map = getSortedPlatforms(
        use_df["Vía"].dropna().unique(), use_df["Código"].iloc[0]
    )

    # Pintamos tiempos libres
    if df_free is not None and not df_free.empty:
        traces.extend(incluirLibre(df_free, platform_map, margen=10, t_min=40))
    # Pintamos los fallos
    if show_fail:
        traces.extend(incluirFallos(use_df[use_df["Fallo"]], platform_map))
    # Incluir planificación
    if show_plan and "Ocupación planificada (segundos)" in use_df.columns:
        traces.extend(
            incluirPlanificacion(use_df, platform_map=platform_map, criterio=criterio)
        )
    # Incluir planificación
    if margenes is not None and not margenes.empty:
        traces.extend(incluirMargenes(margenes, platform_map=platform_map, margen=10))
    # Pintamos los cambios de vía
    if criterio == "EF":
        traces.extend(
            incluirCambioVia(
                use_df.dropna(subset="cambio_vía"), platform_map=platform_map
            )
        )
    # Pintamos las ocupaciones normales
    traces.extend(
        incluirOcupaciones(
            use_df[~use_df["Fallo"]], platform_map=platform_map, criterio=criterio
        )
    )

    # Creamos el layout
    min_date = pd.to_datetime(
        (pd.to_datetime(use_df["FinOcupación"]).min() - timedelta(hours=0.5)).strftime(
            "%Y-%m-%d %H"
        )
    ) - timedelta(hours=1)
    max_date = pd.to_datetime(
        (pd.to_datetime(use_df["FinOcupación"]).max() + timedelta(hours=0.5)).strftime(
            "%Y-%m-%d %H"
        )
    ) + timedelta(hours=1)
    layout = setLayout("togglegroup", title, platform_map, x_range=(min_date, max_date))
    # layout = None

    fig = go.Figure(data=traces, layout=layout)

    return fig


def visualizacionSaturacionVia(
    df: pd.DataFrame, df_free: pd.DataFrame = None, title: str = ""
):
    if df.empty:
        return
    traces = []
    df_rep = df.copy().replace([pd.NA], [None])

    # Definimos el color
    df_rep["color"] = df_rep["Occ"].apply(
        lambda x: set_color_saturacion(x) if pd.notna(x) else set_color_saturacion(0)
    )

    # Info para mostrar
    df_rep["TiempoOcupación"] = df_rep["Ocupación"].apply(formatTimedelta)
    hover_cols = [
        "Vía",
        "TipoVía",
        "Fecha",
        "TiempoOcupación",
        "Ocupación (%)",
        "Trenes",
    ]
    df_rep["hover_info"] = setHoverInfo(df_rep, hover_cols)

    # Ordenamos las vías
    platform_map = getSortedPlatforms(
        df_rep["Vía"].dropna().unique(), df_rep["Código"].iloc[0]
    )
    df_rep["Vía_order"] = df_rep["Vía"].apply(platform_map.get)

    # Aplicamos lo mismo en tiempo libre
    if df_free is not None and not df_free.empty:
        df_free["color"] = "silver"
        df_free["TiempoLibre"] = df_free["Libre"].apply(formatTimedelta)
        df_free["FinOcupación"] = df_free["FinLibre"]
        hover_cols = ["Vía", "TipoVía", "Fecha", "TiempoLibre", "Libre (%)"]
        df_free["hover_info"] = setHoverInfo(df_free, hover_cols)
        df_free["Vía_order"] = df_free["Vía"].apply(platform_map.get) - 0.16
        df_rep["Vía_order"] = df_rep["Vía_order"] + 0.16
        df_rep = pd.concat((df_rep, df_free))

    used_names = []
    opacity = 1
    dy = 0.15
    width = 1
    mode = "lines"
    for c in sorted(df_rep["color"].unique(), key=color_sorter.get):
        df_aux = df_rep[df_rep["color"] == c]
        name = set_name(color=c, map_color=map_color_saturacion)

        for _, row in df_aux.sort_values(by=["Fecha"]).iterrows():
            if row["Vía"] not in platform_map:
                continue
            color = c
            trace = addRectTrace(
                inicio=row["Fecha"],
                fin=row["FinOcupación"],
                y_inicio=row["Vía_order"],
                y_fin=row["Vía_order"],
                dy=dy,
                mode=mode,
                color=color,
                name=name,
                hover_info=row["hover_info"],
                showlegend=True if name not in used_names else False,
                opacity=opacity,
                width=width,
            )
            traces.append(trace)
            if name not in used_names:
                used_names.append(name)

    # Creamos el layout
    min_date = pd.to_datetime(
        (df_rep["Fecha"].min() - timedelta(hours=0.5)).strftime("%Y-%m-%d %H")
    ) - timedelta(hours=1)
    max_date = pd.to_datetime(
        (df_rep["FinOcupación"].max() + timedelta(hours=0.5)).strftime("%Y-%m-%d %H")
    ) + timedelta(hours=1)
    layout = setLayout("togglegroup", title, platform_map, x_range=(min_date, max_date))
    fig = go.Figure(data=traces, layout=layout)
    for x0 in df_rep["Fecha"].unique():
        fig.add_vline(x=x0, line_width=1)
    return fig


def visualizeAnticipacionVia(
    df: pd.DataFrame,
    # show_stage: str = "Inicio",
    include_na: str = "all",
    title: str = "",
):
    traces = []
    df_rep = df.copy().replace([pd.NA], [None])

    # Info shown
    hover_cols = [
        c
        for c in ["T1", "Movimiento", "Vía", "TipoVía", "VíaPlanificada"]
        if c in df_rep.columns
    ]

    # if include_na == "all":
    #     pass
    # elif include_na == "notna":
    #     df_rep = df_rep.dropna(subset=hover_cols)
    # elif include_na == "na":
    #     df_rep = df_rep[df_rep[hover_cols].isna().any(axis=1)]

    # Quitamos los trenes especiales y demás
    df_rep["T1"] = df_rep["T1"].apply(rellenarId)
    df_rep = df_rep[df_rep["T1"].apply(isValidCode)]

    df_rep[["HoraPlataforma", "HoraSalida", "HoraAproximación", "HoraLlegada"]] = (
        df_rep[
            [
                "AnticipaciónPlataforma",
                "AnticipaciónSalida",
                "AnticipaciónAproximación",
                "AnticipaciónLlegada",
            ]
        ].map(lambda x: x.strftime("%H:%M:%S") if pd.notna(x) else "")
    )

    orig_hov = hover_cols + ["HoraPlataforma", "HoraSalida", "Anticipación"]
    paso_hov = hover_cols + ["HoraAproximación", "HoraLlegada", "Anticipación"]

    df_rep["color"] = df_rep["Anticipación (segundos)"].apply(
        lambda x: (
            set_color_anticipacion(x) if pd.notna(x) else set_color_anticipacion(0)
        )
    )
    df_rep["shape"] = df_rep["T1"].apply(set_shape)
    # df_rep["color"] = "RoyalBlue"
    # df_rep = df_rep[df_rep["Movimiento"].isin(["ORIGEN", "PASO", "ROTACIÓN"])]
    if df_rep.empty:
        return

    # Info para mostrar
    # df_rep["hover_info"] = None
    df_rep.loc[df_rep["Movimiento"] == "ORIGEN", "hover_info"] = setHoverInfo(
        df_rep.loc[df_rep["Movimiento"] == "ORIGEN"], orig_hov
    )
    df_rep.loc[~df_rep["Movimiento"].isin(["ORIGEN"]), "hover_info"] = setHoverInfo(
        df_rep.loc[~df_rep["Movimiento"].isin(["ORIGEN"])], paso_hov
    )

    # Elegimos el punto para mostrar
    df_rep.loc[df_rep["Movimiento"] == "ORIGEN", "show_stage"] = df_rep.loc[
        df_rep["Movimiento"] == "ORIGEN", "AnticipaciónPlataforma"
    ]
    df_rep.loc[~df_rep["Movimiento"].isin(["ORIGEN"]), "show_stage"] = df_rep.loc[
        ~df_rep["Movimiento"].isin(["ORIGEN"]), "AnticipaciónLlegada"
    ]

    # Ordenamos las vías
    platform_map = getSortedPlatforms(
        df_rep["Vía"].dropna().unique(), df_rep["Código"].iloc[0]
    )
    df_rep["Vía_order"] = df_rep["Vía"].apply(platform_map.get)

    # Define figure elements

    # Add traces
    for c in sorted(df_rep["color"].unique(), key=color_sorter.get):
        df_g1 = df_rep[df_rep["color"] == c]
        for orient in sorted(df_g1["shape"].unique(), key=shape_sorter.get):
            df_g2 = df_g1[df_g1["shape"] == orient]
            name = set_name(
                color=c,
                shape=orient,
                map_color=map_color_anticipacion,
                map_shape=map_shape,
            )
            traces.append(
                go.Scatter(
                    x=df_g2["show_stage"],
                    y=df_g2["Vía_order"],
                    visible=True,
                    mode="markers",
                    # marker=dict(
                    #     **marker,
                    #     color=df_g2["Anticipación_delta"],
                    #     symbol=df_g2["shape"],
                    # ),
                    marker_color=df_g2["color"],
                    marker_symbol=df_g2["shape"],
                    marker_size=10,
                    hoverinfo="text",
                    hovertext=df_g2["hover_info"],
                    textfont=dict(family="calibri", size=18),
                    name=name,
                    showlegend=True,
                    legendgroup=f"{name}",
                    # legendgrouptitle={"text": map_color[c]},
                )
            )

    # Creamos el layout
    layout = setLayout("toggleitem", title, platform_map)

    fig = go.Figure(data=traces, layout=layout)

    return fig


def planificacionVias(
    conf: pd.DataFrame, show: str = "Absoluto", title: str = "<b>Matriz confusión</b>"
):
    """
    Genera la vista de vias reales vs vias planificadas a partir de una matriz de confusión.

    conf: pd.DataFrame
        Dataframe de `NxN` donde `N` es el número de vías total.
        El nombre de index es `Vía` y el de columns es `Vía planificada`.
    """
    form = ""
    if show == "Relativo":
        form = "%"

    hover_cols = ["Vía", "VíaPlanificada", "Total"]
    c_len = max([len(c) for c in hover_cols]) + 2
    fd_len = min(
        conf.fillna("").map(lambda x: len(f"{x}"), na_action="ignore").max().max()
        + len(form),
        8,
    )
    hover_info = [
        [
            f"{'Vía':<{c_len}}{v:>{fd_len-len(form)}}<br>"
            + f"{'Vía planificada':<{c_len}}{vp:>{fd_len-len(form)}}<br>"
            + f"{'Total':<{c_len-len(form)}}{t:>{fd_len-len(form)}.0f}{form}"
            for vp, t in el.items()
        ]
        for v, el in conf.fillna(0).to_dict(orient="index").items()
    ]

    # fig
    fig = go.Figure(
        data=go.Heatmap(
            z=conf.values,
            # x=via_order,
            # y=via_order,
            x=conf.columns,
            y=conf.index,
            hoverongaps=False,
            hoverinfo="text",
            hovertext=hover_info,
            zmin=0,
            zmax=conf.fillna(0).values.sum() / max(conf.shape),
        )
    )

    # add title
    fig.update_layout(
        title_text=title,
        xaxis=dict(
            title=dict(
                font=dict(color="black", size=14),
                text="VíaPlanificada",
            ),
            tickson="boundaries",
        ),
        yaxis=dict(
            title=dict(
                font=dict(color="black", size=14),
                text="VíaReal",
            ),
            tickson="boundaries",
        ),
    )

    # adjust margins to make room for yaxis title
    fig.update_layout(
        margin=dict(t=50, l=100),
        xaxis_tickangle=0,
        yaxis_tickangle=0,
        # width=800,
        # height=800,
        hoverlabel=dict(bgcolor="white", font_size=16, font_family="consolas"),
        paper_bgcolor=BGCOLOR,
        plot_bgcolor=BGCOLOR,
    )

    # add colorbar
    fig["data"][0]["showscale"] = True
    return fig


###
# PLANIFICACIONES
###


def build_hierarchical_dataframe(
    df: pd.DataFrame,
    levels: list[str],
    value_column: str,
    color_columns=None,
    top_node="total",
):
    """
    Build a hierarchy of levels for Sunburst or Treemap charts.

    Levels are given starting from the bottom to the top of the hierarchy,
    ie the last level corresponds to the root.
    """
    df_list = []
    for i, level in enumerate(levels):
        df_tree = pd.DataFrame(columns=["id", "label", "parent", "value", "color"])
        dfg = df.groupby(levels[i:]).sum()
        dfg = dfg.reset_index()
        df_tree["label"] = dfg[level].copy()
        df_tree["id"] = dfg[levels[i:]].apply(lambda x: "_".join(x), axis=1).copy()
        if i < len(levels) - 1:
            df_tree["parent"] = (
                dfg[levels[i + 1 :]].apply(lambda x: "_".join(x), axis=1).copy()
            )
        else:
            df_tree["parent"] = top_node
        df_tree["value"] = dfg[value_column]
        df_tree["color"] = dfg[color_columns[0]] / dfg[color_columns[1]]
        df_list.append(df_tree)
    total = pd.DataFrame(
        [
            dict(
                id=top_node,
                label=top_node,
                parent="",
                value=df[value_column].sum(),
                color=df[color_columns[0]].sum() / df[color_columns[1]].sum(),
            ),
        ]
    )
    df_list.append(total)
    df_all_trees = pd.concat(df_list, ignore_index=True)
    return df_all_trees


def mostrarConfusionTree(
    df: pd.DataFrame,
    title: str,
    levels: list[str],
    value_column: str,
    color_columns=None,
    top_node="total",
):
    """
    Genera un diagrama de sankey para confusión

    origen: str
        Columna origen
    destino: str
        Columna destino
    size: str
        Columna con el tamaño de la relación
    """
    df_all_trees = build_hierarchical_dataframe(
        df,
        levels,
        value_column,
        color_columns,
        top_node=top_node,
    )
    df_all_trees["color"] = df_all_trees["color"].fillna(0)
    df_all_trees["color"] = df_all_trees["color"] * 100

    traces = []
    traces.append(
        go.Treemap(
            ids=df_all_trees["id"],
            labels=df_all_trees["label"],
            parents=df_all_trees["parent"],
            values=df_all_trees["value"],
            branchvalues="total",
            marker=dict(
                colors=df_all_trees["color"],
                colorscale="rdylgn",
                cmid=50,
                cmax=100,
                cmin=40,
            ),
            hovertemplate="<b>%{label} </b> <br> Movimientos: %{value}<br> Correctos: %{color:.2f}%",
            name="",
            maxdepth=3,
            # pathbar_textfont_size=50,
            # textfont_size=20,
        )
    )
    fig = go.Figure(traces)
    fig.update_layout(
        title=title,
        margin=dict(t=50, l=25, r=25, b=25),
    )
    return fig


def mostrarConfusionSankey(
    df: pd.DataFrame, title: str, origen: str, destino: str, size: str
):
    """
    Genera un diagrama de sankey para confusión

    origen: str
        Columna origen
    destino: str
        Columna destino
    size: str
        Columna con el tamaño de la relación
    """
    df = df.rename(columns={size: "Total"}).copy()

    # Origen
    group_src = (
        df[[origen, destino, "Total"]]
        .groupby(origen)
        .agg({destino: "size", "Total": "sum"})
        .reset_index()
        .rename(columns={destino: "Destinos"})
        .copy()
    )
    _ord_map = {k: v for v, k in enumerate(sortElements(group_src[origen].values))}
    group_src["_ord"] = group_src[origen].apply(_ord_map.get)
    group_src = group_src.sort_values(by="_ord").reset_index(drop=True)
    group_src["_c_sum"] = group_src["Total"].cumsum()
    group_src["x_pos"] = 0.2
    group_src["y_pos"] = range_normalization(group_src["_c_sum"])
    hover_cols = [origen, "Destinos", "Total"]
    group_src["hover_text"] = setHoverInfo(group_src, hover_cols)
    group_src["label"] = group_src.apply(lambda x: f"{x[origen]}<br>Total: {x['Total']:,}", axis=1)

    # Destino
    group_dst = (
        df[[origen, destino, "Total"]]
        .groupby(destino)
        .agg({origen: "size", "Total": "sum"})
        .reset_index()
        .rename(columns={origen: "Orígenes"})
        .copy()
    )
    _ord_map = {k: v for v, k in enumerate(sortElements(group_dst[destino].values))}
    group_dst["_ord"] = group_dst[destino].apply(_ord_map.get)
    group_dst = group_dst.sort_values(by="_ord").reset_index(drop=True)
    group_dst["_c_sum"] = group_dst["Total"].cumsum()
    group_dst["x_pos"] = 0.8
    group_dst["y_pos"] = range_normalization(group_dst["_c_sum"])
    hover_cols = [destino, "Orígenes", "Total"]
    group_dst["hover_text"] = setHoverInfo(group_dst, hover_cols)
    group_dst["label"] = group_dst.apply(lambda x: f"{x[destino]}<br>Total: {x['Total']:,}", axis=1)

    # Nodos
    nodes = pd.concat(
        [
            group_src[[origen, "x_pos", "y_pos", "hover_text","label"]].rename(
                columns={origen: "Elemento"}
            ),
            group_dst[[destino, "x_pos", "y_pos", "hover_text","label"]].rename(
                columns={destino: "Elemento"}
            ),
        ],
        ignore_index=True,
    ).reset_index()
    elementos = nodes["Elemento"].unique()
    cmap_elementos = sample_random_colors(elementos)
    nodes["color"] = nodes["Elemento"].apply(cmap_elementos.get)

    # Enlaces
    hover_cols = [origen, destino, "Total"]
    hover_text_link = setHoverInfo(df, hover_cols)

    map_group_src = {
        k: v for v, k in enumerate(sortElements(group_src[origen].unique().tolist()))
    }
    map_group_dst = {
        k: v
        for v, k in enumerate(
            sortElements(group_dst[destino].unique().tolist()),
            start=len(map_group_src),
        )
    }

    traces = [
        go.Sankey(
            arrangement="snap",
            node=dict(
                pad=10,
                thickness=30,
                label=nodes["label"],
                x=nodes["x_pos"],
                y=nodes["y_pos"],
                color=nodes["color"],
                align="left",    
                customdata=nodes["hover_text"],
                hovertemplate="%{customdata}",
            ),
            link=dict(
                arrowlen=20,
                source=df[origen].apply(map_group_src.get),
                target=df[destino].apply(map_group_dst.get),
                value=df["Total"],  # .apply(np.log10),
                customdata=hover_text_link,
                hovertemplate="%{customdata}",
                hovercolor=[cmap_elementos[v] for v in df[origen]],
            ),
        )
    ]

    layout = setLayout(
        "togglegroup",
        title=title,
    )
    annotations = [
        {
            "xref": "paper",
            "yref": "paper",
            "x": 0.17,
            "y": -0.2,
            "text": "Planificación",
            "showarrow": False,
            "font": {"size": 15, "color": "black"},
        },
        {
            "xref": "paper",
            "yref": "paper",
            "x": 0.81,
            "y": -0.2,
            "text": "Real",
            "showarrow": False,
            "font": {"size": 15, "color": "black"},
        },
    ]
    fig = go.Figure(data=traces, layout=layout)
    fig = fig.update_layout(
        autosize=False,
        width=1200,
        height=500,
        title_x=0.5,
        margin=dict(l=0, r=0),
        annotations=annotations,
    )
    return fig


# def mostrarConfusionSankey1(
#     df: pd.DataFrame, title: str, origen: str, destino: str, size: str
# ):
#     df = df.rename(columns={size: "Total"}).copy()

#     # Origen
#     group_src = (
#         df[[origen, destino, "Total"]]
#         .groupby(origen)
#         .agg({destino: "size", "Total": "sum"})
#         .reset_index()
#         .rename(columns={destino: "Destinos"})
#         .copy()
#     )
#     _ord_map = {k: v for v, k in enumerate(sortElements(group_src[origen].values))}
#     group_src["_ord"] = group_src[origen].apply(_ord_map.get)
#     group_src = group_src.sort_values(by="_ord").reset_index(drop=True)
#     group_src["_c_sum"] = group_src["Total"].cumsum()
#     group_src["x_pos"] = 0.2
#     group_src["y_pos"] = range_normalization(group_src["_c_sum"])
#     group_src["hover_text"] = setHoverInfo(group_src, [origen, "Destinos", "Total"])

#     # Destino
#     group_dst = (
#         df[[origen, destino, "Total"]]
#         .groupby(destino)
#         .agg({origen: "size", "Total": "sum"})
#         .reset_index()
#         .rename(columns={origen: "Orígenes"})
#         .copy()
#     )
#     _ord_map = {k: v for v, k in enumerate(sortElements(group_dst[destino].values))}
#     group_dst["_ord"] = group_dst[destino].apply(_ord_map.get)
#     group_dst = group_dst.sort_values(by="_ord").reset_index(drop=True)
#     group_dst["_c_sum"] = group_dst["Total"].cumsum()
#     group_dst["x_pos"] = 0.8
#     group_dst["y_pos"] = range_normalization(group_dst["_c_sum"])
#     group_dst["hover_text"] = setHoverInfo(group_dst, [destino, "Orígenes", "Total"])

#     # Nodos combinados
#     nodes = pd.concat(
#         [
#             group_src[[origen, "x_pos", "y_pos", "hover_text"]].rename(columns={origen: "Elemento"}),
#             group_dst[[destino, "x_pos", "y_pos", "hover_text"]].rename(columns={destino: "Elemento"}),
#         ],
#         ignore_index=True,
#     ).reset_index(drop=True)

#     elementos = nodes["Elemento"].unique()
#     cmap_elementos = sample_random_colors(elementos)
#     nodes["color"] = nodes["Elemento"].apply(cmap_elementos.get)

#     # Reindexar nodos para Sankey
#     elementos_ordenados = sortElements(group_src[origen].unique().tolist()) + \
#                           sortElements(group_dst[destino].unique().tolist())

#     map_elemento_to_idx = {elem: idx for idx, elem in enumerate(elementos_ordenados)}
#     nodes["sankey_idx"] = nodes["Elemento"].apply(map_elemento_to_idx.get)
#     nodes = nodes.sort_values("sankey_idx").reset_index(drop=True)

#     # Enlaces
#     hover_text_link = setHoverInfo(df, [origen, destino, "Total"])

#     map_group_src = {k: v for v, k in enumerate(sortElements(group_src[origen].unique().tolist()))}
#     map_group_dst = {
#         k: v for v, k in enumerate(
#             sortElements(group_dst[destino].unique().tolist()),
#             start=len(map_group_src)
#         )
#     }

#     traces = [
#         go.Sankey(
#             arrangement="snap",
#             node=dict(
#                 pad=10,
#                 thickness=30,
#                 label=["" for _ in nodes["Elemento"]],  # Oculta etiquetas internas
#                 x=nodes["x_pos"],
#                 y=nodes["y_pos"],
#                 color=nodes["color"],
#                 customdata=nodes["hover_text"],
#                 hovertemplate="%{customdata}",
#             ),
#             link=dict(
#                 arrowlen=20,
#                 source=df[origen].apply(map_group_src.get),
#                 target=df[destino].apply(map_group_dst.get),
#                 value=df["Total"],
#                 customdata=hover_text_link,
#                 hovertemplate="%{customdata}",
#                 hovercolor=[cmap_elementos[v] for v in df[origen]],
#             ),
#         )
#     ]

#     layout = setLayout("togglegroup", title=title)
#     layout.update(
#         width=1200,
#         height=500,
#         margin=dict(l=0, r=0),
#     )

#     fig = go.Figure(data=traces, layout=layout)

#     # Agregar etiquetas externas bien alineadas
#     for _, row in nodes.iterrows():
#         offset = -0.05 if row["x_pos"] < 0.5 else 0.05
#         align = "right" if row["x_pos"] < 0.5 else "left"
#         fig.add_annotation(
#             x=row["x_pos"] + offset,
#             y=row["y_pos"],
#             text=row["Elemento"],
#             showarrow=False,
#             font=dict(size=12, color=row["color"]),
#             xanchor=align,
#             yanchor="middle",
#         )

#     return fig
