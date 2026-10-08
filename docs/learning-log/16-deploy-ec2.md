# 16 · Deploy en EC2 (estación 9)

## Hipótesis (borrador de Claude: reescríbela con tus palabras)

> Si el servidor ya era fail-closed por diseño (sin secrets, rechaza todo),
> entonces se puede desplegar *antes* de tener las credenciales del canal: la
> infraestructura queda lista y probada, y activar WhatsApp será editar un
> `.env` y reiniciar — no un despliegue nuevo.

## Qué se hizo

- **Imagen**: Dockerfile multi-stage — builder con uv contra el lockfile
  (capa de deps cacheada aparte), runtime `python:3.12-slim` sin uv y sin
  root, `HEALTHCHECK` contra `/health`, `DATABASE_PATH=/data/agent.db` para
  el volumen.
- **Ajustes que el deploy destapó**: `create_ingress` ganó `on_shutdown`
  (lifespan); el composition root drena la cola, hace flush de Langfuse y
  cierra el cliente HTTP al apagar. `logging.basicConfig` desde `LOG_LEVEL`
  en el factory de uvicorn (no en `build()`, para no tocar logging en tests).
- **Infra (AWS, por CLI — parte del aprendizaje)**: usuario IAM admin (las
  access keys de root se crearon por error y se eliminaron — lección número
  uno de AWS), t3.micro Ubuntu 24.04 con IMDSv2 obligatorio, créditos CPU
  `standard` (los t3 "unlimited" facturan excedentes en silencio), disco gp3
  cifrado, security group con SSH solo desde una IP y 80/443 abiertos,
  Elastic IP.
- **TLS**: Meta exige HTTPS con certificado válido → Caddy como sidecar en un
  `docker-compose.override.yml` del servidor (no commiteado: lleva el dominio),
  subdominio gratis de DuckDNS. Caddy consigue y renueva Let's Encrypt solo.

## En qué difiere de la versión TS

La referencia se despliega con ECR + Helm + Argo CD (mergear ES desplegar) y
los secrets viven en AWS Secrets Manager. Acá: `git pull && docker compose up
-d --build` y un `.env` en el servidor. Mismo principio (imagen inmutable,
config por entorno), dos órdenes de magnitud menos de maquinaria — apropiado
para un proceso, un servidor.

## Decisiones y tradeoffs

- **EC2 + compose, no App Runner/Fargate**: lo stateless habría forzado
  Postgres ya; el SQLite en volumen vive feliz en el disco de la instancia, y
  EC2 enseña las primitivas reales (SG, key pairs, Elastic IP, IMDS).
- **`ports: !reset []`** en el override: solo Caddy mira al mundo (y el SG
  bloquea 8000 de todos modos — defensa en capas).
- **Secrets por `scp` del `.env` local**, nunca por chat ni por git. Pendiente
  de lujo: una API key de Anthropic separada para el servidor, revocable sola.

## Preguntas abiertas

- Despliegue continuo: ¿un cron con `git pull` + compose, o GitHub Actions con
  SSH? (Hoy es manual, y para aprender está bien así.)
- Backups del volumen (`agent-data`): un `sqlite3 .backup` a S3 con cron sería
  la versión mínima digna.
- Monitoreo: el healthcheck de Docker reinicia, pero nadie avisa si el host
  muere — ¿CloudWatch alarm sobre el status check de la instancia?
- La IP de casa cambia → el SSH se cierra solo (el SG es por IP fija). Comando
  a mano para re-autorizar la nueva.
