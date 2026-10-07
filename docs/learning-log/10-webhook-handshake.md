# 10 · El handshake de suscripción (`edges/` — estación 1 de WhatsApp)

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Si el handshake es un hook más del contrato `ChannelAdapter` (`verify`), entonces
> el ingress puede responder el reto de Meta sin saber nada de Meta: el kernel pone
> la ruta `GET` y el formato de la respuesta, y cada canal decide cómo validar su
> token — igual que `parse_inbound` ya decide cómo validar su firma.

## Qué hace

- **`channels.py`**: `InboundRequest` gana `query` (Meta manda el reto por query
  string, no por body). `ChannelAdapter` gana `verify(request) -> str | None`:
  el texto exacto a devolver, `RejectedRequest` si el token no cuadra, `None` si
  el canal no usa handshake.
- **`ingress.py`**: `GET /webhooks/{channel}/{hook}` → el adapter verifica y el
  ingress devuelve el challenge como **texto plano** (Meta compara el cuerpo
  crudo). 401 con token malo, 404 para canal desconocido o sin handshake.
- El `POST` ahora también pasa `query`, por simetría.

## Doc de tb-agent

10 (webhook ingress). El doc no contempla un handshake GET — es específico de
Meta — pero sus principios aplican tal cual: autenticidad en el adapter,
comparación en tiempo constante, fail-closed, nunca encolar sin verificar.

## En qué difiere de la versión TS

La referencia no tiene este endpoint: su canal registra webhooks por API, sin
reto de suscripción. Meta en cambio no entrega ni un evento hasta que el GET
devuelve el challenge correcto.

## Decisiones y tradeoffs

- **`verify` es parte del contrato, no un protocolo aparte.** Un canal sin
  handshake devuelve `None` y el ingress responde 404. Explícito y tipado, al
  costo de que cada fake de test declare el método (una línea).
- **El reto no corre el agente ni toca la cola.** Es puro edge: se responde en
  el request y no queda estado.
- **El challenge se devuelve crudo (`text/plain`)**, no como JSON: Meta compara
  el cuerpo byte a byte.

## Preguntas abiertas

- El `hub.verify_token` se compara en el adapter (estación 3): ¿tiempo constante
  también ahí, aunque no sea un secreto criptográfico? (barato: sí)
- ¿Debería el ingress loguear los intentos de handshake fallidos? Hoy solo
  responde.
