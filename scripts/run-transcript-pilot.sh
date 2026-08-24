#!/usr/bin/env bash

set -Eeuo pipefail

PILOT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PILOT_REPO_ROOT="$(cd "$PILOT_SCRIPT_DIR/.." && pwd)"
PILOT_HOST="127.0.0.1"
PILOT_BACKEND_PORT="${TRANSCRIPT_PILOT_BACKEND_PORT:-8000}"
PILOT_FRONTEND_PORT="${TRANSCRIPT_PILOT_FRONTEND_PORT:-5173}"
PILOT_DATA_ROOT="${NLP_SKILL_AGENTS_DATA_DIR:-$PILOT_REPO_ROOT/local_data/transcript_pilot_live}"
PILOT_LAUNCH_ID="$(date -u '+%Y%m%dT%H%M%SZ')-$$"
PILOT_LOG_DIR="$PILOT_DATA_ROOT/launcher_logs/$PILOT_LAUNCH_ID"
PILOT_BACKEND_LOG="$PILOT_LOG_DIR/backend.log"
PILOT_FRONTEND_LOG="$PILOT_LOG_DIR/frontend.log"
PILOT_BACKEND_PID=""
PILOT_FRONTEND_PID=""

pilot_fail() {
  printf 'Transcript pilot launcher: %s\n' "$1" >&2
  exit 1
}

pilot_require_command() {
  command -v "$1" >/dev/null 2>&1 || pilot_fail "required command not found: $1"
}

pilot_valid_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && (( "$1" >= 1 && "$1" <= 65535 ))
}

pilot_tail_log() {
  local pilot_log_path="$1"
  if [[ -f "$pilot_log_path" ]]; then
    printf '\nLast lines from %s:\n' "$pilot_log_path" >&2
    tail -n 20 "$pilot_log_path" >&2 || true
  fi
}

pilot_cleanup() {
  local pilot_exit_code=$?
  trap - EXIT INT TERM

  if [[ -n "$PILOT_FRONTEND_PID" ]] && kill -0 "$PILOT_FRONTEND_PID" 2>/dev/null; then
    kill -TERM "$PILOT_FRONTEND_PID" 2>/dev/null || true
  fi
  if [[ -n "$PILOT_BACKEND_PID" ]] && kill -0 "$PILOT_BACKEND_PID" 2>/dev/null; then
    kill -TERM "$PILOT_BACKEND_PID" 2>/dev/null || true
  fi

  if [[ -n "$PILOT_FRONTEND_PID" ]]; then
    wait "$PILOT_FRONTEND_PID" 2>/dev/null || true
  fi
  if [[ -n "$PILOT_BACKEND_PID" ]]; then
    wait "$PILOT_BACKEND_PID" 2>/dev/null || true
  fi

  printf '\nTranscript pilot stopped. Logs remain at %s\n' "$PILOT_LOG_DIR"
  exit "$pilot_exit_code"
}

pilot_signal_exit() {
  exit 130
}

pilot_wait_for_url() {
  local pilot_label="$1"
  local pilot_url="$2"
  local pilot_pid="$3"
  local pilot_log_path="$4"
  local pilot_attempt

  for pilot_attempt in $(seq 1 120); do
    if ! kill -0 "$pilot_pid" 2>/dev/null; then
      pilot_tail_log "$pilot_log_path"
      pilot_fail "$pilot_label exited before readiness"
    fi
    if curl --silent --show-error --fail --max-time 1 "$pilot_url" >/dev/null 2>&1; then
      printf 'Ready: %s (%s)\n' "$pilot_label" "$pilot_url"
      return 0
    fi
    sleep 0.25
  done

  pilot_tail_log "$pilot_log_path"
  pilot_fail "$pilot_label did not become ready within 30 seconds"
}

trap pilot_cleanup EXIT
trap pilot_signal_exit INT TERM

pilot_require_command git
pilot_require_command curl
pilot_require_command date
pilot_require_command seq
pilot_require_command tail

pilot_valid_port "$PILOT_BACKEND_PORT" || pilot_fail "invalid backend port: $PILOT_BACKEND_PORT"
pilot_valid_port "$PILOT_FRONTEND_PORT" || pilot_fail "invalid frontend port: $PILOT_FRONTEND_PORT"
[[ "$PILOT_BACKEND_PORT" != "$PILOT_FRONTEND_PORT" ]] || pilot_fail "backend and frontend ports must differ"

[[ -x "$PILOT_REPO_ROOT/.venv/bin/uvicorn" ]] || pilot_fail "missing .venv/bin/uvicorn; install the backend environment first"
[[ -x "$PILOT_REPO_ROOT/frontend/node_modules/.bin/vite" ]] || pilot_fail "missing frontend/node_modules; run npm install in frontend first"

if git -C "$PILOT_REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  TRANSCRIPT_PILOT_CODE_COMMIT="$(git -C "$PILOT_REPO_ROOT" rev-parse HEAD)"
  if [[ -n "$(git -C "$PILOT_REPO_ROOT" status --porcelain --untracked-files=normal)" ]]; then
    TRANSCRIPT_PILOT_CODE_DIRTY="true"
  else
    TRANSCRIPT_PILOT_CODE_DIRTY="false"
  fi
else
  TRANSCRIPT_PILOT_CODE_COMMIT="unknown"
  TRANSCRIPT_PILOT_CODE_DIRTY="unknown"
fi

[[ "$TRANSCRIPT_PILOT_CODE_COMMIT" != "unknown" ]] || pilot_fail "pilot egress requires a Git commit identity"
[[ "$TRANSCRIPT_PILOT_CODE_DIRTY" == "false" ]] || pilot_fail "pilot egress requires a clean checkout; commit or stash local changes first"

export TRANSCRIPT_PILOT_CODE_COMMIT
export TRANSCRIPT_PILOT_CODE_DIRTY
export NLP_SKILL_AGENTS_DATA_DIR="$PILOT_DATA_ROOT"
export VITE_API_BASE="http://$PILOT_HOST:$PILOT_BACKEND_PORT"
export NLP_SKILL_AGENTS_CORS_ORIGINS="http://$PILOT_HOST:$PILOT_FRONTEND_PORT"

mkdir -p "$PILOT_LOG_DIR"

printf 'Starting researcher-supervised transcript pilot\n'
printf 'Code commit: %s\n' "$TRANSCRIPT_PILOT_CODE_COMMIT"
printf 'Dirty checkout: %s\n' "$TRANSCRIPT_PILOT_CODE_DIRTY"
printf 'Data root: %s\n' "$PILOT_DATA_ROOT"
printf 'Logs: %s\n' "$PILOT_LOG_DIR"
printf 'Boundary: loopback UI and storage; authorized transcript chunks use remote Luna inference.\n\n'

(
  cd "$PILOT_REPO_ROOT"
  exec "$PILOT_REPO_ROOT/.venv/bin/uvicorn" backend.app.main:app \
    --host "$PILOT_HOST" \
    --port "$PILOT_BACKEND_PORT"
) >"$PILOT_BACKEND_LOG" 2>&1 &
PILOT_BACKEND_PID=$!

(
  cd "$PILOT_REPO_ROOT/frontend"
  exec "$PILOT_REPO_ROOT/frontend/node_modules/.bin/vite" \
    --host "$PILOT_HOST" \
    --port "$PILOT_FRONTEND_PORT" \
    --strictPort
) >"$PILOT_FRONTEND_LOG" 2>&1 &
PILOT_FRONTEND_PID=$!

pilot_wait_for_url \
  "backend" \
  "http://$PILOT_HOST:$PILOT_BACKEND_PORT/api/health" \
  "$PILOT_BACKEND_PID" \
  "$PILOT_BACKEND_LOG"
pilot_wait_for_url \
  "frontend" \
  "http://$PILOT_HOST:$PILOT_FRONTEND_PORT/transcript-pilot" \
  "$PILOT_FRONTEND_PID" \
  "$PILOT_FRONTEND_LOG"

printf '\nOpen: http://%s:%s/transcript-pilot\n' "$PILOT_HOST" "$PILOT_FRONTEND_PORT"
printf 'Press Ctrl-C to stop both services cleanly.\n'

while kill -0 "$PILOT_BACKEND_PID" 2>/dev/null && kill -0 "$PILOT_FRONTEND_PID" 2>/dev/null; do
  sleep 1
done

if ! kill -0 "$PILOT_BACKEND_PID" 2>/dev/null; then
  pilot_tail_log "$PILOT_BACKEND_LOG"
  pilot_fail "backend stopped unexpectedly"
fi

pilot_tail_log "$PILOT_FRONTEND_LOG"
pilot_fail "frontend stopped unexpectedly"
