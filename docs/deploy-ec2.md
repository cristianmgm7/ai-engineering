# Desplegar en AWS EC2 (estación 9)

La imagen ya existe (`Dockerfile` + `docker-compose.yml`); esto es ponerla en
una instancia con IP estable y HTTPS válido — **Meta exige HTTPS con
certificado real** para el webhook, así que el TLS no es opcional.

Ruta: cuenta AWS → EC2 + Elastic IP → DNS → Docker → Caddy (TLS automático) →
compose arriba. Los pasos 1–4 son de consola web (tuyos); del 5 en adelante es
terminal y puede ir guiado.

## 1. La cuenta AWS (una vez, ~15 min)

1. [aws.amazon.com](https://aws.amazon.com) → Create account (email + tarjeta;
   el free tier cubre esto el primer año).
2. **Activa MFA en la cuenta root inmediatamente** (IAM → Security
   credentials). La root no se vuelve a usar para el día a día.
3. IAM → crea un usuario (p. ej. `cristian-admin`) con `AdministratorAccess` y
   MFA; entra siempre con ese.
4. Región sugerida: **us-east-1** (la estándar/barata; Colombia no tiene región).

## 2. La instancia

EC2 → **Launch instance**:

- AMI: **Ubuntu Server 24.04 LTS**
- Tipo: **t3.micro** (free tier 750 h/mes el primer año; después ~US$8/mes)
- **Key pair**: crea uno y guarda el `.pem` (sin él no hay SSH)
- **Security group**: SSH (22) *solo desde "My IP"* · HTTP (80) y HTTPS (443)
  desde anywhere (Caddy los necesita para Let's Encrypt y el webhook)
- Storage: 20 GB gp3

Luego **Elastic IP** → Allocate → Associate a la instancia (IP fija aunque
reinicies; gratis mientras esté asociada).

## 3. DNS

Meta no acepta IPs: necesitas un nombre. Dos caminos:

- **Dominio propio** (si tienes): un registro `A` → la Elastic IP.
- **Gratis**: [duckdns.org](https://www.duckdns.org) → `tuagente.duckdns.org`
  → la Elastic IP. Let's Encrypt lo acepta sin problema.

## 4. Entrar y preparar la instancia

```bash
chmod 400 ~/Downloads/tu-key.pem
ssh -i ~/Downloads/tu-key.pem ubuntu@<ELASTIC_IP>
```

En la instancia — Docker oficial + compose:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && exit   # vuelve a entrar para que aplique
```

## 5. El código y los secrets

```bash
git clone https://github.com/cristianmgm7/ai-engineering.git
cd ai-engineering
nano .env   # pega aquí los secrets (ANTHROPIC_API_KEY, WHATSAPP_*, LANGFUSE_*)
```

Los secrets viven **solo** en ese `.env` del servidor. Nunca en git.

## 6. TLS con Caddy (reverse proxy delante del agente)

`Caddyfile` en la raíz del repo clonado:

```
tuagente.duckdns.org {
    reverse_proxy agent:8000
}
```

Y un override `docker-compose.override.yml` (compose lo carga solo):

```yaml
services:
  agent:
    ports: []          # el agente deja de exponerse directo
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
volumes:
  caddy-data:
```

Caddy consigue y renueva el certificado solo. (Alternativa sin dominio:
`cloudflared tunnel` corriendo en la instancia — HTTPS gratis, pero dependes
del túnel.)

## 7. Arriba

```bash
docker compose up -d --build
docker compose logs -f agent     # Ctrl-C para salir de los logs
curl https://tuagente.duckdns.org/health   # → {"ok":true}
```

El webhook para la consola de Meta queda:
`https://tuagente.duckdns.org/webhooks/whatsapp/<PHONE_NUMBER_ID>`

## Operación diaria

| Qué | Cómo |
|---|---|
| Logs | `docker compose logs -f agent` |
| Actualizar | `git pull && docker compose up -d --build` |
| Estado | `docker compose ps` · los datos sobreviven en el volumen `agent-data` |
| Apagar sin perder nada | `docker compose down` (el volumen queda) |

Sin credenciales de WhatsApp en el `.env`, el servidor corre **fail-closed**:
sano en `/health`, 401 a todo webhook — listo para activarse cuando Meta
coopere.
