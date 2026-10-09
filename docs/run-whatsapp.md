# Correr el bot contra WhatsApp de verdad (estación 8)

El código ya está completo; esta estación es configuración. Hecha el
2026-10-08 contra el servidor de EC2 (`docs/deploy-ec2.md`), así que **no hace
falta túnel**: Caddy ya da el HTTPS que Meta exige. Orden exacto:

## 1. La app de Meta (una vez)

1. [developers.facebook.com](https://developers.facebook.com) → **Mis apps →
   Crear app**. El asistente tiene 5 pasos: detalles, casos de uso, negocio,
   requisitos, resumen.
2. **Caso de uso**: "Conectarte con los clientes a través de WhatsApp".
3. **Negocio**: Meta exige un **portfolio comercial**. Si no tienes ninguno,
   "crea uno nuevo" (nombre + tu nombre y apellido + correo). Puede quedar **sin
   verificar**: basta para el número de prueba; la verificación solo hace falta
   para producción.
4. **Crear app** acepta las Condiciones de la plataforma de Meta.
5. Al abrir **Casos de uso → Personalizar → Paso 1. Pruébalo**, "Continuar"
   acepta las condiciones de WhatsApp Business y crea el **número de prueba**
   gratuito (hasta 5 destinatarios). Ahí mismo salen:
   - el **Phone number ID** y el **WhatsApp Business Account ID** (WABA),
   - el **token temporal** ("Generar token", ~24 h),
   - el selector de **destinatario**: añade tu número personal (te llega un
     código por WhatsApp).
6. El **App Secret** NO está en la pantalla de WhatsApp: está en
   **Configuración de la app → Básica → Clave secreta de la app → Mostrar**
   (puede pedir tu contraseña).
7. El **Verify token lo inventas tú**: `openssl rand -hex 24`.

## 2. El `.env`

```
ANTHROPIC_API_KEY=sk-ant-...
WHATSAPP_PHONE_NUMBER_ID=<Phone number ID>
WHATSAPP_ACCESS_TOKEN=<token>
WHATSAPP_APP_SECRET=<App Secret>
WHATSAPP_VERIFY_TOKEN=<el que inventaste>
DATABASE_PATH=agent.db              # opcional: estado que sobrevive reinicios
LANGFUSE_PUBLIC_KEY=pk-lf-...       # opcional: trazas reales
LANGFUSE_SECRET_KEY=sk-lf-...
```

⚠️ **Sin comentarios al final de la línea.** `.env.example` los trae
(`# remitente del bot…`) y si los copias tal cual pasan a formar parte del
valor: el Phone ID mide 70 caracteres en vez de 16 y nada cuadra.
Descomenta la línea de `WHATSAPP_VERIFY_TOKEN`, que viene comentada.

## 3. Llevar los secretos al servidor

Los secretos viven en el `.env` local y en el del servidor; nunca en git ni en
el chat. Este comando copia solo las líneas `WHATSAPP_*` (quitando comentarios
finales) y recrea el contenedor:

```bash
grep -E '^WHATSAPP_[A-Z_]+=' .env | sed -E 's/[[:space:]]+#.*$//' \
  | ssh -i ~/.ssh/ai-engineering.pem ubuntu@<ELASTIC_IP> \
    'cd ~/ai-engineering && sed -i "/^WHATSAPP_/d" .env \
     && { [ -n "$(tail -c1 .env)" ] && echo; cat >> .env; } \
     && docker compose up -d'
```

Es el mismo comando para **refrescar el token** cuando caduque. Comprueba el
handshake desde el servidor antes de ir a Meta (200 con el token bueno, 401 con
uno malo):

```bash
curl -s -w " [%{http_code}]\n" \
  "https://<dominio>/webhooks/whatsapp/<PHONE_NUMBER_ID>?hub.mode=subscribe&hub.verify_token=<TOKEN>&hub.challenge=777"
```

Si SSH da timeout: tu IP de casa cambió; autoriza la nueva en el security
group (puerto 22) y revoca la vieja.

## 4. Registrar el webhook (aquí dispara el handshake)

En la interfaz actual: **Casos de uso → Personalizar → Paso 2: Configuración de
producción → Configurar webhooks** (el menú "WhatsApp → Configuration" de la
interfaz antigua ya no existe).

- **URL de devolución de llamada**:
  `https://<dominio>/webhooks/whatsapp/<PHONE_NUMBER_ID>`
- **Token de verificación**: el tuyo → **Verificar y guardar**. Meta hace el
  `GET`; lo verás en los logs (`docker compose logs agent`).
- Meta **se suscribe sola** a los campos (incluido `messages`); no hay que
  marcarlos a mano. Verifica con la Graph API (ver abajo).

## 5. Suscribir la app a la cuenta de WhatsApp (paso que falta en la UI)

Que el webhook esté verificado **no basta**: la app también debe estar
suscrita a tu WABA, o Meta nunca te reenvía los mensajes entrantes (síntoma:
GET de verificación OK pero cero POST). Comprobar y arreglar desde el
servidor, con el token del `.env`:

```bash
# ¿está suscrita? debe listar tu app, no solo "WA DevX Webhook Events 1P App"
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://graph.facebook.com/v21.0/<WABA_ID>/subscribed_apps"

# suscribirla (reversible con DELETE)
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  "https://graph.facebook.com/v21.0/<WABA_ID>/subscribed_apps"
```

Campos suscritos a nivel de app (debe incluir `messages`):

```bash
curl -s "https://graph.facebook.com/v21.0/<APP_ID>/subscriptions?access_token=<APP_ID>|<APP_SECRET>"
```

## 6. Probar

1. **Abre la conversación**: en Paso 1 envía el mensaje de plantilla
   `hello_world` a tu número (botón "Enviar mensaje"; requiere el token
   generado en esa pantalla). No pasa por tu bot.
2. Desde tu WhatsApp personal, responde al número de prueba:
   1. "Hola, ¿qué pedidos tengo?" → debe contestar (READ directo).
   2. "Quiero 2 tacos al pastor y un agua" → debe pedir tu OK (WRITE parkeado).
   3. "sí" → crea el pedido y te lo confirma citando tu mensaje.
3. Si configuraste Langfuse: abre la traza (`agent.run` → `model.generate` /
   `tool.execute`) y audítala — ¿se entiende qué contexto tuvo el agente?

### Regresión de aislamiento (cada vez que cambie el agente o sus tools)

`scripts/isolation_live.py` hace pasar a un cliente falso (`wa_id` que no posee
nada) por tres ataques contra el bot real: ver sus propios pedidos, leer los de
otro cliente y crear uno a nombre de otro. Firma webhooks válidos con el App
Secret, así que corren el modelo y la política de verdad (cuesta centavos).
Se ejecuta *dentro* del contenedor, que ve el webhook, el secret y la base:

```bash
ssh -i ~/.ssh/<llave>.pem ubuntu@<ip> 'cd ~/ai-engineering && docker compose exec -T agent python -' \
  < scripts/isolation_live.py
```

Sale con `0` si el aislamiento aguantó, `1` si hubo fuga o escritura y `2` si es
inconcluso (el bot no contestó, o no hay pedidos que atacar). Siempre borra las
filas del cliente falso. Las respuestas al número inventado fallan con el error
`131030` de Meta (no es destinatario): es lo esperado y se ve en los logs como
`reply not delivered`.

## El token: temporal vs. permanente

El token temporal de la consola ("Generar token" en Paso 1) dura ~24 h. Recibir
mensajes no lo usa (eso firma con el App Secret); **responder sí**. Al caducar
los mensajes siguen llegando pero el envío falla con 401 / error 190. Sirve
para la primera prueba; para dejar el bot corriendo usa el permanente.

### Token permanente (System User) — el estándar

Un token de **System User** no caduca, solo permite lo que le asignes y se
revoca cuando quieras. Se crea una vez:

1. [business.facebook.com](https://business.facebook.com) → **Configuración →
   Usuarios → Usuarios del sistema → Agregar**. Nombre `agent-bot`, rol
   **Employee** (mínimo privilegio). Meta pide aceptar su política de no
   discriminación publicitaria, aunque no hagas anuncios.
2. **Asignar activos** (dos, ambos con acceso *parcial*):
   - **Cuentas de WhatsApp** → tu cuenta → permiso **Mensajes** (Meta añade
     solo "Números de teléfono (solo ver)").
   - **Apps** → tu app → **Desarrollar app**. Sin esto el generador de tokens
     dice "No hay permisos disponibles" (si ya la asignaste y lo sigue
     diciendo, recarga la página: queda cacheado).
3. **Generar token** → app → caducidad **Nunca** → permiso
   `whatsapp_business_messaging` → **Generar token**. Se muestra **una sola
   vez**: cópialo con el botón (nunca por el chat).
4. Al `.env` local sin mostrarlo, y al servidor (sección 3):

   ```bash
   sed -i '' -E "s|^WHATSAPP_ACCESS_TOKEN=.*|WHATSAPP_ACCESS_TOKEN=$(pbpaste)|" .env && pbcopy </dev/null
   ```

5. Comprobar desde el servidor que es el bueno (`type: SYSTEM_USER`,
   `expires_at: 0`, `is_valid: true`):

   ```bash
   curl -s -H "Authorization: Bearer $TOKEN" \
     "https://graph.facebook.com/v21.0/debug_token?input_token=$TOKEN"
   ```

Revocar: en la misma pantalla del usuario → **Revocar tokens**. Solo pide el
permiso de mensajería; si algún día hace falta administrar (suscribir apps,
leer números) usa el token temporal o añade `whatsapp_business_management`.

## Si algo falla

| Síntoma | Causa típica |
|---|---|
| "Verificar y guardar" falla | servidor caído, contenedor aún arrancando (502), o verify token distinto al del `.env` del servidor |
| Handshake OK pero ningún POST llega | la app no está suscrita al WABA (sección 5) |
| 401 en los POST de Meta | `WHATSAPP_APP_SECRET` equivocado (la firma no cuadra) |
| Valores raros / longitudes enormes | comentarios al final de línea copiados de `.env.example` |
| El bot no contesta | ¿tu número está entre los destinatarios de prueba? ¿token caducado? mira los logs |
| `send` falla con 401/190 | el token temporal caducó (24 h): usa el permanente (System User) |
| SSH da timeout | cambió tu IP de casa: actualiza el security group |
| Responde en frío tras reinicio sin memoria | sin `DATABASE_PATH` el estado muere con el proceso |

Nota: la **ventana de 24 h** del cliente no nos afecta — el bot solo responde a
mensajes entrantes, nunca inicia conversaciones (eso pediría plantillas).
La app de Meta sigue **sin publicar**: con el número de prueba y tus
destinatarios autorizados funciona igual; publicarla solo hace falta para
atender a terceros.
