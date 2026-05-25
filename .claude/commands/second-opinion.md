$ARGUMENTS で指定されたファイルを GitHub Models API (GPT-4o) に送信し、セカンドオピニオンとしてコードレビューを行う。

## 手順

1. $ARGUMENTS をスペース区切りでファイルパスのリストとして解釈する
2. 以下のコマンドを Bash で実行する:
   ```
   bash scripts/second_opinion.sh <ファイルパス...>
   ```
3. 出力されたレビュー結果をユーザーに提示する
4. 重大な指摘（バグ・セキュリティ・設計上の問題）があれば、修正案を提示する

## 前提条件

- `.env` に `GITHUB_TOKEN=<token>` を設定していること（取得: https://github.com/settings/tokens）
- Docker が起動していること（Python のローカルインストール不要）
- 初回は `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` イメージの pull が走る
