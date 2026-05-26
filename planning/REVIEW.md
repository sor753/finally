# 変更レビュー（前回コミット以降）

レビュー実施日: 2026-05-27

対象コミット: a28e8c1 以降の未コミット変更

---

## 変更概要

前回コミット以降の変更は以下の4ファイル。

| 変更種別 | ファイルパス |
|----------|-------------|
| 削除 | `.claude/agents/reviewer.md` |
| 削除 | `planning/REVIEW.md` |
| 新規 | `.claude/agents/change-reviewer.md` |
| 新規 | `planning/PLAN-REVIEW.md` |

---

## 変更の目的と内容

### 削除: `.claude/agents/reviewer.md`

旧エージェント定義。`planning/PLAN.md` をレビューして `planning/REVIEW.md` に記録するという固定的な動作のみを定義していた。

### 削除: `planning/REVIEW.md`

旧レビューファイル。内容は新規ファイル `planning/PLAN-REVIEW.md` に移植されている。

### 新規: `.claude/agents/change-reviewer.md`

前回コミット以降のすべての変更をレビューする新しいエージェント定義。`/second-opinion` スキルを使い、差分ファイルを GitHub Models API (GPT-4o) に送信してセカンドオピニオンを取得する設計に変更。自身でレビューを行わず、外部モデルに委任する方式。

### 新規: `planning/PLAN-REVIEW.md`

旧 `planning/REVIEW.md` の内容を引き継ぐ形で作成されたレビューファイル。第5回レビュー・GPT-4o セカンドオピニオン（第1回・第2回）・第6回対応記録を含む。

---

## GPT-4o セカンドオピニオン（2026-05-27）

> GitHub Models API (GPT-4o) による自動レビュー結果。

### 正確性の問題

1. **`planning/PLAN-REVIEW.md` セクション8**
   - `GET /api/watchlist` のレスポンス形状が未定義。他のエンドポイントと整合性を保つため最低限の JSON スキーマ例を追加すること。
   - `POST /api/watchlist` と `DELETE /api/watchlist/{ticker}` の成功時レスポンスが曖昧。204 No Content か削除済みオブジェクトを返すかを明確化すること。

2. **`.claude/agents/change-reviewer.md`**
   - `description` フィールドで「自分でレビューしない」と明記されているが、コマンド失敗時のエラーハンドリングが不明確。

### セキュリティの問題

3. **`planning/PLAN-REVIEW.md` セクション9**
   - LLM レスポンスのサニタイズ処理の具体的な記述がない。`bleach` ライブラリの使用例を追記することで実装者の判断基準を明確化すること。
   ※ この指摘は第6回対応記録で既に `bleach.clean(message, tags=[], strip=True)` を PLAN.md に反映済み。PLAN-REVIEW.md 自体の記述が古い。

4. **`.claude/agents/change-reviewer.md`**
   - 差分ファイルのパスを外部コマンドに渡す設計はコマンドインジェクションのリスクがある。パラメータのエスケープ処理を明記すること。

### パフォーマンスの問題

5. **`planning/PLAN-REVIEW.md` セクション6（SSE）**
   - 全銘柄の価格情報を全クライアントに送信する設計はクライアント数増加時に帯域を圧迫する可能性がある。
   ※ PLAN.md セクション6に既知の制約として明記済み。

6. **`planning/PLAN-REVIEW.md` セクション7（DB初期化）**
   - SQLite の初回リクエスト時にスキーマ作成が走る設計は遅延を引き起こす可能性がある。
   ※ PLAN.md セクション7に `lifespan` イベントで起動時に実行する旨を明記済み。

### 設計上の懸念

7. **`planning/PLAN-REVIEW.md` セクション12（テスト）**
   - テスト実行コマンドが記載されていないため CI 設定やローカル実行時の手順が不明確。最低限のコマンド例を追記すること。

8. **`.claude/agents/change-reviewer.md`**
   - レビュー結果の保存形式（ファイルパス・フォーマット）が不明確。

### 改善提案

9. **`planning/PLAN-REVIEW.md`**
   - 過去レビュー記録を別ファイルに移管し PLAN.md を簡潔化することを検討する。

10. **`.claude/agents/change-reviewer.md`**
    - コマンド実行結果のエラーハンドリングを明記し、失敗時のログ保存方法を追加すること。

---

## 総評

### 変更の目的と意図

レビュー対象を「PLAN.md の静的レビュー」から「前回コミット以降のすべての変更」に拡張し、エージェントの責務をより汎用的に再定義した変更。ファイル名の変更（`reviewer.md` → `change-reviewer.md`、`REVIEW.md` → `PLAN-REVIEW.md`）はその目的の変化を反映している。

### 内容の品質と正確性

`planning/PLAN-REVIEW.md` の内容は旧 `planning/REVIEW.md` を忠実に引き継いでおり、過去5回分のレビュー記録・2回の GPT-4o セカンドオピニオン・対応履歴が網羅されている。PLAN.md との整合性は高く、反映済みの項目は対応記録として明記されている。

`.claude/agents/change-reviewer.md` の定義は簡潔だが、エラーハンドリングと出力先フォーマットの明記が不足している。

### PLAN.md との整合性

GPT-4o セカンドオピニオンが指摘した「サニタイズ未定義」「DB初期化遅延」「SSEスケーラビリティ」はいずれも PLAN.md 本体に対応済みであり、PLAN-REVIEW.md の対応履歴とも整合している。

### 対応が必要な事項

すべての指摘事項に対応済み。

| 優先度 | 指摘番号 | 内容 | 状態 |
|--------|----------|------|------|
| 高 | 1 | `GET /api/watchlist` のレスポンス JSON サンプルを PLAN.md に追加する | ✅ 対応済み（PLAN.md セクション8に既に定義済みであることを確認） |
| 高 | 1 | `POST /api/watchlist` と `DELETE /api/watchlist/{ticker}` の成功時レスポンスを PLAN.md に定義する | ✅ 対応済み（PLAN.md セクション8に既に定義済みであることを確認） |
| 中 | 7 | PLAN.md セクション12にテスト実行コマンド例を追記する | ✅ 対応済み（2026-05-27） |
| 低 | 8 | `.claude/agents/change-reviewer.md` にレビュー結果の保存先を明記する | ✅ 対応済み（2026-05-27） |
| 低 | 10 | `.claude/agents/change-reviewer.md` にエラーハンドリングの方針を追記する | ✅ 対応済み（2026-05-27） |
