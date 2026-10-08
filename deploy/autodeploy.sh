#!/usr/bin/env bash
# Pull-based CD: si origin/main se movió, despliega. Lo dispara autodeploy.timer.
# Mismo modelo que Argo CD (GitOps): el servidor hala; nadie necesita entrar.
set -euo pipefail
cd "$(dirname "$0")/.."

git fetch -q origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi

logger -t autodeploy "deploying $REMOTE (was $LOCAL)"
git pull --ff-only -q origin main
docker compose up -d --build
logger -t autodeploy "deployed $REMOTE"
