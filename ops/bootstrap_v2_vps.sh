#!/usr/bin/env bash
# V2 setup only; never modifies previous bot, systemd, Indodax keys or LIVE flags.
set -Eeuo pipefail
umask 077

KEY="$HOME/.ssh/indodax_ai_bot_v2"
REPO="$HOME/indodax-ai-bot-v2"
REMOTE="git@github.com:dvoriano/indodax-ai-bot-v2.git"

usage() {
  echo 'Usage: bash ops/bootstrap_v2_vps.sh prepare-key|sync-and-probe' >&2
  exit 2
}

prepare_key() {
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  if [[ ! -f "$KEY" ]]; then
    if [[ -e "$KEY.pub" ]]; then
      echo "Public key exists but private key is missing. Refusing overwrite." >&2
      exit 2
    fi
    ssh-keygen -q -t ed25519 -N '' -f "$KEY" -C "indodax-ai-bot-v2-vps-readonly"
  fi
  if [[ ! -f "$KEY.pub" ]]; then
    ssh-keygen -y -f "$KEY" > "$KEY.pub"
  fi
  chmod 600 "$KEY"
  chmod 644 "$KEY.pub"
  echo "PUBLIC KEY ONLY. Add to V2 repository Settings > Deploy keys (READ ONLY):"
  cat "$KEY.pub"
  echo "NEVER share private key file $KEY"
}

sync_and_probe() {
  [[ -f "$KEY" && -f "$KEY.pub" ]] || {
    echo "Missing V2 key. Run prepare-key and register its PUBLIC key first." >&2
    exit 2
  }

  # Per-command identity; never modify SSH configuration of old bot.
  local ssh_command="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes"
  echo "Checking separate V2 read-only GitHub authorization..."
  GIT_SSH_COMMAND="$ssh_command" git ls-remote "$REMOTE" HEAD

  if [[ -e "$REPO" && ! -d "$REPO/.git" ]]; then
    echo "Refusing: V2 folder exists and is not a Git checkout." >&2
    exit 2
  fi

  if [[ ! -d "$REPO/.git" ]]; then
    GIT_SSH_COMMAND="$ssh_command" git clone --branch main --single-branch "$REMOTE" "$REPO"
  fi

  local origin
  origin="$(git -C "$REPO" remote get-url origin)"
  [[ "$origin" == "$REMOTE" ]] || {
    echo "Refusing: repository origin does not match V2." >&2
    exit 2
  }
  # This config affects ONLY the new V2 checkout.
  git -C "$REPO" config core.sshCommand "$ssh_command"
  git -C "$REPO" pull --ff-only origin main
  echo "V2 SHA: $(git -C "$REPO" rev-parse --short HEAD)"

  echo "Running ONE PUBLIC source probe, private API and trading remain OFF..."
  local rc=0
  (
    cd "$REPO"
    env INDODAX_TRADING_ENABLED=NO \
      INDODAX_ALLOW_LIVE_EXECUTION=NO \
      INDODAX_EXECUTION_MODE=DRY_RUN \
      PYTHONDONTWRITEBYTECODE=1 \
      python3 -B -m indodax_ai_bot \
        --db /tmp/indodax-v2-m02-public.sqlite3 \
        --verify-public --pair btc_idr
  ) || rc=$?
  printf 'M02_EXIT_CODE=%s\n' "$rc"
  return "$rc"
}

[[ $# -eq 1 ]] || usage
case "$1" in
  prepare-key) prepare_key ;;
  sync-and-probe) sync_and_probe ;;
  *) usage ;;
esac
