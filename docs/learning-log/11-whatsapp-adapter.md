# 11 · El `WhatsAppAdapter` (`edges/whatsapp/adapter.py` — estación 3)

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Si el primer edge dejó el contrato correcto, entonces conectar WhatsApp es
> solo escribir la traducción: la firma de Meta en vez de la de antes, el
> payload `entry[].changes[].value` en vez del plano, y la Graph API a la
> salida — sin tocar ni una línea del kernel.

## Qué hace

- **`verify`**: el handshake de la estación 1, concretado. `hub.mode` debe ser
  `subscribe`, el `hub.verify_token` se compara en tiempo constante
  (`hmac.compare_digest`), y se devuelve `hub.challenge` crudo. Sin token
  configurado: todo rechazado.
- **`parse_inbound`**: verifica `X-Hub-Signature-256` (`sha256=<hex>` del HMAC
  del body crudo) antes de parsear nada. Luego busca el primer mensaje de texto
  en `entry[].changes[].value.messages[]` cuyo `metadata.phone_number_id` sea el
  `:hook`. Los `statuses` (entregado/leído), los mensajes que no son texto y los
  payloads de otro número se ignoran con `None` (auténticos, 200, sin reintento).
- **El mapeo**: `wa_id` del cliente → `principal_id` y `tenant_id`
  (sender-scoped); el chat 1:1 → `session_id` (el mismo `wa_id`); el
  `wamid...` → `event_id`, que es la clave de idempotencia de `TurnService`.
- **`send`**: `POST {base}/{versión}/{phone_number_id}/messages` con Bearer, y
  si hay `reply_to_event_id` lo manda como `context.message_id` — la respuesta
  cita el mensaje que contesta, algo nativo de WhatsApp que el canal anterior
  no tenía.

## Doc de tb-agent

10 (ingress/firma) y 17 (outbound), traducidos a Meta.

## En qué difiere de la versión TS

- La referencia tenía dos modos de ingreso (directo y suscripción); Meta tiene
  uno solo + el handshake GET. Menos ramas.
- El outbound de la referencia llevaba `Idempotency-Key`; la Graph API no tiene
  idempotencia nativa de envío — el `context.message_id` cita pero no deduplica.
  La dedupe de entrada sigue siendo el `event_id` en `TurnService`.

## Decisiones y tradeoffs

- **Solo el primer mensaje de texto del batch.** Meta puede agrupar bajo carga;
  en la práctica entrega de a uno. Procesar el lote entero requeriría que
  `parse_inbound` devuelva una lista — cambio de contrato de kernel que no se
  justifica todavía.
- **`metadata.phone_number_id != hook` → se ignora, no se rechaza.** Es un
  payload auténtico de Meta; solo que no es para este bot.
- **Texto vacío o solo-media → `None`.** Media (audio: ¡el caso natural del
  futuro!) queda explícitamente fuera de esta rebanada.

## Preguntas abiertas

- ¿Cuándo traer audio? WhatsApp manda `audio.id` y hay que bajar el media por
  la Graph API + transcribir — un `MediaStore` + STT que hoy no existen.
- ¿Replay protection (timestamp + ventana) además del HMAC? (heredada de la 10.)
- Los botones interactivos (`type: "interactive"`, `button_reply`) llegan en la
  estación 4/5 para aprobaciones: ¿se mapean a texto ("sí"/"no") o a un campo
  propio del evento?
