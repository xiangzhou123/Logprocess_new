import pandas as pd
import plotly.graph_objects as go
import regex
import webcolors
import plotly.express as px
from src.utils import sortStrNumbers

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


# Function to convert named color to RGB
def named_color_to_rgba(color_name, alpha=1.0):
    rgb_value = webcolors.name_to_rgb(color_name)
    rgba_value = f"rgba({rgb_value.red}, {rgb_value.green}, {rgb_value.blue}, {alpha})"
    return rgba_value

## Funcion para crear el donut de rotulación

def crear_grafico_provincias(df,rotulacion = True):
    conteo_provincias = df['Delegación'].value_counts()
    
    fig = px.pie(
        values=conteo_provincias.values, 
        names=conteo_provincias.index,
        # title="Distribución por Delegación",
        hole=0.4,
        color_discrete_sequence=px.colors.qualitative.Set3
    )
    if(rotulacion == True):
        fig.update_traces(
        textposition='inside', 
        textinfo='value',
        textfont_size=30,
        hovertemplate='<b>%{label}</b><br>Mal rotulados: %{value}<br>Porcentaje: %{percent}<extra></extra>'
        )
    
        fig.update_layout(
        title_font_size=20,
        title_x=0.5,
        annotations=[dict(text='Total mal<br>rotulados', x=0.5, y=0.5, font_size=35, showarrow=False)],
        legend = dict(font=dict(size=20)),
        height=500,
        )
    else:
        fig.update_traces(
        textposition='inside', 
        textinfo='value',
        textfont_size=30,
        hovertemplate='<b>%{label}</b><br>Mal Desrotulados: %{value}<br>Porcentaje: %{percent}<extra></extra>'
        )
    
        fig.update_layout(
        title_font_size=20,
        title_x=0.5,
        annotations=[dict(text='Total mal<br>desrotulados', x=0.5, y=0.5, font_size=35, showarrow=False)],
        legend = dict(font=dict(size=18)),
        height=500,
        )
    return fig