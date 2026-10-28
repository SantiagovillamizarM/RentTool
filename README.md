# TenderoBot

Agente de inventario para tiendas de barrio en Colombia. Funciona en Telegram: el tendero le cuenta lo que vende (por voz o por texto) y le manda fotos del estante. El agente lleva el inventario y arma el pedido sugerido para el distribuidor.

Proyecto del **Agent Challenge** (categoría Comercio).

## El problema

El tendero no lleva inventario porque le toma más tiempo del que tiene. Por eso pide mal: se queda sin lo que más vende y se llena de lo que no sale.

- **Quién paga:** distribuidores de consumo masivo y fintechs de tenderos.
- **De dónde salen los datos:** las ventas que registra el tendero y el catálogo del distribuidor.

## Qué hace

| El tendero... | El agente... |
|---|---|
| Manda un audio o escribe *"vendí 2 Coca-Cola litro y un pan tajado"* | Entiende productos y cantidades, los busca en el catálogo y muestra un resumen. Solo guarda si el tendero pulsa ✅ Confirmar. |
| Manda una foto del estante | Estima cuántas unidades hay de cada producto, avisa que es una estimación y deja corregir antes de guardar. |
| Escribe `/stock` | Muestra el inventario actual. |
| Escribe `/alertas` | Dice qué se va a acabar y qué no se está vendiendo. |
| Escribe `/start` y toca 📱 Compartir mi número | Telegram le manda al bot el número verificado de esa cuenta (no se puede escribir otro). Si es el celular que el distribuidor registró para la tienda, entra. Sin códigos ni contraseñas. |
| Vincula su tienda por primera vez | Le pregunta qué días quiere recibir la mercancía, entre los días en que el proveedor pasa por su barrio. |
| Escribe `/pedido` | Sugiere el pedido para la próxima entrega, en cajas, con el motivo de cada producto, el total en pesos y la hora límite para enviarlo. |
| Responde *"quita el arroz"* o *"ponle 2 cajas más de coca"* (por texto o audio) | Ajusta el pedido. El tendero tiene la última palabra; el camión lleva solo lo que él envió. |
| No ha pedido y se acerca la hora límite | Le manda un recordatorio automático: *"El jueves te llega mercancía. Tienes hasta hoy a las 6 p. m."* |
| Escribe `/recibido` | Suma la mercancía del pedido al inventario. |
| Escribe `/catalogo` | Muestra lo que tiene su proveedor, con precios y lo que está agotado. |
| Escribe `/salir` | Cierra la sesión de la tienda en ese chat (pide confirmación). Los datos se conservan; para volver a entrar basta con compartir el número otra vez. Si entra otra cuenta de Telegram, se le pide su propia autorización de datos. |
| No usa el bot en 24 horas | Le avisa, de día, que la sesión sigue abierta, con botones para cerrarla o seguir. Un solo aviso hasta que vuelva a usar el bot. |

El distribuidor tiene un **panel web** (`/panel`) con el resumen de sus tiendas, las alertas de cada una, el gráfico de ventas, la clasificación ABC y la demanda agregada de la próxima semana.

En **Productos** maneja su catálogo: agrega productos, cambia precios, actualiza cuántas unidades tiene y desactiva lo que ya no vende. Al hacer clic en un producto ve qué días de la semana se vende más (últimas 8 semanas), en todas sus tiendas o en una sola, y puede ampliar el gráfico a pantalla completa.
- El bot solo sugiere y deja pedir lo que el distribuidor tiene, en cajas completas. Cuando el tendero envía un pedido, esas unidades se descuentan.
- Cuando cambia un precio o llega un producto nuevo, los tenderos reciben un aviso por Telegram. No se les avisa nada más, para no llenarles el chat.

## Cómo usa Grok

Grok **solo extrae datos**: convierte voz, texto o foto en JSON. Todos los cálculos de inventario los hace código normal, sin IA.

```
Voz  ──► Grok /stt (voz a texto) ──┐
Texto ─────────────────────────────┼─► Grok chat (JSON con esquema) ──► búsqueda en catálogo
Foto ──► Grok chat con imagen ──────┘        (rapidfuzz)
                                                  │
                                                  ▼
                              Resumen con botones ✅ Confirmar / ❌ Cancelar
                                                  │
                                                  ▼
                       PostgreSQL ──► motor de inventario (código) ──► /stock, /alertas, /pedido, panel
```

- La conexión es con la API del reto (`https://api.reto.pltk.mx/v1`), que es compatible con OpenAI. El modelo por defecto es `grok-4.20-0309-non-reasoning`.
- El código está en [app/ai/grok_client.py](app/ai/grok_client.py) y las instrucciones para Grok en [app/ai/prompts.py](app/ai/prompts.py).
- Si Grok devuelve algo inválido, se reintenta una vez. Si vuelve a fallar, el tendero ve: *"No te entendí, intenta de nuevo o escríbelo"*.
- Un producto que no está en el catálogo nunca se guarda.

### Ejemplo de ejecución

Entrada (texto en Telegram):

```
vendí 2 Coca-Cola litro, un pan tajado y 3 bon bon bum
```

JSON que devuelve Grok, ya ligado al catálogo:

```json
{"lines": [
  {"sku": "GAS-COCA-1L",  "product_text": "Coca-Cola litro", "quantity": 2},
  {"sku": "PAN-TAJA-UND", "product_text": "pan tajado",      "quantity": 1},
  {"sku": "CON-BONB-UND", "product_text": "bon bon bum",     "quantity": 3}
]}
```

Salida: el bot muestra el resumen con los botones. Al confirmar, las ventas quedan guardadas y se reflejan en `/stock`, `/alertas` y `/pedido`.

## Cálculos de inventario (sin IA)

En [app/inventory/engine.py](app/inventory/engine.py):

- **Stock actual** = último conteo + mercancía recibida − ventas desde ese conteo.
- **Venta diaria** = promedio de los últimos 28 días.
- **Días de cobertura** = stock actual ÷ venta diaria.
- **Entregas:** el tendero escoge sus días de entrega. Un pedido alcanza una entrega si se envía antes de las 6 p. m., con los días de anticipación de la tienda (mínimo 1).
- **Horizonte** = días desde hoy hasta la entrega siguiente a la próxima entrega alcanzable (lo que se pide hoy debe durar hasta que llegue el pedido siguiente).
- **Stock de seguridad** = 1,65 × desviación estándar de la venta diaria × √horizonte.
- **Pedido** = venta diaria × horizonte + stock de seguridad − stock actual, redondeado hacia arriba a cajas completas.
- **ABC** por valor vendido: A hasta el 80 %, B hasta el 95 %, C el resto.
- **Riesgo de agotarse:** días de cobertura menores que el horizonte.
- **Estancado:** sin ventas en 21 días teniendo stock, o más de 45 días de cobertura.

## Tecnologías

Python 3.12 · FastAPI · python-telegram-bot · PostgreSQL (Neon) · SQLAlchemy + Alembic · Grok (xAI) · rapidfuzz · Jinja2 + Chart.js · pytest

## Cómo correrlo

Requisitos: Python 3.12, una base PostgreSQL (por ejemplo Neon), un bot creado en @BotFather y la API key del reto.

```bash
pip install -r requirements.txt
cp .env.example .env        # llena TELEGRAM_BOT_TOKEN, DATABASE_URL, GROK_API_KEY, SESSION_SECRET
alembic upgrade head        # crea las tablas
python -m scripts.seed      # datos base: 1 distribuidor, 3 tiendas sin vincular, 30 productos (sin ventas)
uvicorn app.main:app --reload
```

Con `BOT_MODE=polling` el bot funciona desde tu computador, sin desplegar nada.

- **Bot:** primero registra tu celular en el panel (Tiendas → una tienda → Acceso al bot). Luego abre tu bot en Telegram, escribe `/start` y toca 📱 Compartir mi número.
- **Datos reales:** el seed no inventa ventas, conteos ni pedidos. Todo lo que muestra el panel llega desde Telegram, y el panel se actualiza solo (indicador "En vivo"). Volver a correr el seed borra los datos del distribuidor, también los celulares registrados.
- **Panel:** entra a http://localhost:8000/panel con `demo@demo.co` y la contraseña de `DEMO_PASSWORD`.
- **Pruebas:** `python -m pytest`

### Variables de entorno

| Variable | Para qué |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot de @BotFather |
| `BOT_MODE` | `polling` (local) o `webhook` (servidor) |
| `PUBLIC_BASE_URL`, `TELEGRAM_WEBHOOK_SECRET` | Solo en modo webhook |
| `DATABASE_URL` | Conexión a PostgreSQL |
| `GROK_API_KEY` | API key del reto |
| `GROK_BASE_URL` | Por defecto `https://api.reto.pltk.mx/v1` |
| `GROK_MODEL` | Por defecto `grok-4.20-0309-non-reasoning` |
| `SESSION_SECRET` | Firma de la sesión del panel |
| `DEMO_PASSWORD` | Contraseña del usuario de prueba del panel |

Las claves van solo en `.env`, que está en `.gitignore`. Nunca se suben al repositorio.

## Reglas que cumple

- La IA solo extrae datos; ningún cálculo lo hace la IA.
- Toda extracción se confirma con botones antes de guardarse.
- Antes de usar el bot, el tendero acepta el tratamiento de sus datos personales (Ley 1581 de 2012).
- Cada tienda y cada distribuidor ven solo sus propios datos.
- Las fotos y los audios se procesan y se descartan; no se guardan.

## Estado

**Semana 1 — flujo de punta a punta:** listo. El agente recibe voz, texto o foto, llama a Grok, procesa la respuesta y entrega un resultado (resumen para confirmar, inventario, alertas y pedido).

**Pendiente:**

- Despliegue con Docker en Render y UptimeRobot para que no se apague.
- Mejorar los datos de prueba (el producto que crece) y la detección de ventas escritas sin la palabra "vendí".
- Aviso al distribuidor cuando el tendero envía un pedido; hoy queda registrado en el panel.
- Las herramientas, ejecuciones y casos de evaluación de las siguientes semanas del reto.
