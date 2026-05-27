# 株価シミュレーター設計

`MASSIVE_API_KEY` が未設定の場合に使用する組み込み市場シミュレーター。
外部依存なし。プロセス内 asyncio タスクとして動作する。

---

## シミュレーション手法: 幾何ブラウン運動（GBM）

株価モデルの標準手法。価格が常に正の値を保ち、現実的なランダムウォークを再現する。

### 数式

```
S(t+Δt) = S(t) × exp((μ - σ²/2)Δt + σ√Δt × Z)
```

- `S(t)` — 現在の価格
- `μ` (mu/drift) — 期待リターン（年率）。銘柄ごとに設定
- `σ` (sigma/volatility) — ボラティリティ（年率）。銘柄ごとに設定
- `Δt` — 時間ステップ（秒単位を年換算: `interval_sec / 31536000`）
- `Z` — 標準正規乱数 `N(0,1)`

### なぜ GBM か

- 価格が必ず正の値になる（対数正規分布）
- 銘柄ごとのドリフト・ボラティリティで個性を表現できる
- 銘柄間の相関を再現可能（Cholesky 分解）
- 実装がシンプルで NumPy のみで完結

---

## 銘柄パラメータ

デフォルト10銘柄の現実的な初期設定。

```python
DEFAULT_TICKER_PARAMS = {
    #         初期価格   ドリフト(年率)  ボラティリティ(年率)
    "AAPL":  (190.0,    0.08,           0.25),
    "GOOGL": (175.0,    0.10,           0.28),
    "MSFT":  (415.0,    0.09,           0.22),
    "AMZN":  (185.0,    0.12,           0.30),
    "TSLA":  (250.0,    0.05,           0.60),  # 高ボラ
    "NVDA":  (875.0,    0.15,           0.50),  # 高ボラ
    "META":  (510.0,    0.10,           0.35),
    "JPM":   (200.0,    0.06,           0.20),
    "V":     (275.0,    0.07,           0.18),
    "NFLX":  (625.0,    0.08,           0.35),
}

# 未知銘柄のデフォルトパラメータ
UNKNOWN_TICKER_DEFAULTS = {
    "drift": 0.07,
    "volatility": 0.30,
    "price_range": (50.0, 200.0),  # 初期価格のランダム範囲
}
```

---

## 銘柄間の相関

ハイテク株は連動して動く。Cholesky 分解で相関した乱数を生成する。

```python
import numpy as np

# ハイテク株グループの相関行列（簡略版）
TECH_TICKERS = {"AAPL", "GOOGL", "MSFT", "AMZN", "NVDA", "META", "NFLX", "TSLA"}
FINANCE_TICKERS = {"JPM", "V"}

def generate_correlated_noise(tickers: list[str], correlation: float = 0.4) -> np.ndarray:
    """相関した標準正規乱数を生成する。"""
    n = len(tickers)
    # 簡易相関行列: 同一グループ内は correlation、異グループは 0.1
    corr_matrix = np.full((n, n), 0.1)
    np.fill_diagonal(corr_matrix, 1.0)
    
    for i, t1 in enumerate(tickers):
        for j, t2 in enumerate(tickers):
            if i != j:
                same_tech = t1 in TECH_TICKERS and t2 in TECH_TICKERS
                same_fin = t1 in FINANCE_TICKERS and t2 in FINANCE_TICKERS
                if same_tech or same_fin:
                    corr_matrix[i, j] = correlation

    L = np.linalg.cholesky(corr_matrix)
    z = np.random.standard_normal(n)
    return L @ z
```

---

## ランダムイベント

ドラマチックな演出として、時折大きな価格変動を発生させる。

```python
import random

EVENT_PROBABILITY = 0.002  # 1ステップあたり 0.2% の確率（500ms × 0.2% ≈ 10分に1回）
EVENT_MAGNITUDE_RANGE = (0.02, 0.05)  # 2〜5% の急騰/急落

def apply_random_event(price: float) -> float:
    """低確率で大きな価格変動を発生させる。"""
    if random.random() < EVENT_PROBABILITY:
        magnitude = random.uniform(*EVENT_MAGNITUDE_RANGE)
        direction = random.choice([1, -1])
        return price * (1 + direction * magnitude)
    return price
```

---

## シミュレーター実装

```python
# backend/market/simulator.py
import asyncio
import math
import random
from datetime import datetime, timezone

import numpy as np

from backend.market.base import MarketDataSource, PriceUpdate

UPDATE_INTERVAL = 0.5  # 500ms ごとに更新

DEFAULT_TICKER_PARAMS = {
    "AAPL":  (190.0, 0.08, 0.25),
    "GOOGL": (175.0, 0.10, 0.28),
    "MSFT":  (415.0, 0.09, 0.22),
    "AMZN":  (185.0, 0.12, 0.30),
    "TSLA":  (250.0, 0.05, 0.60),
    "NVDA":  (875.0, 0.15, 0.50),
    "META":  (510.0, 0.10, 0.35),
    "JPM":   (200.0, 0.06, 0.20),
    "V":     (275.0, 0.07, 0.18),
    "NFLX":  (625.0, 0.08, 0.35),
}

TECH_TICKERS = {"AAPL", "GOOGL", "MSFT", "AMZN", "NVDA", "META", "NFLX", "TSLA"}
FINANCE_TICKERS = {"JPM", "V"}

EVENT_PROB = 0.002
SECONDS_PER_YEAR = 31_536_000


class MarketSimulator(MarketDataSource):
    def __init__(self):
        self._watched: set[str] = set()
        self._prices: dict[str, float] = {}
        self._params: dict[str, tuple[float, float, float]] = {}  # ticker -> (price, drift, vol)
        self._running = False

    def _init_ticker(self, ticker: str) -> None:
        """銘柄を初期化する（既知パラメータ or ランダム生成）。"""
        if ticker not in self._prices:
            if ticker in DEFAULT_TICKER_PARAMS:
                init_price, drift, vol = DEFAULT_TICKER_PARAMS[ticker]
            else:
                init_price = random.uniform(50.0, 200.0)
                drift = 0.07
                vol = 0.30
            self._prices[ticker] = init_price
            self._params[ticker] = (init_price, drift, vol)

    def add_ticker(self, ticker: str) -> None:
        self._watched.add(ticker.upper())
        self._init_ticker(ticker.upper())

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

    def _step(
        self,
        tickers: list[str],
        dt: float,
        price_cache: dict[str, PriceUpdate],
    ) -> None:
        """GBM で全銘柄の価格を1ステップ更新する。"""
        now = datetime.now(timezone.utc).isoformat()

        # 相関乱数の生成
        n = len(tickers)
        corr_matrix = self._build_corr_matrix(tickers)
        L = np.linalg.cholesky(corr_matrix)
        z = L @ np.random.standard_normal(n)

        for i, ticker in enumerate(tickers):
            _, drift, vol = self._params[ticker]
            current = self._prices[ticker]

            # GBM ステップ
            new_price = current * math.exp(
                (drift - 0.5 * vol**2) * dt + vol * math.sqrt(dt) * z[i]
            )

            # ランダムイベント
            if random.random() < EVENT_PROB:
                magnitude = random.uniform(0.02, 0.05)
                direction = random.choice([1, -1])
                new_price *= 1 + direction * magnitude

            # 下限: 0.01ドル（ゼロ以下にならないよう保護）
            new_price = max(new_price, 0.01)

            price_cache[ticker] = PriceUpdate(
                ticker=ticker,
                price=round(new_price, 4),
                prev_price=round(current, 4),
                timestamp=now,
            )
            self._prices[ticker] = new_price

    def _build_corr_matrix(self, tickers: list[str]) -> np.ndarray:
        """銘柄間の相関行列を構築する。"""
        n = len(tickers)
        matrix = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                t1, t2 = tickers[i], tickers[j]
                same_tech = t1 in TECH_TICKERS and t2 in TECH_TICKERS
                same_fin = t1 in FINANCE_TICKERS and t2 in FINANCE_TICKERS
                corr = 0.4 if (same_tech or same_fin) else 0.1
                matrix[i, j] = corr
                matrix[j, i] = corr
        return matrix
```

---

## 動作の特徴まとめ

| 特性 | 詳細 |
|------|------|
| 更新間隔 | 500ms |
| 価格モデル | 幾何ブラウン運動（GBM） |
| 銘柄間相関 | 同一グループ内 0.4、異グループ 0.1 |
| ランダムイベント | 確率 0.2%/ステップ、変動幅 2〜5% |
| 未知銘柄 | 動的生成（初期価格 50〜200ドル、標準パラメータ） |
| 価格下限 | 0.01ドル（負の価格を防止） |
| 外部依存 | numpy のみ（httpx 不要） |

---

## テスト方針

```python
# tests/test_simulator.py
import asyncio
import pytest
from backend.market.simulator import MarketSimulator
from backend.market.cache import price_cache

@pytest.mark.asyncio
async def test_price_update():
    sim = MarketSimulator()
    sim.add_ticker("AAPL")
    cache = {}
    
    # 1ステップだけ実行
    sim._step(["AAPL"], 0.5 / 31_536_000, cache)
    
    assert "AAPL" in cache
    assert cache["AAPL"].price > 0
    assert cache["AAPL"].ticker == "AAPL"

def test_unknown_ticker_dynamic_generation():
    sim = MarketSimulator()
    sim.add_ticker("UNKNOWN")
    assert "UNKNOWN" in sim._prices
    assert 50.0 <= sim._prices["UNKNOWN"] <= 200.0

def test_correlation_matrix_is_positive_definite():
    import numpy as np
    sim = MarketSimulator()
    tickers = ["AAPL", "MSFT", "JPM"]
    matrix = sim._build_corr_matrix(tickers)
    eigenvalues = np.linalg.eigvalsh(matrix)
    assert all(e > 0 for e in eigenvalues)  # 正定値行列であることを確認
```
