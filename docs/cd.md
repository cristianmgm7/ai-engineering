# CD: cómo se despliega este repo

## Hoy: pull-based (GitOps en miniatura)

El servidor se actualiza solo: un timer de systemd corre
[`deploy/autodeploy.sh`](../deploy/autodeploy.sh) cada 2 minutos — si
`origin/main` se movió, hace `git pull --ff-only` y
`docker compose up -d --build`. Mergear a `main` **es** desplegar.

Es el mismo modelo que Argo CD usa en producción de verdad (el cluster *hala*
del repo): cero puertos nuevos, cero secrets en GitHub, y el security group
sigue cerrado (SSH solo desde una IP).

Instalación en el servidor (una vez):

```bash
sudo cp deploy/autodeploy.service deploy/autodeploy.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now autodeploy.timer
```

Operación:

```bash
systemctl list-timers autodeploy.timer      # próximo chequeo
journalctl -t autodeploy -n 20              # historial de deploys
```

Requisitos: el checkout del servidor debe estar en `main`, y los archivos
locales del servidor (`.env`, `Caddyfile`, `docker-compose.override.yml`) son
untracked, así que el pull nunca los toca.

## Pendiente: upgrade a push-based con GitHub Actions + SSM + OIDC

**TODO** — la versión "AWS nativa", sin SSH y sin keys de larga vida en
GitHub. Vale como estación de aprendizaje de IAM/OIDC:

1. **Instance profile** para la instancia con la managed policy
   `AmazonSSMManagedInstanceCore` (el agente SSM ya viene en la AMI de
   Ubuntu; con el profile, la instancia aparece como *managed node*).
2. **Proveedor OIDC de GitHub** en IAM (`token.actions.githubusercontent.com`)
   y un **rol** cuya trust policy solo acepte este repo y la rama `main`
   (condición sobre `sub`: `repo:cristianmgm7/ai-engineering:ref:refs/heads/main`).
   Permisos del rol: `ssm:SendCommand` acotado a la instancia por tag.
3. **Workflow** `.github/workflows/deploy.yml`: `on: push: branches: [main]` →
   `aws-actions/configure-aws-credentials` (role-to-assume, sin keys) →
   `aws ssm send-command --document-name AWS-RunShellScript` ejecutando
   `deploy/autodeploy.sh` en la instancia.
4. Apagar el timer (`systemctl disable --now autodeploy.timer`).

Ganancias sobre el timer: deploy inmediato (no hasta 2 min después), log del
deploy en la pestaña Actions del PR, y cero polling. Costo: más piezas IAM —
por eso es un buen ejercicio.
