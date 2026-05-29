# マーケットデータ 詳細設計書

PLAN.md・MARKET_INTERFACE.md・MARKET_SIMULATOR.md・MASSIVE_API.md を統合した、バックエンド実装者向けの完全リファレンス。

---

## 1. 全体構成

```
backend/
├── main.py                   # FastAPI アプリ、lifespan フック
├── market/
│   ├── __init__.py
│   ├── base.py               # 抽象基底クラス・PriceUpdate データクラス
│   ├── cache.py              # 共有インメモリ価格キャッシュ（シングルトン辞書）
│   ├── factory.py            # 環境変数でソースを切り替えるファクトリ
│   ├── simulator.py          # GBM シミュレーター（外部依存なし）
│   └── massive_client.py     # Massive (Polygon.io) REST ポーリング実装
├── routers/
│   ├── stream.py             # GET /api/stream/prices (SSE)
│   ├── portfolio.py          # GET|POST /api/portfolio
│   └── watchlist.py          # GET|POST|DELETE /api/watchlist
└── db/
    ├── schema.sql
    └── init.py               # 遅延初期化ロジック
```

---

## 2. データクラスと抽象インターフェース

### `backend/market/base.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PriceUpdate:
    ticker: str
    price: float        # 最新価格（小数点以下4桁）
    prev_price: float   # 直前の価格（方向判定・フラッシュアニメーション用）
    timestamp: str      # ISO 8601 UTC 文字列


class MarketDataSource(ABC):
    """市場データソースの統一インターフェース。
    
    シミュレーターと Massive API クライアントの両方がこれを実装する。
    下流コード（SSE・キャッシュ・APIルート）はソースを意識しない。
    """

    @abstractmethod
    async def start(self, price_cache: dict[str, PriceUpdate]) -> None:
        """バックグラウンドループを開始する。無限ループになる想定。
        
        price_cache はキー=ティッカー文字列・値=PriceUpdate のシングルトン辞書。
        このメソッド内で継続的に price_cache を書き換え続ける。
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """ループを停止する。"""
        ...

    @abstractmethod
    def get_watched_tickers(self) -> set[str]:
        """現在監視中の銘柄セットを返す。"""
        ...

    @abstractmethod
    def add_ticker(self, ticker: str) -> None:
        """監視対象銘柄を追加する（大文字正規化を含む）。"""
        ...

    @abstractmethod
    def remove_ticker(self, ticker: str) -> None:
        """監視対象から銘柄を削除する。"""
        ...
```

---

## 3. 共有価格キャッシュ

### `backend/market/cache.py`

```python
from backend.market.base import PriceUpdate

# アプリ全体で共有するシングルトン辞書
# - キー: ticker (大文字, str)
# - 値:   PriceUpdate インスタンス
# asyncio の単一イベントループがスレッドセーフ性を保証する。
price_cache: dict[str, PriceUpdate] = {}
```

**設計上の注意点:**

- このオブジェクトはモジュールレベルで一度だけ生成される（シングルトン）。
- `main.py` の lifespan で `market_source.start(price_cache)` に渡す。
- SSE ストリームはこの辞書を読み取るだけで、書き込まない。
- `DELETE /api/watchlist/{ticker}` 実行後、ポジションがない場合は `price_cache.pop(ticker, None)` でエントリを削除する。ポジションがある銘柄は削除しない（SSE 配信を継続する必要があるため）。

---

## 4. GBM シミュレーター

### `backend/market/simulator.py`

#### 4-1. 銘柄パラメータ

```python
# (初期価格, ドリフト年率, ボラティリティ年率)
DEFAULT_TICKER_PARAMS: dict[str, tuple[float, float, float]] = {
    "AAPL":  (190.0,  0.08, 0.25),
    "GOOGL": (175.0,  0.10, 0.28),
    "MSFT":  (415.0,  0.09, 0.22),
    "AMZN":  (185.0,  0.12, 0.30),
    "TSLA":  (250.0,  0.05, 0.60),  # 高ボラ
    "NVDA":  (875.0,  0.15, 0.50),  # 高ボラ
    "META":  (510.0,  0.10, 0.35),
    "JPM":   (200.0,  0.06, 0.20),
    "V":     (275.0,  0.07, 0.18),
    "NFLX":  (625.0,  0.08, 0.35),
}

# 未知銘柄: ランダム初期価格・標準パラメータ
# price_range から uniform でサンプリング
UNKNOWN_DEFAULTS = {"drift": 0.07, "vol": 0.30, "price_range": (50.0, 200.0)}
```

#### 4-2. GBM 数式

```
S(t+Δt) = S(t) × exp((μ - σ²/2)·Δt + σ·√Δt·Z)
```

- `Δt = UPDATE_INTERVAL / SECONDS_PER_YEAR = 0.5 / 31_536_000`
- `Z` は相関乱数（Cholesky 分解で生成）

#### 4-3. 相関行列の構築

```python
TECH_TICKERS    = {"AAPL", "GOOGL", "MSFT", "AMZN", "NVDA", "META", "NFLX", "TSLA"}
FINANCE_TICKERS = {"JPM", "V"}

def _build_corr_matrix(self, tickers: list[str]) -> np.ndarray:
    """銘柄間相関行列を構築する。
    
    同一グループ内: 相関係数 0.4
    異グループ間 : 相関係数 0.1
    対角成分    : 1.0
    """
    n = len(tickers)
    matrix = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            t1, t2 = tickers[i], tickers[j]
            same_tech = t1 in TECH_TICKERS and t2 in TECH_TICKERS
            same_fin  = t1 in FINANCE_TICKERS and t2 in FINANCE_TICKERS
            corr = 0.4 if (same_tech or same_fin) else 0.1
            matrix[i, j] = matrix[j, i] = corr
    return matrix
```

#### 4-4. ランダムイベント

```python
EVENT_PROB             = 0.002   # 1ステップあたり 0.2%（≈ 10分に1回）
EVENT_MAGNITUDE_RANGE  = (0.02, 0.05)  # 2〜5% の急騰/急落

# _step() 内で各銘柄に適用
if random.random() < EVENT_PROB:
    magnitude  = random.uniform(*EVENT_MAGNITUDE_RANGE)
    direction  = random.choice([1, -1])
    new_price *= (1 + direction * magnitude)
```

#### 4-5. 完全実装

```python
# backend/market/simulator.py
import asyncio
import math
import random
from datetime import datetime, timezone

import numpy as np

from backend.market.base import MarketDataSource, PriceUpdate

UPDATE_INTERVAL  = 0.5           # 秒
SECONDS_PER_YEAR = 31_536_000
EVENT_PROB       = 0.002


class MarketSimulator(MarketDataSource):
    def __init__(self) -> None:
        self._watched: set[str]                            = set()
        self._prices:  dict[str, float]                   = {}
        # ticker -> (init_price, drift, vol)
        self._params:  dict[str, tuple[float, float, float]] = {}
        self._running: bool                                = False

    # ── 公開 API ─────────────────────────────────────────

    def add_ticker(self, ticker: str) -> None:
        t = ticker.upper()
        self._watched.add(t)
        self._init_ticker(t)

    def remove_ticker(self, ticker: str) -> None:
        self._watched.discard(ticker.upper())

    def get_watched_tickers(self) -> set[str]:
        return set(self._watched)

    async def start(self, price_cache: dict[str, PriceUpdate]) -> None:
        self._running = True
        dt = UPDATE_INTERVAL / SECONDS_PER_YEAR
        while self._running:
            tickers = list(self._watched)
            if tickers:
                self._step(tickers, dt, price_cache)
            await asyncio.sleep(UPDATE_INTERVAL)

    async def stop(self) -> None:
        self._running = False

    # ── 内部処理 ─────────────────────────────────────────

    def _init_ticker(self, ticker: str) -> None:
        """既知パラメータまたはランダム値で銘柄を初期化する。"""
        if ticker in self._prices:
            return  # 既に初期化済み
        from backend.market.simulator import DEFAULT_TICKER_PARAMS
        if ticker in DEFAULT_TICKER_PARAMS:
            init_price, drift, vol = DEFAULT_TICKER_PARAMS[ticker]
        else:
            init_price = random.uniform(50.0, 200.0)
            drift, vol = 0.07, 0.30
        self._prices[ticker] = init_price
        self._params[ticker] = (init_price, drift, vol)

    def _step(
        self,
        tickers: list[str],
        dt: float,
        price_cache: dict[str, PriceUpdate],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        corr = self._build_corr_matrix(tickers)
        L    = np.linalg.cholesky(corr)
        z    = L @ np.random.standard_normal(len(tickers))

        for i, ticker in enumerate(tickers):
            _, drift, vol = self._params[ticker]
            current       = self._prices[ticker]

            # GBM ステップ
            new_price = current * math.exp(
                (drift - 0.5 * vol ** 2) * dt + vol * math.sqrt(dt) * z[i]
            )

            # ランダムイベント（低確率の急騰/急落）
            if random.random() < EVENT_PROB:
                mag        = random.uniform(0.02, 0.05)
                new_price *= 1 + random.choice([1, -1]) * mag

            # 下限: 0.01ドル
            new_price = max(new_price, 0.01)

            price_cache[ticker] = PriceUpdate(
                ticker     = ticker,
                price      = round(new_price, 4),
                prev_price = round(current, 4),
                timestamp  = now,
            )
            self._prices[ticker] = new_price

    def _build_corr_matrix(self, tickers: list[str]) -> np.ndarray:
        n      = len(tickers)
        matrix = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                t1, t2    = tickers[i], tickers[j]
                same_tech = t1 in TECH_TICKERS and t2 in TECH_TICKERS
                same_fin  = t1 in FINANCE_TICKERS and t2 in FINANCE_TICKERS
                corr      = 0.4 if (same_tech or same_fin) else 0.1
                matrix[i, j] = matrix[j, i] = corr
        return matrix
```

---

## 5. Massive API クライアント

### `backend/market/massive_client.py`

```python
import asyncio
import os
from datetime import datetime, timezone

import httpx

from backend.market.base import MarketDataSource, PriceUpdate

MASSIVE_BASE_URL        = "https://api.polygon.io"
DEFAULT_POLL_INTERVAL   = 5    # 有料ティア向けデフォルト（秒）
FREE_TIER_POLL_INTERVAL = 15   # 無料ティア向け（秒）


class MassiveMarketClient(MarketDataSource):
    def __init__(self, api_key: str, poll_interval: int | None = None) -> None:
        self._api_key       = api_key
        self._poll_interval = poll_interval or int(
            os.environ.get("MASSIVE_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL)
        )
        self._watched: set[str]              = set()
        self._running: bool                  = False
        self._client: httpx.AsyncClient | None = None

    # ── 公開 API ─────────────────────────────────────────

    def add_ticker(self, ticker: str) -> None:
        self._watched.add(ticker.upper())

    def remove_ticker(self, ticker: str) -> None:
        self._watched.discard(ticker.upper())

    def get_watched_tickers(self) -> set[str]:
        return set(self._watched)

    async def start(self, price_cache: dict[str, PriceUpdate]) -> None:
        self._running = True
        self._client  = httpx.AsyncClient(
            headers = {"Authorization": f"Bearer {self._api_key}"},
            timeout = 10.0,
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

    async def validate_ticker(self, ticker: str) -> bool:
        """Massive API がティッカーを認識しているか確認する。
        
        ウォッチリスト追加・取引実行前の検証に使用する。
        シミュレーターモードでは呼ばれない。
        """
        client = self._client or httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self._api_key}"}, timeout=10.0
        )
        try:
            resp = await client.get(
                f"{MASSIVE_BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker.upper()}"
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
        finally:
            if self._client is None:
                await client.aclose()

    # ── 内部処理 ─────────────────────────────────────────

    async def _poll_once(self, price_cache: dict[str, PriceUpdate]) -> None:
        """全ウォッチリスト銘柄を1リクエストで一括取得し、キャッシュを更新する。"""
        tickers_param = ",".join(self._watched)
        try:
            resp = await self._client.get(
                f"{MASSIVE_BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers",
                params={"tickers": tickers_param},
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            return  # 一時的な障害はスキップ（次のポーリングで再試行）

        now = datetime.now(timezone.utc).isoformat()
        for item in resp.json().get("tickers", []):
            ticker = item["ticker"]
            # lastTrade.p（最終取引価格）を優先、なければ day.c（当日終値）
            price = (
                item.get("lastTrade", {}).get("p")
                or item.get("day", {}).get("c")
            )
            if price is None:
                continue

            prev = price_cache.get(ticker)
            price_cache[ticker] = PriceUpdate(
                ticker     = ticker,
                price      = round(float(price), 4),
                prev_price = round(prev.price if prev else float(price), 4),
                timestamp  = now,
            )
```

---

## 6. ファクトリ関数

### `backend/market/factory.py`

```python
import os

from backend.market.base import MarketDataSource
from backend.market.massive_client import MassiveMarketClient
from backend.market.simulator import MarketSimulator


def create_market_source(initial_tickers: list[str]) -> MarketDataSource:
    """環境変数に基づいてマーケットデータソースを生成する。
    
    MASSIVE_API_KEY が非空文字列なら MassiveMarketClient を返す。
    未設定または空の場合は MarketSimulator を返す。
    initial_tickers は DB から読み込んだウォッチリスト銘柄。
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    source: MarketDataSource = (
        MassiveMarketClient(api_key=api_key) if api_key else MarketSimulator()
    )
    for ticker in initial_tickers:
        source.add_ticker(ticker)
    return source
```

---

## 7. FastAPI への組み込み

### `backend/main.py`（lifespan 抜粋）

```python
from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.db.init import init_db, get_watchlist_tickers
from backend.market.cache import price_cache
from backend.market.factory import create_market_source

# アプリ全体で参照できるグローバル変数
market_source = None
market_task   = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global market_source, market_task

    # 1. DB 初期化（スキーマ作成 + シードデータ投入）
    await init_db()

    # 2. ウォッチリスト銘柄を DB から取得
    initial_tickers = await get_watchlist_tickers()

    # 3. マーケットデータソースをバックグラウンドタスクとして起動
    market_source = create_market_source(initial_tickers)
    market_task   = asyncio.create_task(market_source.start(price_cache))

    # 4. ポートフォリオスナップショットタスクも起動
    snapshot_task = asyncio.create_task(portfolio_snapshot_loop())

    yield  # アプリ稼働中

    # 5. シャットダウン時のクリーンアップ
    await market_source.stop()
    market_task.cancel()
    snapshot_task.cancel()


app = FastAPI(lifespan=lifespan)

# 静的ファイル（Next.js ビルド成果物）をルートから配信
app.mount("/", StaticFiles(directory="static", html=True), name="static")
```

---

## 8. SSE ストリームエンドポイント

### `backend/routers/stream.py`

```python
import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from backend.market.cache import price_cache

router = APIRouter()


@router.get("/api/stream/prices")
async def prices_stream():
    """SSE エンドポイント。約 500ms ごとに全銘柄の価格更新をプッシュする。
    
    クライアントは EventSource API で接続する。
    再接続は EventSource が自動的に処理する。
    """
    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",  # Nginx プロキシ環境でバッファリングを無効化
        },
    )


async def _event_generator():
    while True:
        if price_cache:
            updates = [
                {
                    "ticker":     v.ticker,
                    "price":      v.price,
                    "prev_price": v.prev_price,
                    "direction":  (
                        "up"   if v.price > v.prev_price else
                        "down" if v.price < v.prev_price else
                        "flat"
                    ),
                }
                for v in price_cache.values()
            ]
            payload = json.dumps({
                "type":      "prices",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "prices":    updates,
            })
            yield f"data: {payload}\n\n"

        await asyncio.sleep(0.5)
```

**SSE イベント形式:**

```json
data: {
  "type": "prices",
  "timestamp": "2026-05-28T09:30:00.000000+00:00",
  "prices": [
    { "ticker": "AAPL", "price": 190.12, "prev_price": 189.80, "direction": "up" },
    { "ticker": "GOOGL", "price": 174.50, "prev_price": 174.55, "direction": "down" }
  ]
}
```

---

## 9. ウォッチリスト API とキャッシュ連携

### `backend/routers/watchlist.py`（抜粋）

```python
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.db import watchlist_db
from backend.market.cache import price_cache
from backend import state  # market_source への参照

router = APIRouter()

WATCHLIST_LIMIT = 50


class AddTickerRequest(BaseModel):
    ticker: str


@router.get("/api/watchlist")
async def get_watchlist():
    """ウォッチリスト銘柄を最新価格付きで返す。
    
    price_cache にエントリがない銘柄は price: null を返す。
    """
    items = await watchlist_db.get_all()
    return {
        "watchlist": [
            {
                "ticker":   item.ticker,
                "price":    price_cache[item.ticker].price if item.ticker in price_cache else None,
                "added_at": item.added_at,
            }
            for item in items
        ]
    }


@router.post("/api/watchlist", status_code=status.HTTP_201_CREATED)
async def add_to_watchlist(body: AddTickerRequest):
    ticker = body.ticker.upper().strip()

    # 件数上限チェック
    count = await watchlist_db.count()
    if count >= WATCHLIST_LIMIT:
        raise HTTPException(
            status_code=422,
            detail=f"ウォッチリストの上限（{WATCHLIST_LIMIT}件）に達しています。",
        )

    # Massive API モードの場合: ティッカー存在確認
    if hasattr(state.market_source, "validate_ticker"):
        valid = await state.market_source.validate_ticker(ticker)
        if not valid:
            raise HTTPException(status_code=404, detail=f"{ticker} は有効なティッカーではありません。")

    # DB に追加
    added = await watchlist_db.add(ticker)

    # マーケットソースにも追加（価格更新開始）
    state.market_source.add_ticker(ticker)

    return {"ticker": added.ticker, "added_at": added.added_at}


@router.delete("/api/watchlist/{ticker}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_watchlist(ticker: str):
    ticker = ticker.upper()

    await watchlist_db.remove(ticker)
    state.market_source.remove_ticker(ticker)

    # ポジションがなければキャッシュからも削除
    has_position = await portfolio_db.has_position(ticker)
    if not has_position:
        price_cache.pop(ticker, None)
```

---

## 10. ポートフォリオスナップショット タスク

### `backend/tasks/snapshot.py`

ポートフォリオの時系列価値を30秒ごとに記録する専用バックグラウンドタスク。取引実行直後にも呼び出す。

```python
import asyncio
from datetime import datetime, timezone
import uuid

from backend.db import portfolio_db, snapshot_db
from backend.market.cache import price_cache

SNAPSHOT_INTERVAL = 30  # 秒


async def portfolio_snapshot_loop() -> None:
    """30 秒ごとにポートフォリオの総価値を DB に記録する。"""
    while True:
        await asyncio.sleep(SNAPSHOT_INTERVAL)
        await record_snapshot()


async def record_snapshot() -> None:
    """現在のポートフォリオ総価値を計算し DB に保存する。
    
    取引実行後にも明示的に呼び出す。
    """
    profile   = await portfolio_db.get_profile()
    positions = await portfolio_db.get_positions()

    total_value = profile.cash_balance
    for pos in positions:
        cached = price_cache.get(pos.ticker)
        if cached:
            total_value += pos.quantity * cached.price

    await snapshot_db.insert({
        "id":           str(uuid.uuid4()),
        "user_id":      "default",
        "total_value":  total_value,
        "recorded_at":  datetime.now(timezone.utc).isoformat(),
    })
```

---

## 11. ティッカー検証方針

| モード | 未知ティッカーの扱い | `validate_ticker` 呼出 |
|--------|---------------------|------------------------|
| シミュレーター | 50〜200ドルのランダム価格で動的生成。404 は返さない | 不要 |
| Massive API | `validate_ticker()` で確認。API が認識しない場合のみ 422 | ウォッチリスト追加・取引時 |

```python
# ティッカー検証のユーティリティ
async def ensure_ticker_valid(ticker: str) -> None:
    """Massive API モードのみ実行。シミュレーターモードでは何もしない。"""
    from backend import state
    if hasattr(state.market_source, "validate_ticker"):
        valid = await state.market_source.validate_ticker(ticker)
        if not valid:
            raise HTTPException(
                status_code=404,
                detail=f"{ticker} は Massive API で認識されない銘柄です。",
            )
```

---

## 12. 依存パッケージ

```toml
# backend/pyproject.toml（market 関連の依存）
[project]
dependencies = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "httpx>=0.27",        # Massive API クライアント（async 対応）
    "numpy>=1.26",        # GBM シミュレーター（Cholesky 分解）
    "aiosqlite>=0.20",    # 非同期 SQLite
    "bleach>=6.1",        # LLM 出力サニタイズ
    "litellm>=1.40",      # OpenRouter 経由の LLM 呼び出し
]
```

---

## 13. テスト方針とサンプルコード

### `backend/tests/test_simulator.py`

```python
import asyncio
import pytest
import numpy as np

from backend.market.simulator import MarketSimulator
from backend.market.base import PriceUpdate


def test_unknown_ticker_dynamic_generation():
    sim = MarketSimulator()
    sim.add_ticker("FOOBAR")
    assert "FOOBAR" in sim._prices
    assert 50.0 <= sim._prices["FOOBAR"] <= 200.0


def test_price_update_is_positive():
    sim   = MarketSimulator()
    cache: dict[str, PriceUpdate] = {}
    sim.add_ticker("AAPL")
    dt = 0.5 / 31_536_000
    sim._step(["AAPL"], dt, cache)
    assert cache["AAPL"].price > 0
    assert cache["AAPL"].ticker == "AAPL"


def test_prev_price_set_on_second_step():
    sim   = MarketSimulator()
    cache: dict[str, PriceUpdate] = {}
    sim.add_ticker("MSFT")
    dt = 0.5 / 31_536_000
    sim._step(["MSFT"], dt, cache)
    first_price = cache["MSFT"].price
    sim._step(["MSFT"], dt, cache)
    assert cache["MSFT"].prev_price == first_price


def test_correlation_matrix_is_positive_definite():
    sim      = MarketSimulator()
    tickers  = ["AAPL", "MSFT", "JPM"]
    matrix   = sim._build_corr_matrix(tickers)
    eigvals  = np.linalg.eigvalsh(matrix)
    assert all(e > 0 for e in eigvals), "相関行列が正定値でない"


def test_add_remove_ticker():
    sim = MarketSimulator()
    sim.add_ticker("TSLA")
    assert "TSLA" in sim.get_watched_tickers()
    sim.remove_ticker("TSLA")
    assert "TSLA" not in sim.get_watched_tickers()


@pytest.mark.asyncio
async def test_simulator_runs_one_cycle():
    sim   = MarketSimulator()
    sim.add_ticker("AAPL")
    cache: dict[str, PriceUpdate] = {}

    async def run_briefly():
        task = asyncio.create_task(sim.start(cache))
        await asyncio.sleep(0.6)   # 1 サイクル分だけ待機
        await sim.stop()
        task.cancel()

    await run_briefly()
    assert "AAPL" in cache
```

### `backend/tests/test_massive_client.py`

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from backend.market.massive_client import MassiveMarketClient
from backend.market.base import PriceUpdate

SAMPLE_RESPONSE = {
    "status": "OK",
    "tickers": [
        {
            "ticker": "AAPL",
            "lastTrade": {"p": 190.12},
            "day": {"c": 188.0},
        }
    ],
}


@pytest.mark.asyncio
async def test_poll_once_updates_cache():
    client = MassiveMarketClient(api_key="test-key", poll_interval=5)
    client._watched = {"AAPL"}

    mock_resp = MagicMock()
    mock_resp.json.return_value = SAMPLE_RESPONSE
    mock_resp.raise_for_status = MagicMock()

    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=mock_resp)

    cache: dict[str, PriceUpdate] = {}
    await client._poll_once(cache)

    assert "AAPL" in cache
    assert cache["AAPL"].price == 190.12


@pytest.mark.asyncio
async def test_poll_once_uses_day_close_as_fallback():
    client = MassiveMarketClient(api_key="test-key")
    client._watched = {"AAPL"}

    response_no_trade = {
        "tickers": [{"ticker": "AAPL", "lastTrade": {}, "day": {"c": 188.0}}]
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = response_no_trade
    mock_resp.raise_for_status = MagicMock()
    client._client = AsyncMock()
    client._client.get = AsyncMock(return_value=mock_resp)

    cache: dict[str, PriceUpdate] = {}
    await client._poll_once(cache)
    assert cache["AAPL"].price == 188.0


@pytest.mark.asyncio
async def test_poll_once_skips_on_http_error():
    import httpx
    client = MassiveMarketClient(api_key="test-key")
    client._watched = {"AAPL"}
    client._client  = AsyncMock()
    client._client.get = AsyncMock(side_effect=httpx.HTTPError("timeout"))

    cache: dict[str, PriceUpdate] = {}
    await client._poll_once(cache)  # 例外が伝播しないことを確認
    assert cache == {}
```

### `backend/tests/test_factory.py`

```python
import os
import pytest
from unittest.mock import patch

from backend.market.factory import create_market_source
from backend.market.simulator import MarketSimulator
from backend.market.massive_client import MassiveMarketClient


def test_returns_simulator_when_no_api_key():
    with patch.dict(os.environ, {"MASSIVE_API_KEY": ""}):
        source = create_market_source(["AAPL", "MSFT"])
    assert isinstance(source, MarketSimulator)
    assert "AAPL" in source.get_watched_tickers()


def test_returns_massive_client_when_api_key_set():
    with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key-xyz"}):
        source = create_market_source(["GOOGL"])
    assert isinstance(source, MassiveMarketClient)
    assert "GOOGL" in source.get_watched_tickers()
```

---

## 14. 実装チェックリスト

バックエンド実装者がすべての機能を網羅したか確認するためのリスト。

- [ ] `backend/market/base.py` — `PriceUpdate` データクラスと `MarketDataSource` ABC
- [ ] `backend/market/cache.py` — シングルトン `price_cache` 辞書
- [ ] `backend/market/simulator.py` — GBM シミュレーター（全パラメータ・相関・ランダムイベント）
- [ ] `backend/market/massive_client.py` — REST ポーリングクライアント（httpx.AsyncClient）
- [ ] `backend/market/factory.py` — `MASSIVE_API_KEY` で切り替えるファクトリ
- [ ] `backend/main.py` — `lifespan` フック内での DB 初期化・マーケットソース起動
- [ ] `backend/routers/stream.py` — SSE エンドポイント（500ms 間隔・direction フィールド付き）
- [ ] `backend/routers/watchlist.py` — 追加/削除時に `market_source` と `price_cache` を同期
- [ ] `backend/tasks/snapshot.py` — 30秒ごとおよび取引直後のスナップショット記録
- [ ] ウォッチリスト削除時に保有ポジションがない銘柄の `price_cache` エントリを削除
- [ ] Massive API モードでのティッカー検証（`validate_ticker`）を追加・取引API に組み込む
- [ ] `uv add numpy httpx aiosqlite bleach litellm` で依存を追加し `uv.lock` を更新
- [ ] `pytest` でシミュレーター・Massive クライアント・ファクトリのテストをすべてパス
