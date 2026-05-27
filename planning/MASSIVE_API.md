# Massive API (旧 Polygon.io) リファレンス

Polygon.io は 2025年10月30日に Massive.com にリブランド。既存のAPIキーと実装はそのまま動作する。

## 基本情報

| 項目 | 値 |
|------|-----|
| ベースURL | `https://api.polygon.io`（レガシー、引き続き有効） |
| 認証 | `Authorization: Bearer YOUR_API_KEY` ヘッダー、または `?apiKey=YOUR_API_KEY` クエリパラメータ |
| Python ライブラリ | `pip install -U polygon-api-client` |

## レート制限

| プラン | 制限 | データ |
|--------|------|--------|
| 無料 | 5リクエスト/分 | 遅延データ |
| Starter ($29/月) | 制限あり | 15分遅延 |
| Developer ($79/月) | 制限緩和 | リアルタイム |
| Advanced ($199/月) | 無制限 | リアルタイム + WebSocket |

本プロジェクトでは REST ポーリング方式を採用。無料ティアは15秒ごと、有料ティアは5秒ごとのポーリングを推奨。

---

## 主要エンドポイント

### 1. 複数銘柄スナップショット（バッチ取得）

リアルタイム価格の一括取得に最も重要なエンドポイント。最大250銘柄を1リクエストで取得可能。

```
GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,GOOGL
```

**レスポンス例:**
```json
{
  "status": "OK",
  "count": 2,
  "tickers": [
    {
      "ticker": "AAPL",
      "day": {
        "o": 119.62,
        "h": 120.53,
        "l": 118.81,
        "c": 120.42,
        "v": 28727868,
        "vw": 119.725
      },
      "lastTrade": {
        "p": 120.47,
        "s": 236,
        "t": 1605195918306274000
      },
      "lastQuote": {
        "P": 120.47,
        "p": 120.46,
        "S": 4,
        "s": 8,
        "t": 1605195918507251700
      },
      "prevDay": {
        "o": 117.19,
        "h": 119.63,
        "l": 116.44,
        "c": 119.49,
        "v": 110597265,
        "vw": 118.4998
      },
      "todaysChange": 0.98,
      "todaysChangePerc": 0.82,
      "updated": 1605195918306274000
    }
  ]
}
```

**フィールド定義:**
- `lastTrade.p` — 最終取引価格（リアルタイム価格として使用）
- `prevDay.c` — 前日終値
- `todaysChange` — 前日比価格変動
- `todaysChangePerc` — 前日比変動率（%）
- `day.vw` — 当日の出来高加重平均価格（VWAP）

### 2. 単一銘柄スナップショット

```
GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}
```

### 3. 前日終値（OHLC）

```
GET /v2/aggs/ticker/{ticker}/prev
```

**レスポンス例:**
```json
{
  "status": "OK",
  "resultsCount": 1,
  "results": [
    {
      "T": "AAPL",
      "o": 117.19,
      "h": 119.63,
      "l": 116.44,
      "c": 119.49,
      "v": 110597265,
      "vw": 118.4998,
      "t": 1604673600000
    }
  ]
}
```

### 4. 最終取引価格（単一銘柄）

```
GET /v2/last/trade/{ticker}
```

---

## Python コード例

### インストール

```bash
pip install polygon-api-client
```

### 複数銘柄のリアルタイム価格取得（本プロジェクトのメイン用途）

```python
import os
import httpx

API_KEY = os.environ["MASSIVE_API_KEY"]
BASE_URL = "https://api.polygon.io"

def fetch_prices(tickers: list[str]) -> dict[str, float]:
    """複数銘柄の最新価格を一括取得する。"""
    tickers_param = ",".join(tickers)
    url = f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers"
    
    response = httpx.get(
        url,
        params={"tickers": tickers_param},
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=10.0,
    )
    response.raise_for_status()
    data = response.json()
    
    result = {}
    for ticker_data in data.get("tickers", []):
        ticker = ticker_data["ticker"]
        # 最終取引価格を優先、なければ当日終値にフォールバック
        price = (
            ticker_data.get("lastTrade", {}).get("p")
            or ticker_data.get("day", {}).get("c")
        )
        if price:
            result[ticker] = float(price)
    return result
```

### 前日終値の取得

```python
def fetch_prev_close(ticker: str) -> float | None:
    """指定銘柄の前日終値を取得する。"""
    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/prev"
    
    response = httpx.get(
        url,
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=10.0,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    
    data = response.json()
    results = data.get("results", [])
    if results:
        return float(results[0]["c"])
    return None
```

### polygon-api-client ライブラリを使用した例

```python
from polygon import RESTClient

client = RESTClient(api_key=os.environ["MASSIVE_API_KEY"])

# スナップショット取得
snapshot = client.get_snapshot_all("stocks", tickers=["AAPL", "MSFT", "GOOGL"])
for ticker_snap in snapshot:
    print(f"{ticker_snap.ticker}: {ticker_snap.last_trade.price}")

# 前日終値取得
prev = client.get_previous_close_agg("AAPL")
for agg in prev:
    print(f"AAPL 前日終値: {agg.close}")
```

---

## 本プロジェクトでの使用方針

1. **ポーリング方式**: REST ポーリングで複数銘柄を一括取得（WebSocket は使用しない）
2. **バッチエンドポイント**: `/v2/snapshot/locale/us/markets/stocks/tickers?tickers=...` で全ウォッチリスト銘柄を1リクエストで取得
3. **ポーリング間隔**: 無料ティア 15秒、有料ティアは `MASSIVE_POLL_INTERVAL_SECONDS` 環境変数で制御（デフォルト5秒）
4. **価格フィールド**: `lastTrade.p`（最終取引価格）を優先使用、なければ `day.c` にフォールバック
5. **未知銘柄の扱い**: API が 404 または空を返した場合のみエラーとする
6. **httpx 使用**: asyncio 対応のため `httpx.AsyncClient` を本番実装で使用

## 注意事項

- スナップショットデータは毎日 EST 3:30 AM にリセットされ、4:00 AM から再取得開始
- 取引時間外はデータが古くなる場合がある
- 無料ティアは遅延データのため、シミュレーター用途のデモには十分
