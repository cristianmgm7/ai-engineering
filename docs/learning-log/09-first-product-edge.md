# 09 · El primer edge de producto (`edges/` + un canal de voz)

> **Nota:** el edge de producto que describe esta nota (un canal de mensajes de
> voz) se retiró del árbol en el pivote a WhatsApp (oct 2026). La parte kernel
> (`edges/channels.py`, `ingress.py`, `worker.py`) sigue intacta; lo aprendido
> aquí es la base del edge de WhatsApp.

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Si el edge solo traduce (bytes firmados → evento estándar, y resultado → texto
> hablado) y todo lo demás ya vive en el kernel, entonces conectar un canal nuevo
> es escribir un adapter y un responder, y la seguridad del webhook (firma, eco,
> duplicados, quién aprueba) se puede probar de punta a punta sin modelo real.

## Qué hace

**Kernel (`edges/`)**
- `channels.py`: el contrato del canal. `parse_inbound(InboundRequest) →
  RoutedEvent | None` (`None` = auténtico pero ignorado), `send(OutboundMessage)`,
  y las excepciones `Unauthorized` (401) / `BadRequest` (400).
- `ingress.py`: `POST /webhooks/{channel}/{hook}` con FastAPI. El adapter verifica
  el body crudo, se comprueba el agente, se encola y se responde 200 rápido. 503
  si la cola falla, para que el canal reintente.
- `worker.py`: resuelve el agente; si hay una aprobación esperando y lo que se dijo
  es un sí/no claro, es una decisión (`TurnService.decide`), si no, un turno
  (`TurnService.handle`); el `Responder` decide qué decir y el adapter lo envía.
  `InProcessQueue` corre cada evento como tarea en segundo plano.

**Producto (el edge retirado)**
- `adapter.py`: dos modos de ingreso. Modo A: firma HMAC-SHA256 hex del body
  crudo en un header. Modo B: suscripción de eventos con `Authorization: Bearer`,
  donde solo el evento "mensaje publicado" corre el agente. Ignoraba el eco del
  propio agente y los mensajes sin texto. Sin secreto configurado rechazaba todo.
  Enviaba por el endpoint de salida del canal con `Idempotency-Key`.
- `replies.py`: sí/no hablado, estricto (solo una frase corta que sea solo eso).
- `responder.py`: el texto de aprobación desde la llamada exacta, y los fallbacks
  hablados (límite, rechazo, fallo).
- `app.py`: el composition root.

## Doc de tb-agent

10 (webhook ingress) y 17 (outbound).

## En qué difiere de la versión TS

- La referencia encola en Bull/Redis y el worker es un proceso aparte. Acá la cola
  es en proceso (`InProcessQueue`) detrás del puerto `EventQueue`; Redis va detrás
  del mismo puerto.
- La referencia decide el sí/no hablado con un modelo (un reply-classifier, con su
  suite de evals). Acá es una regla estricta: barata y predecible, pero "sí, pero a
  las 2" no cuenta como sí.
- Los taps en la tarjeta de aprobación todavía no: el evento se aceptaba y se
  ignoraba. Solo se aprobaba por voz.
- La referencia responde con una tarjeta de permiso más el texto hablado; acá solo
  el texto.

## Decisiones y tradeoffs

- **La decisión se guarda con el id del evento del "sí".** Si el canal reentrega
  ese webhook, la segunda vez ya no hay aprobación esperando y correría como un
  turno normal. Con el mismo `event_id`, `TurnService` lo ve como duplicado.
- **`waiting()` es por persona y sesión.** El "sí" de otro miembro del canal no
  encuentra la aprobación ajena; corre como un turno normal suyo (hay un test).
- **El fallback de error se dice en voz alta.** Con la cola en proceso ya se
  respondió 200 al canal, así que no va a reintentar. Con una cola real que sí
  reintenta, habría que no decir nada hasta el último intento.
- **El adapter es síncrono al parsear.** Verificar y normalizar no necesita I/O;
  resolver el agente lo hace el ingress con el directorio.
- **Sin tools reales todavía.** El agente solo conversaba. El primer conector trae
  las tools y la `Policy` de sender-scoping.

## Preguntas abiertas (heredadas por el edge de WhatsApp)

- El mensaje de error y la respuesta real comparten `Idempotency-Key` (el id del
  mensaje entrante). Si una cola con reintentos manda primero el error y luego la
  respuesta, el canal descartaría la segunda. ¿Clave distinta para el error?
- ¿Protección contra replay (timestamp + ventana) además del HMAC? (tb-agent 10).
- ¿Cuándo pasar el sí/no a un clasificador con modelo, y con qué eval?
- ¿Aprobaciones por botones/tarjeta por el mismo `decide()`? (En WhatsApp: reply
  buttons.)
