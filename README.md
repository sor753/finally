# finally

FinAlly Capstone Project - LLM駆動のトレーディング用ワークステーション（シミュレーション取引用）

## 起動方法

### 起動スクリプト（推奨）

```bash
# macOS / Linux
./scripts/start_mac.sh

# Windows PowerShell
./scripts/start_windows.ps1
```

### docker-compose（スクリプトを使わない場合）

```bash
# ビルドして起動
docker-compose up --build

# バックグラウンドで起動
docker-compose up --build -d

# 停止
docker-compose down
```

### docker 直接実行

```bash
docker build -t finally .
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

起動後、ブラウザで `http://localhost:8000` を開く。
