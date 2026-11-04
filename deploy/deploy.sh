#!/usr/bin/env bash
# Deploy the latest main branch to this EC2 instance.
# Run by hand (sudo /opt/uptime/deploy/deploy.sh) or by CI/CD through SSM.
set -euo pipefail

REPO_URL="https://github.com/spandey1702/uptime-monitor-aws.git"
REPO_DIR="/opt/uptime"
BRANCH="main"
REGION="us-east-2"
PARAM_NAME="/uptime/DATABASE_URL"
CONTAINER="uptime-app"
IMAGE="uptime-app"

log() { echo "[deploy] $*"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

# On a fresh instance, user data may still be installing Docker
cloud-init status --wait >/dev/null 2>&1 || true

# 1. Code: clone the first time, then reset to exactly what's on main
if [[ -d "$REPO_DIR/.git" ]]; then
  log "Updating code"
  git -C "$REPO_DIR" fetch --quiet origin "$BRANCH"
  git -C "$REPO_DIR" reset --quiet --hard "origin/$BRANCH"
else
  log "Cloning $REPO_URL"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi
COMMIT=$(git -C "$REPO_DIR" rev-parse --short HEAD)
log "Commit $COMMIT"

# 2. Image, tagged with the commit so every deploy is traceable
log "Building image $IMAGE:$COMMIT"
docker build --quiet -t "$IMAGE:$COMMIT" -t "$IMAGE:latest" "$REPO_DIR/app" >/dev/null

# 3. Secret: read from Parameter Store with the instance role, kept only in memory
DATABASE_URL=$(aws ssm get-parameter --region "$REGION" --name "$PARAM_NAME" \
  --with-decryption --query "Parameter.Value" --output text)
export DATABASE_URL

# 4. Replace the container, remembering the old image for rollback
PREVIOUS_IMAGE=$(docker inspect --format '{{.Image}}' "$CONTAINER" 2>/dev/null || true)

start_container() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  # "-e DATABASE_URL" with no value passes it from the environment, so the
  # password never appears in the process list
  docker run -d --name "$CONTAINER" --restart unless-stopped \
    -p 80:8000 \
    -e DATABASE_URL -e LOG_LEVEL=INFO \
    --log-opt max-size=10m --log-opt max-file=3 \
    "$1" >/dev/null
}

healthy() {
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 http://localhost/health >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

log "Starting container"
start_container "$IMAGE:$COMMIT"

# 5. Health gate: the deploy only counts if /health says ok
if healthy; then
  log "Healthy: $(curl -s http://localhost/health)"
  docker image prune -f >/dev/null
  log "Deployed $COMMIT"
else
  log "Health check failed. Last logs:"
  docker logs --tail 30 "$CONTAINER" || true
  if [[ -n "$PREVIOUS_IMAGE" ]]; then
    log "Rolling back to the previous image"
    start_container "$PREVIOUS_IMAGE"
    healthy && log "Rollback healthy" || log "Rollback also unhealthy"
  fi
  exit 1
fi
