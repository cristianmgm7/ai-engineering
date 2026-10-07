# 14 · Stores SQLite: el estado sobrevive al proceso (estación 6)

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Si los puertos (`SessionStore`, `PendingActionStore`, `PedidoStore`) estaban
> bien definidos, entonces la persistencia es solo escribir implementaciones
> nuevas y cambiar tres líneas del composition root — ni el kernel ni las
> tools se enteran de que ahora hay un archivo .db debajo.

## Qué hace

- **`adapters/stores/sqlite.py`** (kernel): `SqliteSessionStore` y
  `SqlitePendingActions` sobre **aiosqlite**. Las filas guardan el JSON de
  Pydantic (`model_dump_json` / `model_validate_json`) con solo las columnas
  necesarias para indexar (session_id, event_id, principal_id). La unión
  discriminada de `ContentBlock` reconstruye los tipos de bloque sola.
- **`adapters/whatsapp/sqlite.py`** (producto): `SqlitePedidoStore`, tabla
  propia en el mismo archivo, reusando `open_db(path, schema)`. El rowid hace
  de id del pedido.
- **`config.py`**: `DATABASE_PATH` — seteado usa SQLite, sin setear todo queda
  en memoria. El composition root elige con tres ternarios.

## Doc de tb-agent

No hay doc específico; el contrato venía de 22 (confirm-flow: "una aprobación
parkeada debe sobrevivir") y de la separación ports/adapters de la 01.

## En qué difiere de la versión TS

La referencia usa Mongo con repositorios de Nest; acá SQLite con el puerto que
ya existía. Lo importante que sí se copió: **`claim` como find-and-delete
atómico** (`DELETE … RETURNING`) — el in-memory era "atómico" solo porque
asyncio corre una corutina a la vez; en la DB es atómico de verdad, y dos
decisiones concurrentes sobre la misma aprobación siguen dando un solo ganador.

## Decisiones y tradeoffs

- **Conexión corta por operación**, no pool ni conexión compartida: simple y
  correcto para un proceso; si duele, el pool entra detrás del mismo puerto.
  El `executescript(schema)` por conexión es un costo aceptable (IF NOT EXISTS).
- **JSON en la fila, columnas solo para indexar.** El esquema no persigue al
  modelo Pydantic: si `Message` gana un campo, las filas viejas siguen
  validando. El tradeoff: no puedes consultar por dentro del payload desde SQL.
- **El "reinicio" en los tests es una instancia nueva sobre el mismo archivo**
  (`tmp_path`): nada en memoria sobrevive, así que lo que lee la segunda
  instancia vino del disco. Es la prueba de la estación entera.
- **`Depends()` de FastAPI no entró** — y la razón es honesta: en este diseño
  la DB se toca en el worker (fuera del request HTTP; el webhook responde 200
  y encola). `Depends` brilla para recursos por request; aquí no hay ninguno
  todavía. Entrará natural con el primer endpoint que lea la DB (p. ej. un
  `GET /pedidos` de administración).

## Preguntas abiertas

- ¿WAL mode + busy_timeout cuando haya concurrencia real de escrituras?
- La ventana de memoria carga la sesión entera y recorta en Python
  (`WindowMemory`); con sesiones largas convendría un `LIMIT` en SQL.
- Migraciones: hoy `CREATE TABLE IF NOT EXISTS` basta; el día que cambie una
  columna hará falta algo (¿alembic? ¿a mano?).
