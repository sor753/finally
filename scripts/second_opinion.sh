#!/usr/bin/env bash
set -euo pipefail

# GITHUB_TOKEN の解決: 環境変数 → .env ファイル
if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    if [[ -f ".env" ]]; then
        GITHUB_TOKEN=$(grep -E '^GITHUB_TOKEN=' .env | cut -d'=' -f2-)
    fi
fi

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    echo "Error: GITHUB_TOKEN が設定されていません。" >&2
    echo ".env に GITHUB_TOKEN=<token> を追加してください。" >&2
    echo "トークンの取得: https://github.com/settings/tokens" >&2
    exit 1
fi

docker run --rm \
    -v "$(pwd):/workspace" \
    -v "$HOME/.cache/uv:/root/.cache/uv" \
    -w /workspace \
    -e GITHUB_TOKEN="$GITHUB_TOKEN" \
    ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
    uv run scripts/second_opinion.py "$@"
