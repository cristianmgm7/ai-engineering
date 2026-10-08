# 17 · WhatsApp en vivo (estación 8)

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Si el kernel es agnóstico y el edge ya verifica firma y handshake con tests,
> correr contra Meta de verdad es solo configuración: ningún cambio de código.
> Lo que falle será de la plataforma, no del agente.

## Qué se hizo

- **App de Meta** (`ai-engineering-agent`) con el caso de uso de WhatsApp, un
  portfolio comercial sin verificar y el número de prueba gratuito (5
  destinatarios). El **código no cambió**: la hipótesis se cumplió en lo que
  importa, y todo lo que costó tiempo fue plataforma.
- **Secretos**: `.env` local → `.env` del servidor con un solo comando
  (`grep` + `sed` + `ssh`), sin pasar nunca por el chat ni por git. El token
  nuevo entró con `pbpaste`, sin mostrarse.
- **Webhook** contra el servidor de EC2 (sin túnel: Caddy ya da HTTPS).
  Handshake `GET` verificado, y un `GET` con token malo responde 401.
- **Token permanente**: System User `agent-bot` (rol Employee) con la cuenta
  de WhatsApp (solo *Mensajes*) y la app (*Desarrollar app*) asignadas, scope
  único `whatsapp_business_messaging`, caducidad "Nunca". Comprobado con
  `debug_token` (`SYSTEM_USER`, `expires_at: 0`).
- **Prueba**: mensaje real desde un WhatsApp personal → POST firmado → dos
  llamadas al modelo → respuesta → avisos de estado de Meta (200).

## Lo que mordió (y no estaba en el runbook)

1. **Webhook verificado ≠ webhook recibiendo.** El `GET` de verificación
   pasó, pero ningún `POST` llegaba: la app no estaba suscrita a la cuenta de
   WhatsApp. `GET /{waba}/subscribed_apps` solo listaba una app interna de
   Meta; se arregló con un `POST` al mismo path.
2. **Los comentarios de `.env.example` se copian como parte del valor.** El
   Phone ID medía 70 caracteres. Una sola línea con `# …` al final basta.
3. **El generador de tokens pide la app, no solo la cuenta.** "No hay
   permisos disponibles" se resolvió asignando también la app al System
   User... y recargando la página (el estado quedó cacheado).
4. **La interfaz de Meta ya no es la del runbook**: el menú "WhatsApp →
   Configuration" no existe; el webhook vive en el Paso 2 del caso de uso.
5. **Carrera con el despliegue continuo**: mergear a `main` dispara el
   autodeploy a los ~2 minutos; un `docker compose up -d` manual a la vez
   dejó el contenedor con nombre prefijado (inofensivo).

## En qué difiere de la versión TS

`cv-agents` autentica contra Carbon Voice con un PAT. Meta separa tres
secretos con tres propósitos: **App Secret** (firma de los webhooks entrantes),
**verify token** (handshake, lo inventas tú) y **access token** (salientes).
Recibir y responder dependen de secretos distintos, y por eso el bot puede
oír sin poder hablar cuando caduca el token.

## Decisiones y tradeoffs

- **Mínimo privilegio en el System User**: Employee, activos con acceso
  parcial, un solo scope. Costo: para suscribir apps o leer números hay que
  usar otro token o ampliar permisos.
- **Sin CLI de terceros** con el App Secret: los `curl` a la Graph API bastan
  y se auditan de un vistazo (queda pendiente un `scripts/meta.sh`).
- **App sin publicar**: suficiente para el número de prueba; publicar solo
  hace falta para atender a terceros.

## Preguntas abiertas

- ¿Por qué la app no se suscribe sola a la cuenta en el flujo nuevo? ¿Es
  diseño, o lo hace el botón "Probar webhooks" y nos lo saltamos?
- Qué cambia al **publicar** la app: ¿los avisos de producción llegan solo
  entonces? Con la app sin publicar el mensaje real sí llegó; entender dónde
  está la frontera.
- Número propio: verificación del negocio, método de pago y ventana de 24 h
  (mensajes iniciados por la empresa exigen plantillas).
- El `.env` en el servidor con el token permanente: ¿Secrets Manager? ¿rotación
  periódica aunque no caduque?
- Los `POST` repetidos de Meta (estados enviado/entregado/leído) y los
  reintentos: ¿el ingress es realmente idempotente ante duplicados?
- En los logs vi las llamadas a Anthropic pero no la de envío a la Graph API:
  ¿falta un log del envío, o es solo el nivel del logger?
