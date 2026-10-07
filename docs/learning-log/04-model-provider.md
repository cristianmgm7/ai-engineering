# 04 · ModelProvider (`adapters/models/anthropic.py`)

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Si todo lo que el agente le dice al modelo pasa por un `ModelRequest` neutral y
> todo lo que el modelo devuelve llega como un `ModelResponse` neutral, entonces el
> core nunca importa `anthropic`, cambiar de proveedor es escribir un archivo nuevo,
> y el loop se puede testear con un fake de 20 líneas. Lo que se pierde en la
> traducción (bloques de thinking) tiene que viajar intacto, o el segundo paso de
> un loop con tools falla.

## Qué hace

`AnthropicModelProvider.generate(ModelRequest) → ModelResponse`. Traduce mensajes,
tools y system a los params de `messages.create`, y la respuesta del SDK a tipos
neutrales: `Message` (rol assistant), `StopReason`, `Usage` (incluye tokens de
caché) y el modelo que respondió. Es el único módulo que importa `anthropic`; el
test de arquitectura lo garantiza para `core/` y `domain/`.

## Doc de tb-agent

Ninguno dedicado: tb-agent solo dice "thin in-house `ModelProvider`, Anthropic now,
OpenAI-compatible later". El contrato viene de la arquitectura de referencia (L0).

## En qué difiere de la versión TS

- la referencia (`AnthropicModelProvider`, token `MODEL_PROVIDER`) devuelve un
  `ModelResult` con los tipos del SDK. Acá el `ModelResponse` es neutral: el loop
  no sabe qué es un `anthropic.types.Message`.
- la referencia mete el prompt caching dentro del provider (`cache: true` pone tres
  breakpoints). Acá todavía no: llega con `ContextBuilder` (componente 7), que es
  quien sabe qué parte del prompt es estable.
- Los errores del SDK se traducen a `ModelProviderError(retryable=...)`. El core
  puede decidir qué hacer con un 429 sin importar el SDK.

## Decisiones y tradeoffs

- **`OpaqueBlock` en vez de modelar `thinking`.** El modelo por defecto
  (`claude-sonnet-5`) piensa de forma adaptativa, así que una respuesta con
  `tool_use` puede traer bloques `thinking` con firma. La API exige devolver ese
  turno **sin cambios** en el siguiente request. En vez de modelar cada tipo de
  bloque del proveedor, el provider los envuelve tal cual (`raw`) y los reenvía
  verbatim; los de otro proveedor se descartan. Hay un test de ida y vuelta que lo
  prueba.
- **Sin reintentos propios.** El SDK ya reintenta 408/409/429/5xx y errores de
  conexión (2 veces, backoff exponencial). Un segundo nivel de reintentos
  multiplicaría la espera.
- **`max_tokens` por defecto 16000** (antes 1024). Con thinking activo, el
  razonamiento cuenta contra ese límite. Con 1024 el modelo podía cortarse antes de
  responder (`stop_reason: max_tokens`). Para respuestas de voz cortas, el control
  de longitud es el prompt, no el tope.
- **`refusal` es un `StopReason` propio.** Los modelos actuales tienen
  clasificadores de seguridad; el loop debe distinguir "se negó" de "terminó".
- **Cliente inyectado** (`AnthropicModelProvider(client)`), con
  `from_settings()` como fábrica. Los tests usan un cliente falso sin red.
- **Tests live opt-in** (`uv run pytest -m live`): cuestan dinero, así que el run
  normal los excluye.

## Revisión de estructura

Primero vivía en `platform/anthropic_provider.py`. Lo moví a `adapters/models/`:
`platform/` es la capa más interna y de la que todo depende, así que debe ser la
más estable, y el código de un vendor es lo menos estable que hay. El puerto
(`ModelProvider`) y los tipos neutrales siguen en `platform/model.py`; la
implementación es un adapter. tb-agent pone el provider en L0. Acá me separo de
eso por su propia regla de dependencias.

## Preguntas abiertas

- ¿Subir el modelo por defecto a `claude-sonnet-5-5`? Mismo precio, generación más
  nueva, pero cambia cosas (no se puede apagar thinking con `disabled`, sin
  `tool_choice` forzado). Hoy no usamos ninguna de las dos.
- ¿Dónde vive `effort` (`output_config.effort`)? Probablemente en `AgentSpec`, por
  agente: las respuestas de voz cortas podrían ir en `low`.
- ¿Streaming? No hace falta para un turno de voz, pero sí si `max_tokens` sube mucho.
