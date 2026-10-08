# Correr el bot contra WhatsApp de verdad (estación 8)

El código ya está completo; esta estación es configuración. Orden exacto:

## 1. La app de Meta (una vez)

1. [developers.facebook.com](https://developers.facebook.com) → **My Apps →
   Create App** → caso de uso/tipo **Business** → añade el producto
   **WhatsApp**.
2. En **WhatsApp → API Setup** Meta te regala un entorno de prueba:
   - un **número de prueba** con su **Phone number ID** (cópialo),
   - un **token temporal** (~24 h; para algo duradero: System User token en
     Business Settings),
   - hasta **5 destinatarios de prueba**: añade tu número personal en "To"
     (te llega un código por WhatsApp).
3. El **App Secret** está en **App Settings → Basic** (firma los webhooks).
4. El **Verify token lo inventas tú** (cualquier string); es el secreto
   compartido del handshake GET.

## 2. El `.env`

```
ANTHROPIC_API_KEY=sk-ant-...
WHATSAPP_PHONE_NUMBER_ID=<Phone number ID del paso 1>
WHATSAPP_ACCESS_TOKEN=<token del paso 1>
WHATSAPP_APP_SECRET=<App Secret>
WHATSAPP_VERIFY_TOKEN=<el que inventaste>
DATABASE_PATH=agent.db              # opcional: estado que sobrevive reinicios
LANGFUSE_PUBLIC_KEY=pk-lf-...       # opcional: trazas reales
LANGFUSE_SECRET_KEY=sk-lf-...
```

## 3. Servidor + túnel (dos terminales)

```bash
uv run uvicorn agent.edges.whatsapp.app:create_app --factory --port 8000
```

```bash
cloudflared tunnel --url http://localhost:8000   # o: ngrok http 8000
```

Copia la URL pública (`https://<algo>.trycloudflare.com`).

## 4. Registrar el webhook (aquí dispara el handshake)

En **WhatsApp → Configuration → Webhook → Edit**:

- **Callback URL**: `https://<túnel>/webhooks/whatsapp/<PHONE_NUMBER_ID>`
- **Verify token**: el tuyo
- **Verify and save** → en ese instante Meta hace el `GET`; si el servidor
  está arriba y el token cuadra, queda verificado (verás el request en los
  logs de uvicorn).
- Luego **Manage** → suscríbete al campo **`messages`** (solo ese).

## 5. Probar

Desde tu WhatsApp personal, escríbele al número de prueba:

1. "Hola, ¿qué pedidos tengo?" → debe contestar (READ directo).
2. "Quiero 2 tacos al pastor y un agua" → debe pedir tu OK (WRITE parkeado).
3. "sí" → crea el pedido y te lo confirma citando tu mensaje.
4. Si configuraste Langfuse: abre la traza (`agent.run` → `model.generate` /
   `tool.execute`) y audítala — ¿se entiende qué contexto tuvo el agente?

## Si algo falla

| Síntoma | Causa típica |
|---|---|
| "Verify and save" falla | servidor caído, túnel viejo, o verify token distinto al del `.env` |
| 401 en los POST de Meta | `WHATSAPP_APP_SECRET` equivocado (la firma no cuadra) |
| El bot no contesta | ¿suscrito a `messages`? ¿tu número está en los 5 de prueba? mira los logs |
| `send` falla con 401/190 | el token temporal caducó (24 h): regenéralo |
| Responde en frío tras reinicio sin memoria | sin `DATABASE_PATH` el estado muere con el proceso |

Nota: la **ventana de 24 h** no nos afecta — el bot solo responde a mensajes
entrantes, nunca inicia conversaciones (eso pediría plantillas).
