import numpy as np
from plotly.colors import sample_colorscale

from src.utils import isOdd

color_sorter = {
    c: i
    for i, c in enumerate(
        [
            "#830065",  # pantone 2425c renfe
            "#DA291C",  # pantone 485c iryo
            "#0096CA",  # azul ouigo
            "silver",
            "mediumblue",
            "green",
            "yellow",
            "khaki",
            "orange",
            "red",
            "darkred",
            "black",
        ]
    )
}

# Productos
map_product_color = {
    "AV": "#830065",
    "CERCANIAS": "brown",
    "CERCANIAS RAM": "#008000",
    "REGIONAL RAM": "#838065",
    "MD-LD": "indigo",
    "otros (viajeros)": "mediumblue",
    "otros (no viajeros)": "orange",
    "otros": "mediumblue",
}

# Empresas ferroviarias
map_EF_color = {
    "RENFE": "#830065",
    "IRYO": "#DA291C",
    "OUIGO": "#0096CA",
    "INCORRECTO": "khaki",
    "Otro": "mediumblue",
}
map_color_EF = {c: ef for ef, c in map_EF_color.items()}

# Formas
map_shape = {
    "triangle-up": "Impar",
    "triangle-down": "Par",
    "x": "Indefinido",
}

shape_sorter = {
    s: i
    for i, s in enumerate(
        [
            "triangle-up",
            "triangle-down",
            "x",
        ]
    )
}


def set_shape(n_tec):
    is_odd = isOdd(n_tec)
    # int_part = "".join(regex.findall(r"\d+", f"{n_tec}"))
    # if not int_part:
    #     return "x"
    # isOdd = int(f"{int_part.group()}") % 2
    if is_odd is None:
        return "x"
    elif is_odd:
        shape = "triangle-up"
    else:
        shape = "triangle-down"
    return shape


def set_name(
    color: str = None,
    shape: str = None,
    map_color: dict = None,
    map_shape: dict = None,
):
    name_parts = []
    if map_color:
        name_parts.append(map_color[color])
    if map_shape:
        name_parts.append(map_shape[shape])
    return ", ".join(name_parts)


# Ocupaciones
map_color_ocupacion = {
    "mediumblue": "<5 segundos",
    "green": "<120 segundos",
    # "yellow": "<180 segundos",
    "orange": "<180 segundos",
    "red": "<300 segundos",
    "darkred": ">300 segundos",
    "black": "Error",
}


def set_color_ocupacion(s, criterio="EF"):
    """
    Establece el color de la ocupación en función de un criterio.
    criterio: {"EF", "tiempo"}
    """
    # if criterio == "EF":
    #     if regex.search(
    #         r"(ALVIA|AVANT|AVE|CERCANIAS|INTERCITY|MD|REGIONAL EXPRES|TALGO|AVLO)", s
    #     ):
    #         return "#830065"
    #     if regex.search(r"(IRYO)", s):
    #         return "#DA291C"
    #     elif regex.search(r"(OUIGO)", s):
    #         return "#0096CA"
    #     else:
    #         return "mediumblue"
    if criterio == "EF":
        return map_EF_color.get(s, "mediumblue")

    elif criterio == "tiempo":
        if s < 5:
            return "mediumblue"
        elif s <= 120:
            return "green"
        elif s < 180:
            return "orange"
        elif s <= 300:
            return "red"
        elif s > 300:
            return "darkred"
        else:
            return "black"


# Saturación
map_color_saturacion = {
    "green": "<30%",
    "orange": "<60%",
    "red": ">60%",
    "black": "100%",
    "silver": "Libre",
}


def set_color_saturacion(s):
    if s < 0.3:
        return "green"
    elif s <= 0.6:
        return "orange"
    elif s <= 1:
        return "red"
    else:
        return "black"


# Anticipación
map_color_anticipacion = {
    "mediumblue": "<5 segundos",
    "green": ">90 segundos",
    # "yellow": "Amarillo",
    # "orange": "Naranja",
    "red": "<90 segundos",
    # "darkred": "Granate",
    "black": "Error",
}


def set_color_anticipacion(s):
    if s < 5:
        return "mediumblue"
    elif s < 90:
        return "red"
    # elif s < 90:
    #     return "orange"
    elif s >= 90:
        return "green"
    else:
        return "black"


def sample_random_colors(unique_list: list):
    """
    unique_list: list
    Lista con elementos únicos a los que asignar un color
    """
    n_colors = len(unique_list)
    colors = sample_colorscale("turbo", [n / n_colors for n in range(n_colors)])
    np.random.seed(1)
    np.random.shuffle(colors)
    cmap_elements = dict(zip(unique_list, colors))
    return cmap_elements
