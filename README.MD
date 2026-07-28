# Documentación

## Scripts
### api_elcano.ipynb
Es un notebook algo genérico en el que se pueden hacer peticiones a las diferentes apis de elcano para comprobar o actualizar diversos elementos de manera directa. Por ejemplo, hay que guardar de manera regular la información actualizada del `IHM topo`, ya que luego se accede a ello cada vez que se carga `src/utils/util/loadEstaciones()`.

### app.py
Esto es una prueba de concepto para crear una interfaz accesible a usuarios mediante un servidor (ver también [`map.py`](#mappy)). La idea es utilizar la biblioteca de `shiny` y montar un servidor local, donde de manera autónoma cada usuario pueda acceder al la URL y ejecutar el informe deseado, visualizarlo, descargarlo, o hacer otros filtros.

### auto_download.py
Permite la descarga directa de los logs de Graylog de xSIV, MIE y/o Sitra. También es práctico para identificar cuáles son las peticiones que deben hacerse si se quiere acceder de manera manual a Graylog.

Se puede extender (y sería recomendable) la funcionalidad para incluir la descarga de lo que se hace en [`api_elcano.ipynb`](#api_elcanoipynb).

### cambios_sentido.ipynb
Detecta cambios de sentido en las circulaciones a partir de logs de xSIV o MIE. Se considera que existe un cambio cuando:
-   XSIV: maniobras antes/después de salida/llegada
-   MIE: Cambio de paridad en un mismo enclavamiento

### canon_estacionamiento.ipynb
Es una modificación provisional sobre lo que se produce en [`ocupacion.py`](#ocupacionpy) para rellenar el excel de los cánones de estacionamiento.

### estado_estaciones.ipynb
Hace una comprobación del estado de las estaciones a partir de:
-   Logs de MIE/socket_in: descargado manualmente de Graylog (hay que guardar los campos de `message` y `source`) o solicitado por Helix
-   Catálogos: guardados localmente en `data/`
-   Topologías MSE: a partir de lo que hay en topogen
Hay que asegurarse de manera regular que estos datos estén actualizados.
Genera excel con el estado de todos los elementos ordenados por CTC, enclavamiento, tipo de elemento.

### fiabilidad_vias.ipynb
Fichero a ejecutar Diariamente en el que se analizan las fuentes de vía donde compara la vía planificada con la vía real**NOTA: Es necesario revisar el directorio sincronizado en el que se deja esta información.**

Adicionalmente, hacee un cálculo de la calidad de la planificación y asigna una nota en cada estación.
#### Rotulación_desrotulación.ipynb
Fichero que extraer los datos diarios de historico de movimiento donde comprueba si las circulaciones ha tenido problema de rotulacion o desrotulación

### main.py
En desuso (ver [`ocupacion.py`](#ocupacionpy))

### map.ipynb
Esto es una prueba de concepto para crear una visualización de mapa accesible a usuarios mediante un servidor (ver también [`app.py`](#apppy)).

### ocupacion.py
Hace el cálculo completo de ocupación en las estaciones seleccionadas en un periodo de tiempo. Esto incluye:
-   Gráfico de Ocupación de Vías
-   Gráfico de Anticipación de Vía
-   Gráficos de Saturación de Vías (periodos de 1h y 3h)
-   Excel que sirve como base para todo lo anterior

La ejecución normal viene dada por:
```
python ocupacion.py [-h] [-p {días,semanas}] [-i INICIO_SEMANA] [-d DIR_SAVE] [-s INICIO] [-e FIN] [-a ESTACIONES [ESTACIONES ...]] [-t [TRENES ...]] [-c CONFIG]
```

-    -p: procesar el rango de fechas por semanas completas o el total de los días seleccionados
-    -i: si '-p semanas' indica que el cuándo empieza la semana (0:lunes, 1:martes, etc.)
-    -d: directorio donde guardar el resultado por estación
-    -s: fecha inicio periodo, preferentemente formato %Y-%m-%d (%H:%M:%S) aunque otros pueden funcionar
-    -e: fecha fin periodo, preferentemente formato %Y-%m-%d (%H:%M:%S) aunque otros pueden funcionar
-    -a: lista de estaciones separadas por espacios
-    -t: lista de trenes separadas por espacios
-    -c: en caso de no rellenar el resto de parámetros se puede usar un fichero de configuración
En cualquier caso debe incluirse al menos un tren o una estación.
Ejemplo:
```python ocupacion.py -p días -d output/Madrid/ -s 2025-05-26 -e 2025-05-27 -i 0 -a 98003 70104```

### recorrido_linea.py
Representa los puntos de paso de las circulaciones seleccionadas. Habría que indicar exactamente cuáles son estas circulaciones, que deberían pertenecer todas a una misma línea. Por ejemplo, para cercanías o un trayecto concreto (ej: Madrid-Pamplona) representa lo que ha ocurrido en el histórico frente a la planificación obtenida del xPEC, así como las velocidades medias entre cada punto.

#### TODO
-   Establecer que la selección de líneas o trayectos sea a través de la petición de datos al histórico o de la planificación de xPEC, en vez de introducir los números técnicos a mano.
-   Replantear la representación del recorrido, si todo como una única malla (ida y vuelta) se cruzan, ida como representación independiente de la vuelta, etc.

### resumen_global.ipynb
Permite diversas fuentes de datos, aunque principalmente se utiliza el histórico de MOW. Genera descripciones del estado de la red/circulaciones a nivel global:
-   Rotulaciones/Desrotulaciones incorrectas:
    si la secuencia de paso no empieza en 1 o sigue circulando después de haber finalizado.
-   Trenes especiales:
    recuento por fecha y/o estaciones.
-   Trenes suprimidos:
    recuento por fecha y/o estaciones en total, en origen y en destino.
-   Calidad de mensajería:
    explora las secuencias de movimintos correctas en altas, paso y baja.
-   Tiempos inferencias:
    representa la calidad de la inferencia de movimientos, desde alta hasta salida y desde aproximación hasta llegada.
#### TODO
-   Incluir la provincia cuando esté disponible en la mensajería.

### validacion_mensajeria.ipynb
**Esto se ha utilizado para las comprobaciones de mensajería de Sitra y su correspondencia en xSIV, pero va a eliminarse para crear su propio informe.**

### vias_planif_ctc.ipynb
Hace una comparación de las vías de estacionamiento en las que se incluyen como fuentes xPEC, MOW/xSIV y las topologías MSE. Con esto se pueden encontrar fallos en la nomenclatura, vías que están de más o de menos en alguna de ellas, etc.


## src
### api
Proporciona todos los métodos y acceso a documentación de las APIs de circulaciones, MOW y IHM en el fichero `APIs.py`. Hay código en desuso heredado de Nat y Noe para Graylog en el resto.
### processor
Aquí está toda la funcionalidad para leer y formatear los logs de las distintas fuentes de datos (MOW, xSIV, Sitra, xPEC, MIE...).
### utils
### visualizacion
## data


# TODOs
## Carga xPEC
Además de actualizar con cierta frecuencia los ficheros de xPEC, a la hora de leer habría que incluir exactamente cuáles son los periodos de servicio en los que están activos. Al leer el fichero hay que asegurarse de que para cada NTécnico haya un registro para cada periodo (inicio-fin) donde se indiquen además los días de la semana activos y se excluyan los que no.
## Planificación de Gestor de Circulaciones
### Técnicas
Habría que establecer la manera de obtener el recorrido planificado completo a partir de lo que se obtiene del GCT `src.api.getPlanificacionCirculacionesTecnicas`. Se encuentra en la columna `Recorrido` en formato JSON.
### Comerciales
Obtener la planificación comercial de manera similar a la técnica.

<!-- 3.	KPI
Los formatos están por definir, aunque en un principio se esperarían tablas de Excel, puede cambiar en función del uso específico que se les quiera dar.
a.	Coincidencia de vía (previsión vs real)
i.	Resumen: indica el porcentaje de vías que han sido correctamente previstas.
ii.	Total: lista todos los movimientos que se han dado en cada estación por fecha, tren y vía, e indica si la vía prevista es o no correcta.
b.	Tiempos (retraso, ocupación, puntualidad)
i.	Resumen: indica los tiempos medios, máximos, mínimos, relativos, mediano de ocupación, retrasos, tramos libres, etc.
ii.	Total: información desagregada por fecha, vía y, en su caso, tren.
c.	Elementos de uso más frecuente
En cada elemento de un enclavamiento indica el uso que se le ha dado en un tiempo determinado.


Desarrollo
Además de lo anterior, hay otros informes que pueden ser de interés al equipo de desarrollo.
1.	Uso de elementos (catálogo vs recibido)
Compara los elementos que están definidos en el catálogo frente a los que se reciben por mensajería de la MIE.
La salida es un Excel con la siguiente información:
a.	Resumen de cada enclavamiento: incluye el número de elementos activos, inactivos y cuánto falta por completar en ese enclavamiento
b.	Elementos: lista de todos los elementos del catálogo y su estado de recepción desde la MIE y aparición o no en el fichero .pl. 
2.	Cambio de paridad en elementos
Se obtienen los elementos en los que hay un cambio de paridad del movimiento de un tren dentro de un mismo enclavamiento a través de la mensajería MIE, de esta forma se detectarán fallos en las visualizaciones donde la orientación de la bala del tren pueda ser incorrecta.
La salida es un Excel con la siguiente información:
a.	Resumen: lista de pares de elementos concretos (origen-destino) por mie y mnemónico en los que ha ocurrido ese cambio de paridad y número de ocurrencias.
b.	MIEs: eventos concretos en los que ha sucedido ese cambio, indicando la fecha, tren, elemento origen y elemento destino donde se da el cambio, así como la paridad en cada uno de ellos.
3.	Movimientos incorrectos
Se encuentran dependencias en las que la secuencia de movimientos de un tren es incorrecta (maniobras antes de llegadas, maniobras después de salidas, falta de baja/alta, salida sin entrada, etc.) a partir de la mensajería xSIV.
La salida no está del todo definida, pero puede ser la secuencia completa para cada tren indicando la fecha, el movimiento, el enclavamiento, la vía, etc.


[No implementado]:
•	Distribuciones temporales, desviación
•	Incidencias
•	Búsqueda de patrones sobre lo anterior -->
