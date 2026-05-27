# 市場データ統一インターフェース設計

`MASSIVE_API.md` を基に設計した、バックエンドが使用する統一市場データ API。
`MASSIVE_API_KEY` の有無で Massive API とシミュレーターを切り替える。

---

## 設計方針

- バックエンドの下流コード（SSEストリーミング、価格キャッシュ、APIルート）はデータソースを意識しない
- 実装の切り替えは環境変数のみで制御
- `asyncio` ベース（`asyncio.Task` としてバックグラウンド実行）
- 共有インメモリ価格キャッシュへの書き込みを担う

---

## 抽象基底クラス

```python
# backend/market/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PriceUpdate:
    ticker: str
    price: float
    prev_price: float
    timestamp: str  # ISO 8601


class MarketDataSource(ABC):
    """市場データソースの抽象インターフェース。"""

    @abstractmethod
    async def start(self, price_cache: dict[str, PriceUpdate]) -> None:
        """バックグラウンドポーリング/シミュレーションループを開始する。
        
        price_cache を継続的に更新し続ける。このメソッドは無限ループになる想定。
        price_cache のキーはティッカー文字列、値は PriceUpdate インスタンス。
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """ポーリング/シミュレーションを停止する。"""
        ...

    @abstractmethod
    def get_watched_tickers(self) -> set[str]:
        """現在監視中の銘柄セットを返す。"""
        ...

    @abstractmethod
    def add_ticker(self, ticker: str) -> None:
        """監視対象銘柄を追加する。"""
        ...

    @abstractmethod
    def remove_ticker(self, ticker: str) -> None:
        """監視対象銘柄を削除する。"""
        ...
```

---

## 価格キャッシュ

バックグラウンドタスクと SSE ストリームが共有するインメモリ辞書。

```python
# backend/market/cache.py
from backend.market.base import PriceUpdate

# アプリ全体で共有するシングルトン辞書
# キー: ticker (str), 値: PriceUpdate
price_cache: dict[str, PriceUpdate] = {}
```

アクセスは同一プロセス内のみ（SQLite と同様にシングルユーザー前提）。  
スレッドセーフ性の問題は asyncio の単一イベントループが解決する。

---

## Massive API 実装

```python
# backend/market/massive_client.py
import asyncio
import os
from datetime import datetime, timezone

import httpx

from backend.market.base import MarketDataSource, PriceUpdate

MASSIVE_BASE_URL = "https://api.polygon.io"
DEFAULT_POLL_INTERVAL = 5  # 有料ティア向けデフォルト（秒）
FREE_TIER_POLL_INTERVAL = 15  # 無料ティア向け（秒）


class MassiveMarketClient(MarketDataSource):
    def __init__(self, api_key: str, poll_interval: int | None = None):
        self._api_key = api_key
        self._poll_interval = poll_interval or int(
            os.environ.get("MASSIVE_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL)
        )
        self._watched: set[str] = set()
        self._running = False
        self._client: httpx.AsyncClient | None = None

    async def start(self, price_cache: dict[str, PriceUpdate]) -> None:
        self._running = True
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=10.0,
        )
        try:
            while self._running:
                if self._watched:
                    await self._poll_once(price_cache)
                await asyncio.sleep(self._poll_interval)
        finally:
            await self._client.aclose()

    async def stop(self) -> None:
        self._running = False

    def get_watched_tickers(self) -> set[str]:
        return set(self._watched)

    def add_ticker(self, ticker: str) -> None:
        self._watched.add(ticker.upper())

    def remove_ticker(self, ticker: str) -> None:
        self._watched.discard(ticker.upper())

    async def _poll_once(self, price_cache: dict[str, PriceUpdate]) -> None:
        tickers_param = ",".join(self._watched)
        try:
            resp = await self._client.get(
                f"{MASSIVE_BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers",
                params={"tickers": tickers_param},
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            return  # 一時的な障害はスキップ

        now = datetime.now(timezone.utc).isoformat()
        for item in resp.json().get("tickers", []):
            ticker = item["ticker"]
            price = (
                item.get("lastTrade", {}).get("p")
                or item.get("day", {}).get("c")
            )
            if price is None:
                continue

            prev = price_cache.get(ticker)
            price_cache[ticker] = PriceUpdate(
                ticker=ticker,
                price=float(price),
                prev_price=prev.price if prev else float(price),
                timestamp=now,
            )

    async def validate_ticker(self, ticker: str) -> bool:
        """Massive API がティッカーを認識しているか確認する。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=10.0,
            )
        try:
            resp = await self._client.get(
                f"{MASSIVE_BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker.upper()}"
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
```

---

## ファクトリ関数（切り替えロジック）

```python
# backend/market/factory.py
import os

from backend.market.base import MarketDataSource
from backend.market.massive_client import MassiveMarketClient
from backend.market.simulator import MarketSimulator


def create_market_source(initial_tickers: list[str]) -> MarketDataSource:
    """環境変数に基づいて市場データソースを生成する。
    
    MASSIVE_API_KEY が設定されていれば MassiveMarketClient を返す。
    未設定または空の場合は MarketSimulator を返す。
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        source = MassiveMarketClient(api_key=api_key)
    else:
        source = MarketSimulator()

    for ticker in initial_tickers:
        source.add_ticker(ticker)

    return source
```

---

## FastAPI への組み込み

```python
# backend/main.py（抜粋）
from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI

from backend.market.cache import price_cache
from backend.market.factory import create_market_source

market_source = None
market_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global market_source, market_task

    # DB初期化
    await init_db()

    # ウォッチリスト銘柄を読み込んでマーケットソースを起動
    initial_tickers = await get_watchlist_tickers()  # DB から取得
    market_source = create_market_source(initial_tickers)
    market_task = asyncio.create_task(market_source.start(price_cache))

    yield

    # シャットダウン
    if market_source:
        await market_source.stop()
    if market_task:
        market_task.cancel()

app = FastAPI(lifespan=lifespan)
```

---

## ウォッチリスト更新時の連携

銘柄の追加・削除は DB と同時にマーケットソースにも反映する。

```python
# POST /api/watchlist
async def add_to_watchlist(ticker: str):
    # ... DB に追加 ...
    market_source.add_ticker(ticker)

# DELETE /api/watchlist/{ticker}
async def remove_from_watchlist(ticker: str):
    # ... DB から削除 ...
    market_source.remove_ticker(ticker)
    # ポジションがなければキャッシュからも削除
    if not has_position(ticker):
        price_cache.pop(ticker, None)
```

---

## SSE ストリームでの利用

```python
# GET /api/stream/prices
async def price_stream():
    while True:
        if price_cache:
            updates = [
                {
                    "ticker": v.ticker,
                    "price": v.price,
                    "prev_price": v.prev_price,
                    "direction": "up" if v.price > v.prev_price
                                else "down" if v.price < v.prev_price
                                else "flat",
                }
                for v in price_cache.values()
            ]
            yield {
                "data": json.dumps({
                    "type": "prices",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "prices": updates,
                })
            }
        await asyncio.sleep(0.5)
```

---

## ファイル構成

```
backend/
└── market/
    ├── __init__.py
    ├── base.py          # 抽象基底クラス・PriceUpdate データクラス
    ├── cache.py         # 共有インメモリ価格キャッシュ
    ├── factory.py       # ファクトリ関数（環境変数で切り替え）
    ├── massive_client.py # Massive API ポーリング実装
    └── simulator.py     # GBM シミュレーター実装（MARKET_SIMULATOR.md 参照）
```

---

## ティッカー検証方針

| モード | 未知ティッカーの扱い |
|--------|---------------------|
| シミュレーター | 動的にデフォルトパラメータで価格生成。404 は返さない |
| Massive API | `validate_ticker()` で確認。API が 404 を返した場合のみ 422 エラーを返す |
