# 13 · El conector de pedidos y el aislamiento por cliente (estación 5)

El negocio: un restaurante en WhatsApp. El cliente escribe, el agente le toma
el pedido.

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Si las tools se escriben ancladas a `ctx.principal_id` (el cliente no es un
> argumento, es el contexto) y la policy de producto niega cualquier campo de
> cliente ajeno **sobre el input crudo**, entonces el aislamiento queda probado
> por construcción Y por defensa en profundidad — y un "sí" aprobado tampoco
> puede colar un cruce, porque `execute_approved` re-chequea el `Deny`.

## Qué hace

- **`domain/whatsapp/pedidos.py`** (L1): `Pedido` (items en las palabras del
  cliente), el puerto `PedidoStore`, y la regla determinista `rechazo_para`
  (un pedido sin platos no existe). Las reglas de negocio viven en código, no
  en el prompt.
- **`adapters/whatsapp/pedidos.py`** (L3): `InMemoryPedidoStore` +
  `pedidos_tools()` — `pedidos__listar` (READ) y `pedidos__crear` (WRITE).
  Cada llamada al store va con `ctx.principal_id`; **no existe argumento para
  elegir cliente**.
- **`adapters/whatsapp/policy.py`** (L3): `CustomerScoped` — niega si el input
  crudo trae `customer_id`/`cliente_id`/`wa_id` de otro. Corre antes de que la
  validación Pydantic descarte campos desconocidos: el intento se vuelve un
  `Deny` legible para el modelo, no un argumento ignorado en silencio.
- **`app.py`**: `AllOf(CustomerScoped(), ConfirmWrites())` y el conector como
  tools por defecto del producto (`tools=[]` da un agente pelado).

## Doc de tb-agent

15 (executeTool), 27 (tenant isolation). La meta del CLAUDE.md "default
sender-scoped isolation — prove it with negative tests": esta estación.

## En qué difiere de la versión TS

La referencia aísla por cuentas de conector (el conector del sender, con sus
credenciales); acá el "conector" es el store de pedidos y el scoping es por
registro. Mismo principio, un nivel menos de indirección — las credenciales
por cliente llegarían con un conector externo real.

## Decisiones y tradeoffs

- **Primero fue "citas", se cambió a pedidos antes de commitear**: cuando un
  cliente le escribe a un restaurante, lo que está haciendo es un pedido. El
  intercambio costó un rename + reescritura — el kernel ni se enteró, que era
  el punto de todo el diseño.
- **Store en memoria, no SQLite** (el CLAUDE.md decía SQLite aquí): un
  componente a la vez — la persistencia es la estación 6, y el puerto
  `PedidoStore` ya está definido para recibirla.
- **Doble capa de aislamiento a propósito.** Por construcción (las tools no
  aceptan cliente) bastaría; la policy existe para que cruzar sea un `Deny`
  arquitectónico, no una convención de quien escribió la tool. El test clave:
  ni siquiera una llamada *aprobada* con `customer_id` ajeno pasa el re-chequeo.
- **La validación vacía tiene dos pisos**: `min_length=1` en el schema (el
  modelo ni puede mandar `[]`) y `rechazo_para` en el dominio (los items en
  blanco). Schema para la forma, dominio para el significado.

## Preguntas abiertas

- **El menú y los precios.** Hoy los items son texto libre; un `Catalog` en el
  dominio permitiría validar platos, calcular totales (determinista, jamás el
  modelo) y rechazar lo que no existe.
- **El ciclo de vida del pedido** (recibido → preparando → entregado) y
  `pedidos__cancelar` — la primera tool donde la *propiedad* del registro se
  chequea en el handler (el id podría ser ajeno): buen test negativo adicional.
- ¿El restaurante (tenant) vs. el cliente (principal)? Hoy tenant = sender;
  con multi-restaurante el tenant pasaría a ser el negocio.
