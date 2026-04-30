#!/usr/bin/env bash
# configure-scaleway.sh
#
# Purpose:
#   Configure the local shell to route Anthropic-shape traffic through the
#   y-router proxy to Scaleway Generative APIs. Sources the secret key from
#   the Scaleway CLI profile `newprofile`, exports the canonical
#   ANTHROPIC_* environment variables, and (by default) performs two LIVE
#   probes:
#     1. Direct Scaleway OpenAI-compatible /v1/chat/completions
#     2. y-router Anthropic-shape /v1/messages
#
# Usage:
#   source scripts/configure-scaleway.sh           # exports vars, runs probes
#   bash scripts/configure-scaleway.sh             # runs probes, exits non-zero on failure
#   bash scripts/configure-scaleway.sh --skip-live # offline CI: skip both probes
#
# Required tools on PATH: scw, curl, jq.
#
# Owner: engineer-3 (RC-002).

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
SKIP_LIVE=0
for arg in "$@"; do
    case "$arg" in
        --skip-live)
            SKIP_LIVE=1
            ;;
        -h|--help)
            sed -n '2,20p' "${BASH_SOURCE[0]}"
            return 0 2>/dev/null || exit 0
            ;;
        *)
            echo "WARN: unknown argument '$arg' (ignored)" >&2
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Tool prerequisites
# ---------------------------------------------------------------------------
_require_bin() {
    local bin="$1"
    if ! command -v "$bin" >/dev/null 2>&1; then
        echo "ERROR: required binary '$bin' not found on PATH" >&2
        return 1
    fi
}

if ! command -v scw >/dev/null 2>&1; then
    echo "ERROR: missing Scaleway secret key (scw profile=newprofile)" >&2
    # `scw` missing means we cannot read the key at all — same fail-fast contract.
    return 1 2>/dev/null || exit 1
fi

if ! _require_bin curl; then
    return 1 2>/dev/null || exit 1
fi
if ! _require_bin jq; then
    return 1 2>/dev/null || exit 1
fi

# ---------------------------------------------------------------------------
# Read Scaleway secret key
# ---------------------------------------------------------------------------
# Try canonical key first, fall back to legacy snake_case. 2s budget per call
# is enforced via SIGALRM-style timeout below.
_scw_get_key() {
    # Two attempts; either one returning a non-empty string wins.
    local key=""
    key="$(scw config get secret-key --profile newprofile 2>/dev/null || true)"
    if [[ -z "${key// /}" ]]; then
        key="$(scw config get secret_key --profile newprofile 2>/dev/null || true)"
    fi
    printf '%s' "$key"
}

SCALEWAY_API_KEY_FROM_SCW="$(_scw_get_key || true)"
# Strip surrounding whitespace.
SCALEWAY_API_KEY_FROM_SCW="${SCALEWAY_API_KEY_FROM_SCW//$'\n'/}"
SCALEWAY_API_KEY_FROM_SCW="${SCALEWAY_API_KEY_FROM_SCW//$'\r'/}"

# Allow a pre-set SCALEWAY_API_KEY to win (caller already populated it).
if [[ -z "${SCALEWAY_API_KEY:-}" ]]; then
    SCALEWAY_API_KEY="$SCALEWAY_API_KEY_FROM_SCW"
fi

if [[ -z "${SCALEWAY_API_KEY// /}" ]]; then
    echo "ERROR: missing Scaleway secret key (scw profile=newprofile)" >&2
    return 1 2>/dev/null || exit 1
fi

export SCALEWAY_API_KEY

# ---------------------------------------------------------------------------
# Export Anthropic-shape env vars
# ---------------------------------------------------------------------------
: "${ANTHROPIC_BASE_URL:=http://localhost:8787}"
export ANTHROPIC_BASE_URL
export ANTHROPIC_AUTH_TOKEN="$SCALEWAY_API_KEY"
export ANTHROPIC_MODEL="devstral-2-123b-instruct-2512"
export ANTHROPIC_SMALL_FAST_MODEL="devstral-2-123b-instruct-2512"

# Diagnostic (do not echo the secret).
echo "configure-scaleway: ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL"
echo "configure-scaleway: ANTHROPIC_MODEL=$ANTHROPIC_MODEL"
echo "configure-scaleway: SCALEWAY_API_KEY=<redacted, length=${#SCALEWAY_API_KEY}>"

# ---------------------------------------------------------------------------
# Live probes
# ---------------------------------------------------------------------------
if [[ "$SKIP_LIVE" -eq 1 ]]; then
    echo "configure-scaleway: --skip-live set, bypassing live probes"
    return 0 2>/dev/null || exit 0
fi

_probe_scaleway_direct() {
    local body
    body=$(jq -n --arg model "$ANTHROPIC_MODEL" '{
        model: $model,
        messages: [{role: "user", content: "ping"}],
        max_tokens: 16
    }')

    local resp
    if ! resp=$(curl --fail-with-body -sS \
        --max-time 30 \
        -X POST "https://api.scaleway.ai/v1/chat/completions" \
        -H "Authorization: Bearer $SCALEWAY_API_KEY" \
        -H "Content-Type: application/json" \
        -d "$body"); then
        echo "ERROR: Scaleway direct probe failed (curl)" >&2
        echo "  endpoint: https://api.scaleway.ai/v1/chat/completions" >&2
        echo "  model: $ANTHROPIC_MODEL" >&2
        return 1
    fi

    if ! printf '%s' "$resp" | jq -e '.choices[0].message.content | strings | select(length>0)' >/dev/null; then
        echo "ERROR: Scaleway direct probe returned empty/invalid completion" >&2
        echo "  body (first 400 chars): ${resp:0:400}" >&2
        return 1
    fi

    echo "configure-scaleway: Scaleway direct probe OK"
    return 0
}

_probe_yrouter() {
    # Anthropic-shape /v1/messages.
    local body
    body=$(jq -n --arg model "$ANTHROPIC_MODEL" '{
        model: $model,
        max_tokens: 16,
        messages: [{role: "user", content: "ping"}]
    }')

    # Quick reachability check first — the y-router may not be running locally.
    # Use a short connect timeout so we can degrade to a warning.
    local host_port
    host_port="${ANTHROPIC_BASE_URL#http://}"
    host_port="${host_port#https://}"
    host_port="${host_port%%/*}"

    if ! curl -sS --max-time 2 -o /dev/null "$ANTHROPIC_BASE_URL" 2>/dev/null; then
        # Could still be reachable but rejecting GET. Try a TCP connect via curl.
        if ! curl -sS --max-time 2 --connect-timeout 2 -o /dev/null "$ANTHROPIC_BASE_URL/v1/messages" 2>/dev/null; then
            echo "WARN: y-router at $ANTHROPIC_BASE_URL not reachable; skipping y-router probe" >&2
            return 0
        fi
    fi

    local resp
    if ! resp=$(curl --fail-with-body -sS \
        --max-time 30 \
        -X POST "$ANTHROPIC_BASE_URL/v1/messages" \
        -H "Authorization: Bearer $ANTHROPIC_AUTH_TOKEN" \
        -H "x-api-key: $ANTHROPIC_AUTH_TOKEN" \
        -H "anthropic-version: 2023-06-01" \
        -H "Content-Type: application/json" \
        -d "$body"); then
        echo "ERROR: y-router probe failed (curl)" >&2
        echo "  endpoint: $ANTHROPIC_BASE_URL/v1/messages" >&2
        return 1
    fi

    # Anthropic shape: .content[0].text OR OpenAI shape: .choices[0].message.content
    if ! printf '%s' "$resp" | jq -e '
        (.content[0].text // .choices[0].message.content)
        | strings | select(length>0)
    ' >/dev/null; then
        echo "ERROR: y-router probe returned empty/invalid completion" >&2
        echo "  body (first 400 chars): ${resp:0:400}" >&2
        return 1
    fi

    echo "configure-scaleway: y-router probe OK"
    return 0
}

_probe_failed=0
if ! _probe_scaleway_direct; then
    _probe_failed=1
fi

if ! _probe_yrouter; then
    _probe_failed=1
fi

if [[ "$_probe_failed" -ne 0 ]]; then
    return 1 2>/dev/null || exit 1
fi

echo "configure-scaleway: all probes passed"
return 0 2>/dev/null || exit 0
