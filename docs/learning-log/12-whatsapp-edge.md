# 12 · El edge de WhatsApp completo (`edges/whatsapp/` — estación 4)

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Si el kernel ya tiene Worker, TurnService y ApprovalGate, entonces cerrar el
> edge es solo producto: decidir *qué palabras* usa el bot (responder), *qué
> cuenta como sí/no* (replies) y *cómo se cablea todo* (app). El mismo flujo que
> el primer edge, con texto en vez de voz.

## Qué hace

- **`replies.py` — `YesNoReplies`**: estricto por diseño. Solo un mensaje corto
  que *sea* la respuesta cuenta ("sí", "Sí.", "ok", "mejor no"); se normalizan
  espacios, puntuación y el énfasis de WhatsApp (`*sí*`). "Sí, pero a las 2" →
  `None` → corre como turno normal y el agente maneja el matiz.
- **`responder.py` — `TextResponder`**: las palabras del producto. Fallbacks
  por `RunStop`, la pregunta de aprobación construida desde la llamada parkeada
  exacta ("Responde *sí* para continuar o *no* para cancelar"), los rechazos de
  decisión, y **todo recortado a 4096 caracteres** (límite duro de WhatsApp).
- **`app.py` — composition root**: el cableado completo, calcado del patrón del
  primer edge: modelo → loop → turns → worker → ingress. El agente se registra
  bajo el `phone_number_id`. Estado en memoria; tools: ninguna todavía.

## Doc de tb-agent

11 (worker), 17 (outbound) — ya absorbidos por el kernel; esta estación fue
casi puro producto.

## En qué difiere de la versión TS

- La referencia clasifica el sí/no con un modelo (reply-classifier + evals);
  acá sigue la regla estricta, ahora para chat escrito. El puerto
  `ReplyClassifier` queda listo para el upgrade.
- El responder de voz prohibía listas y markdown; el de texto permite énfasis
  de WhatsApp con moderación y agrega el límite de 4096.

## Decisiones y tradeoffs

- **Las palabras del cliente están en español**; las instrucciones del agente
  piden responder en el idioma del cliente. Si el negocio fuera multilenguaje
  de verdad, los fallbacks tendrían que localizarse por sesión.
- **El recorte a 4096 es a lo bruto** (… al final). Partir en varios mensajes
  sería más fiel; no se justifica hasta ver respuestas tan largas en la práctica.
- **El end-to-end de `test_app.py` solo fakea el modelo y el transporte HTTP**:
  webhook firmado → razona → responde por Graph API, con la firma, la cola, el
  worker y el responder reales. Es el test que define "el edge funciona".

## Preguntas abiertas

- Botones interactivos para aprobar (reply buttons) en vez de "sí/no" escrito:
  entra por `parse_inbound` (`type: "interactive"`) y el mismo `decide()`.
- ¿Cuándo duele la regla estricta del sí/no? Medirlo antes de traer el
  clasificador con modelo.
- La ventana de 24 h de WhatsApp: para un bot que solo responde no afecta, pero
  si el agente algún día inicia conversaciones, necesita plantillas.
