# TenderoBot — Qué hay que construir

## Objetivo
Construir un bot de Telegram con IA que le lleve el inventario al tendero de barrio en Colombia y le arme el pedido al distribuidor. Es para una competencia: debe funcionar de punta a punta, sin errores, en una demo en vivo.

**Problema que resuelve:** el tendero no lleva inventario porque le cuesta más tiempo del que tiene, y pide mal: se queda sin lo que vende y lleno de lo que no.

**Quién paga:** distribuidores de consumo masivo y fintechs de tenderos.

**De dónde salen los datos:** las ventas que registra el tendero y el catálogo del distribuidor.

## Qué debe hacer el bot (tendero)
1. **Vincular la tienda:** con `/start` el tendero toca 📱 Compartir mi número (Telegram manda su número verificado; debe ser el celular que el distribuidor registró para la tienda) y acepta el tratamiento de datos personales (Ley 1581 de 2012). Sin aceptar, el bot no funciona.
2. **Registrar ventas por voz o texto:** el tendero dice o escribe "vendí 2 Coca-Cola litro y un pan tajado". El bot identifica productos y cantidades, los busca en el catálogo, muestra un resumen y solo guarda si el tendero pulsa ✅ Confirmar.
3. **Contar inventario con foto:** el tendero manda una foto del estante. El bot estima cuántas unidades hay de cada producto, avisa que es una estimación y deja corregir antes de confirmar.
4. **`/stock`:** muestra el inventario actual.
5. **`/alertas`:** muestra qué productos se van a acabar y cuáles no se están vendiendo.
6. **`/pedido`:** genera el pedido sugerido en cajas para la próxima entrega, con el motivo de cada producto, el total en pesos y la hora límite. El tendero lo ajusta por texto o voz ("quita el arroz", "ponle 2 cajas más de coca") y lo envía con un botón. El día de la hora límite, si no ha pedido, el bot le manda un recordatorio automático.
7. **`/recibido`:** confirma que llegó el pedido y suma la mercancía al inventario.
8. **`/ayuda`:** ejemplos de uso.

## Qué debe hacer el panel web (distribuidor)
Login con correo y contraseña. Debe mostrar:
1. **Resumen:** tiendas activas, tiendas en riesgo de quedarse sin producto y valor de los pedidos pendientes.
2. **Tiendas:** lista con alertas por tienda.
3. **Detalle de tienda:** gráfico de ventas diarias, clasificación de productos (alta, media y baja rotación) y pedido sugerido.
4. **Demanda agregada:** qué productos van a necesitar todas sus tiendas la próxima semana.

## Cálculos de inventario (sin IA, solo código)
- **Stock actual** = último conteo + mercancía recibida − ventas desde ese conteo.
- **Venta diaria** = promedio de ventas de los últimos 28 días.
- **Días de cobertura** = stock actual ÷ venta diaria.
- **Horizonte** = días desde hoy hasta la entrega siguiente a la próxima entrega alcanzable. Un pedido alcanza una entrega si se envía antes de las 6 p. m., con los días de anticipación de la tienda (mínimo 1). Los días de entrega los escoge el tendero entre los días de ruta del proveedor.
- **Stock de seguridad** = 1,65 × desviación estándar de ventas diarias × √horizonte.
- **Pedido** = venta diaria × horizonte + stock de seguridad − stock actual, redondeado hacia arriba a cajas completas.
- **Clasificación ABC** por valor vendido: A hasta 80 %, B hasta 95 %, C el resto.
- **Riesgo de agotarse:** días de cobertura menores que el horizonte.
- **Estancado:** sin ventas en 21 días con stock, o más de 45 días de cobertura.

## Reglas obligatorias
- Todo debe usar servicios **gratuitos**.
- La IA **solo extrae datos** (voz, texto o foto → JSON). Ningún cálculo lo hace la IA.
- Ningún producto se guarda si no existe en el catálogo.
- Toda extracción de la IA se confirma con botones antes de guardarse.
- Las claves y contraseñas van solo en un archivo `.env`, nunca en el código ni en GitHub.
- Cada tienda y cada distribuidor solo ven sus propios datos.
- No se guardan las fotos ni los audios; se procesan y se descartan.
- Código en inglés; todo lo que ve el usuario en español de Colombia, corto y sencillo.

## Tecnologías
- **Lenguaje:** Python 3.12.
- **Backend:** FastAPI.
- **Bot:** python-telegram-bot.
- **Base de datos:** PostgreSQL en Neon (gratis).
- **IA:** Grok (xAI) a través de la API del reto (`https://api.reto.pltk.mx/v1`), para voz, texto y fotos.
- **Búsqueda en catálogo:** rapidfuzz.
- **Panel:** plantillas Jinja2 + Chart.js.
- **Pruebas:** pytest.
- **Despliegue:** Docker + Render (gratis) + UptimeRobot para que no se apague.

## Datos de prueba
Un distribuidor, un usuario del panel, 3 tiendas, 30 productos típicos de tienda colombiana con precios en pesos y 60 días de ventas inventadas con patrones claros: un producto que se agota cada fin de semana, uno estancado y uno que crece.

## Qué se necesita conseguir
- Bot creado en **@BotFather** (token).
- API key del reto para **Grok**.
- Base de datos en **Neon**.
- Repositorio en **GitHub**.
- Cuenta en **Render**.
- Cuenta en **UptimeRobot**.
- Imagen de perfil del bot (logo serio y minimalista de una tiendita cuya fachada sugiere un robot).

## Qué se entrega al final
1. Bot de Telegram funcionando con todas sus funciones.
2. Panel web del distribuidor en línea.
3. Código en GitHub con README.
4. Demo en vivo: venta por voz → foto del estante → alertas → pedido → panel del distribuidor.

Esto es lo que aparecio en el problema adentro de la pagina del reto

Agente de inventario para tiendas de barrio
COMERCIO
EL DOLOR
El tendero no lleva inventario porque llevarlo cuesta más tiempo del que tiene, y pide mal: se queda sin lo que vende y lleno de lo que no.
QUÉ HACE EL AGENTE
Registra ventas por voz o foto del estante, detecta el patrón de rotación y arma el pedido sugerido al distribuidor.
QUIÉN PAGA
Distribuidores de consumo masivo, fintechs de tenderos
DE DÓNDE SALEN LOS DATOS
Ventas registradas, catálogo del distribuidor Agente de inventario para tiendas de barrio
COMERCIO
EL DOLOR
El tendero no lleva inventario porque llevarlo cuesta más tiempo del que tiene, y pide mal: se queda sin lo que vende y lleno de lo que no.
QUÉ HACE EL AGENTE
Registra ventas por voz o foto del estante, detecta el patrón de rotación y arma el pedido sugerido al distribuidor.
QUIÉN PAGA
Distribuidores de consumo masivo, fintechs de tenderos
DE DÓNDE SALEN LOS DATOS
Ventas registradas, catálogo del distribuidor
