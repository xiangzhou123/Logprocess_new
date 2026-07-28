# Documentación
## ENTORNO
crear un fichero .env dentro de proyecto con las siguientes variables:
-     -CLIENT_ID: cliente ID de sharepoint
-     -TENANT_ID tenant_id de sharepoint
-     -CLIENT_SECRET: contraseña de api de sharepoint.
-     -site_id="adif365.sharepoint.com,b975d513-b31f-42b3-8167-bb2219f603f3,39767b5f-6e4e-4154-83e7-8fce45eb6e69"
-     -GRAYLOG_URL="http://silog.solvan.comun.adif"
-     -GRAYLOG_USER
-     -GRAYLOG_PASSWORD
-      -user-key: use-key de los APIs de MSE
## Scripts


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
### rotulacion.py
Generar informe de día anterior de rotulaciones y desrotulaciones erróneas.

comando:
 python rotulacion.py
### Fiabilidad.py
Generar el informe de Fiabilidad de vía
comando:
 python Fiabilidad.py


### ErrorCatalogo.py
Comparación de mensajes recibidos de fuente JCTC con el csv de catálogo subido a IHM.
comando 
 python ErrorCatalogo.py

### Errores_topologías.py
informe comparativo entre mensajerias JCTC recibidas de Graylog con los ficheros json subidas de IHM topo
comando:
python Errores_topologías.py --ctc [acrónimo de CTC] 
ejemplo: python Errores_topologías.py --ctc  BNC

### Sitra.py
Script para sacar los mensajes de sitra de un día concreto
python Sitra.py   --fecha --tipo  --NTecnico – ruta
-     - Tipo:  1(RM), 2(RP), 3(RU)
-     -Fecha(Requerido): YYYY-MM-dd
-     -NTecnico(opcional) 
-     -ruta(opcional)

#### GCT.py
Scrypt para sacar las circulaciones planificadas de un día concreto. el límite es de dos días anteriores y posterior
 Python GCT.py --fecha –guarda /  Python GCT.py --fecha  -- ntécnico
 -    - fecha(requerido): YYYY-MM-DD
 -    - guarda, opción para guardar las circulaciones de día concreta
 -    - NTecnico(opcional): circulación concreta.
 -    - rotacion opcion para imprimir la rotación de una circulación de una fecha concreta.
[No implementado]:
•	Distribuciones temporales, desviación
•	Incidencias
•	Búsqueda de patrones sobre lo anterior -->
