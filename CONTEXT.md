# Stock Agent AI — CONTEXT.md
> Đọc file này trước khi làm bất cứ thứ gì.

## 🏗️ Stack & Deploy
- **Backend**: Python FastAPI (`server.py`) — `uvicorn server:app --reload --port 8000`
- **Frontend**: Single-file `dashboard.html` — served từ FastAPI
- **Data**: `vnstock` 4.x (đã uninstall vnstock3) — `from vnstock import ...`
- **AI**: Anthropic Claude Haiku API — env var `ANTHROPIC_API_KEY`
- **Deploy**: Railway.app — push GitHub → auto deploy
- **Python**: 3.12, virtualenv tại `~/stock-env`

## 👥 Users & Auth
- Multi-user, PIN 4 số
- Session token lưu in-memory (`SESSIONS` dict) + cookie `sa_token`
- User data lưu file JSON riêng theo từng user
- `users.json` — danh sách user + PIN
- `calc_data/{username}.json` — lịch sử calculator
- `watchlist_{username}.json` — watchlist riêng từng user (**chưa làm, đang dùng chung**)
- `daily_picks.json` — kết quả scan chung (ok, không cần per-user)

## 📁 Files chính
```
server.py          ← FastAPI backend, tất cả API endpoints
dashboard.html     ← Frontend single-file
users.json         ← Auth data
watchlist.json     ← Watchlist CHUNG (cần tách per-user — xem Phase 1.4)
daily_picks.json   ← Kết quả scan gần nhất
calc_data/         ← Lịch sử calculator per-user
requirements.txt   ← fastapi uvicorn httpx pytz pandas vnstock anthropic
render.yaml        ← Ignore, dùng Railway không dùng Render
```

## 🔌 API Endpoints hiện có
| Endpoint | Mô tả |
|---|---|
| `GET /` | Dashboard HTML |
| `GET /api/health` | Health check |
| `POST /api/auth/register` | Đăng ký (username + PIN) |
| `POST /api/auth/login` | Đăng nhập → cookie + token |
| `POST /api/auth/logout` | Đăng xuất |
| `GET /api/auth/me` | Check session |
| `GET /api/watchlist` | Xem watchlist |
| `POST /api/watchlist` | Set watchlist |
| `POST /api/watchlist/add` | Thêm mã |
| `DELETE /api/watchlist/{ticker}` | Xóa mã |
| `GET /api/daily-picks` | Kết quả scan gần nhất |
| `POST /api/scan` | Trigger scan thủ công |
| `GET /api/scan-stream` | SSE stream scan từng mã |
| `GET /api/fetch/{ticker}` | Kéo BCTC từ VCI |
| `GET /api/buffett-score/{ticker}` | Tính điểm Buffett nhanh |
| `GET /api/history/{ticker}` | Lịch sử giá (**BUG: thiếu decorator**) |
| `GET /api/technical/{ticker}` | RSI, MACD, MA, Bollinger |
| `GET /api/price-board` | Bảng giá nhiều mã |
| `POST /api/analyze-claude` | Gọi Claude API phân tích |
| `GET /api/ai-picks` | Daily picks + AI commentary |
| `POST /api/calc/avg-price` | Calculator trung bình giá |
| `POST /api/calc/dao-hang` | Calculator đảo hàng |
| `GET /api/calc/history` | Lịch sử calculator |
| `DELETE /api/calc/history/{id}` | Xóa bản ghi lịch sử |
| `GET /api/debug/{ticker}` | Debug item_en names |

## 🧮 Logic quan trọng

### Đơn vị dữ liệu
- Raw DataFrame từ vnstock = đồng VNĐ gốc
- `to_mil()` = chia 1,000,000 → triệu VNĐ
- Frontend hiển thị: chia thêm 1,000 → tỷ VNĐ

### Tính số CP lưu hành
```python
shares = Paid-in capital (đồng gốc) / 10_000  # mệnh giá 10,000đ
```

### Tính P/E
```python
EPS = LNST_parent (đồng gốc) / shares
P/E = Giá (đồng) / EPS
```

### ROE đúng
```python
ROE = LNST_total / VCSH_bình_quân  # (đầu kỳ + cuối kỳ) / 2
VCSH = Owner's Equity - Minority interests
```

### ROA đúng
```python
ROA = LNST_parent / TS_bình_quân  # dùng LNST cổ đông công ty mẹ
```

### LNST đúng (quan trọng!)
```python
# SAI — đang dùng:
"Net profit/(loss) after tax"          # bao gồm minority interests

# ĐÚNG — phải dùng:
"Attributable to parent company"       # LNST cổ đông công ty mẹ
```

### vnstock keyword mapping (item_en)
| Chỉ tiêu | item_en |
|---|---|
| Doanh thu thuần | `Net sales` |
| Lợi nhuận gộp | `Gross Profit` |
| Lãi vay | `Interest expenses` |
| CP bán hàng | `Selling expenses` |
| CP QLDN | `General and admin expenses` |
| LN trước thuế | `Net accounting profit/(loss) before tax` |
| LN sau thuế toàn bộ | `Net profit/(loss) after tax` |
| LN sau thuế cổ đông mẹ | `Attributable to parent company` |
| Tiền | `Cash and cash equivalents` |
| Hàng tồn kho | `Inventories, Net` |
| Tài sản ngắn hạn | `CURRENT ASSETS` |
| Tổng tài sản | `Total Assets` |
| Nợ ngắn hạn | `Current liabilities` |
| Nợ dài hạn | `Long-term liabilities` |
| Vốn chủ sở hữu | `Owner's Equity` |
| Lợi ích cổ đông thiểu số | `Minority interests` |
| Vốn góp | `Paid-in capital` |

### parseInput frontend (vi-VN format)
- Dấu chấm = phân ngàn, dấu phẩy = thập phân
- `"58.137.438,25"` → `58137438.25`
- `"58.137"` (1 dấu chấm, phần trước ≤ 2 chữ số) → `58.137` (thập phân)

### SafeEncoder — KHÔNG XÓA
```python
# Xử lý NaN/Inf từ pandas — bắt buộc giữ lại
class SafeEncoder(json.JSONEncoder): ...
```

### Phát hiện ngân hàng
```python
is_bank = find_row(inc, "Net Interest Income") is not None
# Ngân hàng dùng mapping khác cho income statement
```

---

## 🚀 ROADMAP — Làm theo thứ tự

---

### PHASE 1 — Fix & Polish (làm trước)

#### 1.1 Fix bug `get_history` thiếu decorator
```python
# Thêm decorator này vào trước def get_history() trong server.py
@app.get("/api/history/{ticker}")
def get_history(
    ticker: str,
    start: str = Query("2024-01-01"),
    end: Optional[str] = Query(None),
    interval: str = Query("d"),
):
```

#### 1.2 Migrate vnstock API
- Bỏ `Vnstock().stock()` (deprecated)
- Migrate sang `Finance(symbol, source)` và `Quote(symbol, source)`
- Cập nhật `get_vnstock()`, `get_quote()`, `get_trading()`
- Test với: BMP, FPT, VCB

#### 1.3 Fix LNST cho ROE/ROA
- Debug tại sao `lnst_parent` không được dùng đúng trong `compute_buffett_score()`
- Đảm bảo ROE dùng `lnst_total / avg_vcsh`
- Đảm bảo ROA dùng `lnst_parent / avg_ts`
- So sánh kết quả với SSI Finance để validate

#### 1.4 Watchlist per-user
- Tách `watchlist.json` thành `watchlist_{username}.json`
- Update các hàm: `load_watchlist()`, `save_watchlist()` nhận thêm param `username`
- Update tất cả endpoints `/api/watchlist/*` — lấy username từ session/token
- Migration: user nào login đầu tiên thì copy watchlist.json cũ cho họ

---

### PHASE 2 — Chart Kỹ Thuật Visual

#### 2.1 Backend — đã có
- `GET /api/history/{ticker}` (sau khi fix Phase 1.1)
- `GET /api/technical/{ticker}` trả về RSI, MACD, MA, Bollinger

#### 2.2 Frontend — thêm vào tab Kỹ Thuật
- Dùng **lightweight-charts** (TradingView, MIT license)
- CDN: `https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js`

**Layout:**
```
[Mã CP] [Timeframe: 1M|3M|6M|1Y] [Phân tích]
─────────────────────────────────────────────
Chart 1: Candlestick + Volume bars + MA20 + MA50  (60% height)
Chart 2: RSI panel + đường 30/70                  (20% height)
Chart 3: MACD line + Signal + Histogram            (20% height)
─────────────────────────────────────────────
Stat grid: Giá | RSI | MACD | MA20 | MA50 | Tín hiệu
Signal cards: danh sách tín hiệu bullish/bearish
```

**UX:**
- Crosshair tooltip hiện OHLCV khi hover
- Timeframe selector tự động tính start date
- Auto load khi nhấn Enter trong ô mã CP

---

### PHASE 3 — AI Alert System

#### 3.1 Data structure
```python
# alerts_{username}.json
[
  {
    "id": "1234567890",
    "ticker": "FPT",
    "type": "price_below",    # price_above | price_below | rsi_oversold | rsi_overbought | score_change
    "value": 90.0,            # nghìn VNĐ (giá) hoặc số (RSI) hoặc điểm (score)
    "active": True,
    "created_at": "2025-01-01T00:00:00",
    "triggered_at": None,
    "last_checked": None
  }
]
```

#### 3.2 Backend endpoints cần thêm
```
POST /api/alerts              ← tạo alert mới
GET  /api/alerts              ← danh sách alerts của user
DELETE /api/alerts/{id}       ← xóa alert
GET  /api/alerts/triggered    ← alerts đã triggered (polling từ frontend)
POST /api/alerts/{id}/reset   ← reset alert để dùng lại
```

#### 3.3 Alert checker (scheduler)
```python
# Chạy mỗi 15 phút, giờ 9:00–15:00 VN (UTC+7)
# Dùng APScheduler (thêm vào requirements.txt)
async def check_all_alerts():
    for username, alerts in load_all_alerts().items():
        for alert in alerts:
            if not alert["active"] or alert["triggered_at"]:
                continue
            price = get_latest_price(alert["ticker"])
            rsi   = get_latest_rsi(alert["ticker"])
            check_and_trigger(username, alert, price, rsi)
```

#### 3.4 Frontend — Tab Alerts mới trong sidebar
```
Icon: 🔔  Label: Alerts
Badge đỏ trên icon chuông header khi có triggered alerts
```

**UI trong tab:**
- Form tạo alert: [Mã CP] [Loại] [Ngưỡng] [Tạo alert]
- Danh sách alerts: active / triggered
- Polling mỗi 60s gọi `/api/alerts/triggered`
- Toast notification khi có alert mới triggered

#### 3.5 Telegram (optional, Phase 3 sau)
- Thêm field `telegram_chat_id` vào user profile
- Gửi message khi alert triggered

---

### PHASE 4 — Performance & UX

#### 4.1 Cache
```python
# Simple in-memory cache
_cache = {}  # key -> (data, timestamp)
CACHE_TTL = {"bctc": 86400, "price": 300, "technical": 300}  # seconds

def cached(key, ttl, fn):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < ttl:
            return data
    result = fn()
    _cache[key] = (result, time.time())
    return result
```

#### 4.2 Portfolio Tracker
- User nhập danh mục thực tế: mã + số CP + giá mua
- Tính P&L realtime (giá hiện tại - giá mua) × số CP
- Hiển thị: tổng vốn, tổng giá trị hiện tại, lãi/lỗ tổng, % thay đổi
- So sánh với VN-Index cùng kỳ

#### 4.3 So sánh ngành
- Khi xem phân tích 1 mã, tự fetch 2-3 mã cùng ngành (hardcode mapping ngành)
- Bảng so sánh: P/E, ROE, biên gộp, tỷ lệ nợ, Buffett score
- Highlight mã đang phân tích

---

## ⚙️ Environment Variables (Railway)
```
ANTHROPIC_API_KEY=sk-ant-...
```
Chỉ cần 1 biến này. vnstock free, không cần API key.

## ⚠️ Lưu ý quan trọng
1. **Không xóa `SafeEncoder`** — bắt buộc để handle NaN/Inf từ pandas
2. **Không xóa logic phát hiện ngân hàng** — `is_bank` flag đang dùng
3. **Test mỗi phase** trước khi qua phase tiếp
4. **Railway deploy**: push GitHub → auto deploy, không cần thêm config
5. **vnstock rate limit**: thêm `await asyncio.sleep(0.5)` giữa các requests trong scan
6. **`find_row()` dùng `regex=False`** — tránh lỗi với ký tự đặc biệt như `(`, `/`

---

## 🕯️ PHASE 2 (CHI TIẾT) — Chart Kỹ Thuật Nâng Cao

### Mục tiêu
Tab Kỹ Thuật hiện tại chỉ show số liệu dạng text (RSI, MACD, MA).
Cần nâng cấp thành full technical analysis với chart thật + AI nhận xét.

### Layout tab Kỹ Thuật mới
```
┌─────────────────────────────────────────────┐
│ [Mã CP] [1D | 1W | 1M] [Phân tích]          │
├─────────────────────────────────────────────┤
│ TradingView Widget — full interactive chart  │
│ (candlestick + volume + indicators)          │
│ Height: ~500px                               │
├─────────────────────────────────────────────┤
│ AI Analysis Panel (gọi Claude API)           │
│ ┌──────────┬──────────┬──────────┐           │
│ │Ichimoku  │Fibonacci │Mô hình   │           │
│ │Cloud     │Levels    │Nến       │           │
│ └──────────┴──────────┴──────────┘           │
│ Tín hiệu tổng hợp: [MUA/TRUNG TÍNH/BÁN]    │
│ Nhận xét chi tiết (AI generated)            │
└─────────────────────────────────────────────┘
```

---

### BƯỚC 1 — TradingView Widget (frontend only)

Nhúng TradingView Advanced Chart Widget vào tab Kỹ Thuật:

```html
<!-- Thay thế toàn bộ nội dung hiện tại của #taResult -->
<div id="tv-chart-container"></div>
<script>
function loadTVChart(ticker, interval) {
  const container = document.getElementById('tv-chart-container');
  container.innerHTML = '';
  const script = document.createElement('script');
  script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
  script.async = true;
  script.innerHTML = JSON.stringify({
    "autosize": true,
    "height": 500,
    "symbol": "HOSE:" + ticker,   // fallback: "HNX:" + ticker nếu HOSE không có
    "interval": interval || "D",
    "timezone": "Asia/Ho_Chi_Minh",
    "theme": "light",
    "style": "1",                  // 1 = Candlestick
    "locale": "vi_VN",
    "enable_publishing": false,
    "hide_top_toolbar": false,
    "hide_legend": false,
    "studies": [
      "STD;Ichimoku%20Cloud",
      "STD;MACD",
      "STD;RSI"
    ],
    "container_id": "tv-chart-container"
  });
  container.appendChild(script);
}
</script>
```

**Lưu ý:**
- Mã HOSE thì prefix `HOSE:FPT`, mã HNX thì `HNX:SHB`
- Cần detect sàn của mã → thêm field `exchange` vào `/api/technical/{ticker}` response
- Interval mapping: `1D` → `"D"`, `1W` → `"W"`, `1M` → `"M"`

---

### BƯỚC 2 — Backend: tính Indicators cho AI

Thêm vào endpoint `GET /api/technical/{ticker}` các chỉ số sau (dùng pandas, không cần thư viện ngoài):

#### 2.1 Ichimoku Cloud
```python
def calc_ichimoku(df):
    # Tenkan-sen (Conversion Line): (9-period high + 9-period low) / 2
    tenkan = (df['high'].rolling(9).max() + df['high'].rolling(9).min()) / 2
    # Kijun-sen (Base Line): (26-period high + 26-period low) / 2
    kijun  = (df['high'].rolling(26).max() + df['low'].rolling(26).min()) / 2
    # Senkou Span A: (Tenkan + Kijun) / 2, shift 26 periods forward
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    # Senkou Span B: (52-period high + 52-period low) / 2, shift 26 forward
    senkou_b = ((df['high'].rolling(52).max() + df['low'].rolling(52).min()) / 2).shift(26)
    # Chikou Span: close shifted 26 periods back
    chikou = df['close'].shift(-26)

    last = df.index[-1]
    price = df['close'].iloc[-1]
    sa = senkou_a.iloc[-1]
    sb = senkou_b.iloc[-1]

    cloud_top    = max(sa, sb)
    cloud_bottom = min(sa, sb)

    if price > cloud_top:
        position = "TRÊN MÂY"      # bullish
        signal   = "bullish"
    elif price < cloud_bottom:
        position = "DƯỚI MÂY"      # bearish
        signal   = "bearish"
    else:
        position = "TRONG MÂY"     # neutral
        signal   = "neutral"

    tk_cross = None
    if len(tenkan) > 1 and len(kijun) > 1:
        if tenkan.iloc[-2] < kijun.iloc[-2] and tenkan.iloc[-1] > kijun.iloc[-1]:
            tk_cross = "golden_cross"   # bullish
        elif tenkan.iloc[-2] > kijun.iloc[-2] and tenkan.iloc[-1] < kijun.iloc[-1]:
            tk_cross = "dead_cross"     # bearish

    return {
        "tenkan": round(tenkan.iloc[-1], 2),
        "kijun":  round(kijun.iloc[-1], 2),
        "senkou_a": round(sa, 2),
        "senkou_b": round(sb, 2),
        "cloud_top": round(cloud_top, 2),
        "cloud_bottom": round(cloud_bottom, 2),
        "position": position,
        "signal": signal,
        "tk_cross": tk_cross,
    }
```

#### 2.2 Fibonacci Retracement
```python
def calc_fibonacci(df, lookback=60):
    # Tính từ đỉnh/đáy trong lookback ngày gần nhất
    recent = df.tail(lookback)
    high = recent['high'].max()
    low  = recent['low'].min()
    diff = high - low
    price = df['close'].iloc[-1]

    levels = {
        "0.0":   round(high, 2),
        "0.236": round(high - 0.236 * diff, 2),
        "0.382": round(high - 0.382 * diff, 2),
        "0.5":   round(high - 0.5   * diff, 2),
        "0.618": round(high - 0.618 * diff, 2),
        "0.786": round(high - 0.786 * diff, 2),
        "1.0":   round(low, 2),
    }

    # Tìm giá đang nằm giữa 2 mức Fibonacci nào
    sorted_levels = sorted(levels.items(), key=lambda x: x[1], reverse=True)
    current_zone = None
    for i in range(len(sorted_levels) - 1):
        upper_label, upper_val = sorted_levels[i]
        lower_label, lower_val = sorted_levels[i+1]
        if lower_val <= price <= upper_val:
            current_zone = f"Giữa Fib {lower_label} ({lower_val:,.0f}) và {upper_label} ({upper_val:,.0f})"
            break

    return {
        "high": round(high, 2),
        "low":  round(low, 2),
        "levels": levels,
        "current_zone": current_zone,
        "lookback_days": lookback,
    }
```

#### 2.3 Nhận diện mô hình nến (5 mô hình quan trọng nhất)
```python
def detect_candle_patterns(df):
    patterns = []
    o, h, l, c = df['open'], df['high'], df['low'], df['close']

    # Lấy 3 nến gần nhất
    for i in [-1, -2, -3]:
        body   = abs(c.iloc[i] - o.iloc[i])
        candle = c.iloc[i] - o.iloc[i]
        range_ = h.iloc[i] - l.iloc[i]
        if range_ == 0: continue

        # Doji: body rất nhỏ so với range
        if body / range_ < 0.1:
            patterns.append({"name": "Doji", "index": i, "type": "neutral",
                             "meaning": "Thị trường do dự, có thể đảo chiều"})

        # Hammer (búa): lower shadow dài, body nhỏ ở trên, xuất hiện sau downtrend
        lower_shadow = min(o.iloc[i], c.iloc[i]) - l.iloc[i]
        upper_shadow = h.iloc[i] - max(o.iloc[i], c.iloc[i])
        if lower_shadow > 2 * body and upper_shadow < body:
            patterns.append({"name": "Hammer", "index": i, "type": "bullish",
                             "meaning": "Tín hiệu đảo chiều tăng sau downtrend"})

        # Shooting Star: upper shadow dài, body nhỏ ở dưới
        if upper_shadow > 2 * body and lower_shadow < body:
            patterns.append({"name": "Shooting Star", "index": i, "type": "bearish",
                             "meaning": "Tín hiệu đảo chiều giảm sau uptrend"})

    # Bullish Engulfing (nến 2 cây)
    if (c.iloc[-2] < o.iloc[-2] and   # nến trước đỏ
        c.iloc[-1] > o.iloc[-1] and   # nến sau xanh
        o.iloc[-1] < c.iloc[-2] and   # mở thấp hơn đóng cửa nến trước
        c.iloc[-1] > o.iloc[-2]):     # đóng cao hơn mở cửa nến trước
        patterns.append({"name": "Bullish Engulfing", "index": -1, "type": "bullish",
                         "meaning": "Phe mua áp đảo, tín hiệu tăng mạnh"})

    # Bearish Engulfing
    if (c.iloc[-2] > o.iloc[-2] and
        c.iloc[-1] < o.iloc[-1] and
        o.iloc[-1] > c.iloc[-2] and
        c.iloc[-1] < o.iloc[-2]):
        patterns.append({"name": "Bearish Engulfing", "index": -1, "type": "bearish",
                         "meaning": "Phe bán áp đảo, tín hiệu giảm mạnh"})

    # Morning Star (3 cây — đảo chiều tăng)
    if len(df) >= 3:
        if (c.iloc[-3] < o.iloc[-3] and                          # nến 1: đỏ dài
            abs(c.iloc[-2] - o.iloc[-2]) < (h.iloc[-2] - l.iloc[-2]) * 0.3 and  # nến 2: body nhỏ
            c.iloc[-1] > o.iloc[-1] and                          # nến 3: xanh
            c.iloc[-1] > (o.iloc[-3] + c.iloc[-3]) / 2):         # đóng trên midpoint nến 1
            patterns.append({"name": "Morning Star", "index": -1, "type": "bullish",
                             "meaning": "Tín hiệu đảo chiều tăng mạnh, xác nhận đáy"})

    return patterns[:3]  # trả về tối đa 3 pattern quan trọng nhất
```

#### 2.4 Update response của `/api/technical/{ticker}`
```python
# Thêm vào dict response hiện tại:
return ok({
    "ticker": ticker,
    "latest": { ...existing... },
    "signals": [ ...existing... ],
    "ichimoku": calc_ichimoku(df),          # NEW
    "fibonacci": calc_fibonacci(df),         # NEW
    "candle_patterns": detect_candle_patterns(df),  # NEW
    "exchange": detect_exchange(ticker),     # NEW — "HOSE" hoặc "HNX"
})
```

**Hàm detect_exchange:**
```python
def detect_exchange(ticker: str) -> str:
    # Danh sách mã HNX phổ biến — hoặc gọi API check
    HNX_TICKERS = {"SHB","ACB","PVS","NTP","VCS","CEO","HUT","PVI","BVS","MBS"}
    return "HNX" if ticker.upper() in HNX_TICKERS else "HOSE"
```

---

### BƯỚC 3 — Frontend: AI Analysis Panel

Sau khi fetch `/api/technical/{ticker}`, hiển thị panel phân tích phía dưới TradingView widget:

#### 3.1 Gọi Claude API để tổng hợp
```javascript
async function getAITechnicalAnalysis(ticker, taData) {
  const prompt = `
Bạn là chuyên gia phân tích kỹ thuật chứng khoán Việt Nam.
Phân tích cổ phiếu ${ticker} dựa trên dữ liệu sau:

GIÁ HIỆN TẠI: ${taData.latest.close} đồng

ICHIMOKU:
- Vị trí: ${taData.ichimoku.position}
- Tenkan: ${taData.ichimoku.tenkan} | Kijun: ${taData.ichimoku.kijun}
- Mây: ${taData.ichimoku.cloud_bottom} - ${taData.ichimoku.cloud_top}
- TK Cross: ${taData.ichimoku.tk_cross || 'Không có'}

FIBONACCI (${taData.fibonacci.lookback_days} ngày):
- Vùng hiện tại: ${taData.fibonacci.current_zone}
- Đỉnh: ${taData.fibonacci.high} | Đáy: ${taData.fibonacci.low}

MÔ HÌNH NẾN GẦN NHẤT:
${taData.candle_patterns.map(p => `- ${p.name}: ${p.meaning}`).join('\n') || '- Không có mô hình rõ ràng'}

RSI: ${taData.latest.rsi} | MACD: ${taData.latest.macd}
MA20: ${taData.latest.ma20} | MA50: ${taData.latest.ma50}

Hãy đưa ra:
1. Tín hiệu tổng hợp: MUA / TRUNG TÍNH / BÁN (1 từ duy nhất trên dòng đầu)
2. Nhận xét ngắn gọn 3-4 câu bằng tiếng Việt
3. Vùng hỗ trợ và kháng cự gần nhất
4. Rủi ro cần lưu ý

Trả lời súc tích, dùng bullet points.`;

  const response = await fetch('/api/analyze-claude', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Token': authToken},
    body: JSON.stringify({ prompt, ticker })
  });
  const d = await response.json();
  return d.analysis || '';
}
```

#### 3.2 UI Panel AI Analysis
```html
<!-- Thêm vào sau TradingView widget -->
<div id="aiTAPanel" style="display:none">
  <div class="card">
    <div class="card-title">AI Phân Tích Kỹ Thuật</div>

    <!-- 3 box indicators -->
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px">

      <!-- Ichimoku -->
      <div id="ichiBox" class="stat">
        <div class="stat-lbl">Ichimoku Cloud</div>
        <div class="stat-val" id="ichiSignal">—</div>
        <div style="font-size:.7rem;color:var(--text3)" id="ichiDetail"></div>
      </div>

      <!-- Fibonacci -->
      <div class="stat">
        <div class="stat-lbl">Fibonacci Zone</div>
        <div class="stat-val" style="font-size:.82rem" id="fibZone">—</div>
      </div>

      <!-- Candle Pattern -->
      <div class="stat">
        <div class="stat-lbl">Mô hình nến</div>
        <div id="candlePatterns"></div>
      </div>

    </div>

    <!-- AI verdict badge -->
    <div id="aiVerdict" style="margin-bottom:12px"></div>

    <!-- AI commentary -->
    <div id="aiTAComment" class="ai-box"></div>
  </div>
</div>
```

#### 3.3 Render logic
```javascript
function renderTAPanel(taData, aiText) {
  // Ichimoku
  const ichi = taData.ichimoku;
  document.getElementById('ichiSignal').textContent = ichi.position;
  document.getElementById('ichiSignal').className =
    'stat-val ' + (ichi.signal === 'bullish' ? 'cg' : ichi.signal === 'bearish' ? 'cr' : 'cy');
  document.getElementById('ichiDetail').textContent =
    `T:${(ichi.tenkan/1000).toFixed(1)}k K:${(ichi.kijun/1000).toFixed(1)}k` +
    (ichi.tk_cross ? ` | ${ichi.tk_cross === 'golden_cross' ? '✨ Golden Cross' : '☠️ Dead Cross'}` : '');

  // Fibonacci
  document.getElementById('fibZone').textContent = taData.fibonacci.current_zone || '—';

  // Candle patterns
  const cp = taData.candle_patterns;
  document.getElementById('candlePatterns').innerHTML = cp.length
    ? cp.map(p => `<div style="font-size:.72rem;padding:2px 0">
        <span class="${p.type==='bullish'?'cg':p.type==='bearish'?'cr':'cy'}">●</span>
        ${p.name}
      </div>`).join('')
    : '<div style="font-size:.72rem;color:var(--text3)">Chưa rõ</div>';

  // AI verdict (parse dòng đầu của AI response)
  const firstLine = aiText.split('\n')[0].toUpperCase();
  const verdict = firstLine.includes('MUA') ? 'MUA' : firstLine.includes('BÁN') ? 'BÁN' : 'TRUNG TÍNH';
  const vColor = verdict === 'MUA' ? 'var(--green)' : verdict === 'BÁN' ? 'var(--red)' : 'var(--yellow)';
  document.getElementById('aiVerdict').innerHTML =
    `<div style="display:inline-flex;align-items:center;gap:8px;padding:8px 20px;
      border-radius:20px;background:${vColor}20;border:1px solid ${vColor};
      font-weight:700;font-size:1rem;color:${vColor}">
      ${verdict === 'MUA' ? '📈' : verdict === 'BÁN' ? '📉' : '⚖️'} Tín hiệu: ${verdict}
    </div>`;

  // AI text
  document.getElementById('aiTAComment').innerHTML = aiText.replace(/\n/g, '<br>');
  document.getElementById('aiTAPanel').style.display = 'block';
}
```

#### 3.4 Update hàm fetchTA()
```javascript
async function fetchTA() {
  const ticker = document.getElementById('taMA').value.trim().toUpperCase();
  const period = document.getElementById('taPeriod').value || 90;
  if (!ticker) { alert('Nhập mã CP!'); return; }

  // 1. Load TradingView chart ngay lập tức
  loadTVChart(ticker, 'D');

  // 2. Fetch technical data từ backend
  try {
    const r = await fetch(`/api/technical/${ticker}?period=${period}`);
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Lỗi');

    // 3. Show stats cũ (giữ nguyên)
    renderTAStats(d);

    // 4. Gọi AI analysis (async, show sau)
    document.getElementById('aiTAComment').textContent = 'Đang phân tích...';
    document.getElementById('aiTAPanel').style.display = 'block';
    const aiText = await getAITechnicalAnalysis(ticker, d);
    renderTAPanel(d, aiText);

  } catch(e) { alert('Lỗi: ' + e.message); }
}
```

---

### CHECKLIST HOÀN THÀNH PHASE 2

- [ ] `server.py`: thêm `calc_ichimoku()`, `calc_fibonacci()`, `detect_candle_patterns()`, `detect_exchange()`
- [ ] `server.py`: update response `/api/technical/{ticker}` với 4 fields mới
- [ ] `dashboard.html`: thêm `loadTVChart()` function
- [ ] `dashboard.html`: thêm AI Analysis Panel HTML
- [ ] `dashboard.html`: thêm `renderTAPanel()` function
- [ ] `dashboard.html`: update `fetchTA()` để gọi cả TradingView + AI
- [ ] Test với: FPT, MWG, VCB, SSI (các mã phổ biến)
- [ ] Test edge case: mã HNX (prefix khác)


---

## 🏗️ REFACTOR GUIDE — Tách modules trước Phase 3

> Làm sau khi Phase 2 xong, trước khi bắt đầu Phase 3 (Alert system)

### Tại sao cần refactor?
- `server.py` hiện tại ~1,500 dòng, sau Phase 2 sẽ ~2,500 dòng
- Alert system cần APScheduler chạy background — để chung 1 file rất khó debug
- Tách modules giúp Claude Code sửa từng phần mà không break phần khác

### Cấu trúc mới

```
final_app/
├── server.py              ← chỉ giữ app init + include routers
├── dashboard.html         ← giữ nguyên single file (OK cho nhóm nhỏ)
├── requirements.txt
├── CONTEXT.md
│
├── routers/               ← NEW: tách API endpoints
│   ├── __init__.py
│   ├── auth.py            ← /api/auth/*
│   ├── watchlist.py       ← /api/watchlist/*
│   ├── buffett.py         ← /api/fetch, /api/buffett-score, /api/scan*
│   ├── technical.py       ← /api/technical, /api/history
│   ├── calculator.py      ← /api/calc/*
│   └── ai.py              ← /api/analyze-claude, /api/ai-picks
│
├── services/              ← NEW: tách business logic
│   ├── __init__.py
│   ├── vnstock_service.py ← tất cả logic gọi vnstock API
│   ├── buffett_engine.py  ← compute_buffett_score() — quan trọng nhất
│   ├── indicators.py      ← calc_ichimoku, calc_fibonacci, detect_candle_patterns
│   └── scheduler.py       ← APScheduler jobs (scan hàng ngày, check alerts)
│
└── data/                  ← NEW: tách data files ra 1 folder
    ├── users.json
    ├── watchlist_*.json
    ├── calc_data/
    ├── alerts_*.json
    └── daily_picks.json
```

### server.py sau refactor — chỉ còn ~50 dòng

```python
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from routers import auth, watchlist, buffett, technical, calculator, ai
from services.scheduler import start_scheduler

app = FastAPI()

# Include routers
app.include_router(auth.router,       prefix="/api/auth")
app.include_router(watchlist.router,  prefix="/api")
app.include_router(buffett.router,    prefix="/api")
app.include_router(technical.router,  prefix="/api")
app.include_router(calculator.router, prefix="/api/calc")
app.include_router(ai.router,         prefix="/api")

@app.on_event("startup")
async def startup():
    start_scheduler()

@app.get("/", response_class=HTMLResponse)
async def root():
    with open("dashboard.html") as f:
        return f.read()
```

### Thứ tự thực hiện refactor

1. Tạo folder `routers/` và `services/` và `data/`
2. Move `compute_buffett_score()` → `services/buffett_engine.py`
3. Move vnstock calls → `services/vnstock_service.py`
4. Tách từng nhóm endpoints → `routers/*.py`
5. Update `server.py` thành thin wrapper
6. Move data files → `data/`
7. Update path references trong code
8. Test toàn bộ endpoints còn hoạt động

### Lưu ý khi refactor
- Giữ nguyên `SafeEncoder` — move vào `services/vnstock_service.py`
- Giữ nguyên `SESSIONS` dict — move vào `routers/auth.py`
- `DATA_DIR` constant cần update từ `"calc_data/"` → `"data/calc_data/"`
- Railway không cần config gì thêm — cấu trúc folder thay đổi nhưng entry point vẫn là `server.py`

---

## 🧠 STREET SMART RULES ENGINE

> Bộ quy tắc thực chiến nhúng vào AI commentary.
> **Living document** — cập nhật thêm khi có kinh nghiệm mới.

### Cách implement

Thêm constant `STREET_SMART_RULES` vào `services/ai.py` (sau refactor)
hoặc trực tiếp vào `/api/analyze-claude` và `/api/technical/{ticker}` AI prompt.

```python
STREET_SMART_RULES = """
=== KINH NGHIỆM THỰC CHIẾN — ÁP DỤNG KHI PHÂN TÍCH ===

[VOLUME + GIÁ]
- Trần + vol thấp hơn TB20 phiên  → Cung khan, còn room tăng tiếp
- Trần + vol đột biến (>3x TB20)  → Nguy cơ tay to xả hàng, CẢNH BÁO
- Sàn + vol lớn                   → Hoảng loạn, chưa nên bắt đáy
- Sàn + vol thấp                  → Rơi tự do, chờ tín hiệu hồi phục rõ

[HỖ TRỢ / KHÁNG CỰ]
- Giá đang dưới kháng cự          → Upside bị chặn, chưa nên mua
- Giá đang trên hỗ trợ            → Downside bị đỡ, chưa nên bán
- Breakout kháng cự + vol lớn     → Tín hiệu thật, cơ hội vào hàng
- Thủng hỗ trợ                    → Thoát hàng, không bắt dao rơi
- Test lại hỗ trợ lần 3+          → Hỗ trợ yếu dần, theo dõi sát

[ICHIMOKU]
- Giá trên mây + Tenkan > Kijun   → Uptrend xác nhận, giữ/mua
- Giá dưới mây + Tenkan < Kijun   → Downtrend, tránh xa
- Giá trong mây                   → Vùng nhiễu, không rõ xu hướng
- Golden Cross Tenkan/Kijun       → Tín hiệu mua sớm
- Dead Cross Tenkan/Kijun         → Tín hiệu thoát hàng

[TÂM LÝ ĐÁM ĐÔNG]
- 3 phiên trần liên tiếp          → FOMO zone, tuyệt đối không đuổi giá
- RSI > 70 + vol tăng             → Vùng quá mua, dễ điều chỉnh mạnh
- RSI < 30 + vol giảm dần         → Cạn lực bán, có thể sắp hồi
- Giá tăng mạnh + vol giảm dần    → Uptrend yếu, cẩn thận đảo chiều

Khi phân tích: CHECK từng nhóm rule, nêu rõ rule nào đang kích hoạt.
Ưu tiên cảnh báo rủi ro trước, cơ hội sau.
=== HẾT QUY TẮC ===
"""
```

### Cách dùng trong AI prompt

```python
# Trong /api/technical/{ticker} — AI technical analysis
system_prompt = f"""Bạn là chuyên gia phân tích kỹ thuật chứng khoán Việt Nam.
{STREET_SMART_RULES}
Phân tích súc tích, dùng bullet points, tiếng Việt."""

# Trong /api/analyze-claude — AI Buffett analysis  
# Thêm STREET_SMART_RULES vào cuối system prompt hiện tại
```

### Cách tính vol so với TB20 (thêm vào backend)

```python
def calc_volume_signal(df) -> dict:
    vol_ma20 = df['volume'].rolling(20).mean().iloc[-1]
    vol_last = df['volume'].iloc[-1]
    vol_ratio = vol_last / vol_ma20 if vol_ma20 > 0 else 1

    close_last  = df['close'].iloc[-1]
    close_prev  = df['close'].iloc[-2]
    ref_price   = close_prev  # giá tham chiếu
    change_pct  = (close_last - ref_price) / ref_price * 100

    is_tran = change_pct >= 6.5   # +6.5% ~ trần HOSE
    is_san  = change_pct <= -6.5  # -6.5% ~ sàn HOSE

    signal = "neutral"
    warning = None

    if is_tran and vol_ratio > 3:
        signal  = "bearish"
        warning = f"⚠️ TRẦN + VOL đột biến {vol_ratio:.1f}x TB20 — nguy cơ xả hàng"
    elif is_tran and vol_ratio < 1:
        signal  = "bullish"
        warning = f"✅ Trần + vol thấp ({vol_ratio:.1f}x TB20) — cung khan, còn room"
    elif is_san and vol_ratio > 2:
        signal  = "bearish"
        warning = f"⚠️ SÀN + VOL lớn {vol_ratio:.1f}x TB20 — hoảng loạn, chưa bắt đáy"

    return {
        "vol_last": int(vol_last),
        "vol_ma20": int(vol_ma20),
        "vol_ratio": round(vol_ratio, 2),
        "change_pct": round(change_pct, 2),
        "is_tran": is_tran,
        "is_san": is_san,
        "signal": signal,
        "warning": warning,
    }
```

### Thêm vào response `/api/technical/{ticker}`

```python
return ok({
    ...existing fields...,
    "volume_signal": calc_volume_signal(df),   # NEW
})
```

### Hiển thị warning nổi bật trên dashboard

```javascript
// Nếu có warning từ volume_signal → show banner đỏ/xanh nổi bật
if (d.volume_signal?.warning) {
    document.getElementById('taWarning').innerHTML =
        `<div style="padding:10px 14px;border-radius:8px;font-weight:600;font-size:.85rem;
        background:${d.volume_signal.signal==='bearish'?'var(--red-bg)':'var(--green-bg)'};
        color:${d.volume_signal.signal==='bearish'?'var(--red)':'var(--green)'};
        border:1px solid currentColor;margin-bottom:12px">
        ${d.volume_signal.warning}</div>`;
}
```

---

> 📝 **TODO — cập nhật thêm rules khi có kinh nghiệm mới:**
> - [ ] Rule về divergence RSI/giá
> - [ ] Rule về gap up/gap down
> - [ ] Rule về accumulation/distribution
> - [ ] Rule về insider trading signals (đột biến khối lượng bất thường)
> - [ ] *(thêm vào đây khi học được)*

---

## 🎨 REDESIGN V2 — Bộ Công Cụ Ra Quyết Định

> Thiết kế lại toàn bộ UX/UI theo 4 module thay vì tab rời rạc.
> Triết lý: "Nhìn vào là biết làm gì — không cần tự suy"

---

### NAVIGATION MỚI — 4 Module

```
Sidebar:
🔍 Radar          ← màn hình chính, mở app là thấy
🔬 Phân Tích      ← deep dive 1 mã (cơ bản + kỹ thuật gộp)
💼 Danh Mục       ← portfolio tracker + cảnh báo
🧮 Công Cụ        ← calculator (kết nối danh mục)
```

---

### MODULE 1 — RADAR 🔍

**Mục đích:** Hôm nay nên nhìn vào mã nào?

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│ 🔍 RADAR  [Scan ngay 🔄]  Cập nhật: 08:45 hôm nay  │
├───────┬──────────┬────────────┬──────────┬──────────┤
│ Mã    │ Tín hiệu │ Cơ bản     │ Kỹ thuật │ Hành động│
├───────┼──────────┼────────────┼──────────┼──────────┤
│ FPT   │ 🟢 85%   │ 11/14 ✅   │ Trên mây │ XEM      │
│ MWG   │ 🟡 58%   │  8/14 ⚠️   │ Trong mây│ CHỜ      │
│ VCB   │ 🔴 38%   │  5/14 ❌   │ Dưới mây │ TRÁNH    │
└───────┴──────────┴────────────┴──────────┴──────────┘
│ 🤖 AI: "FPT và HPG đang có tín hiệu tốt nhất hôm   │
│ nay. FPT breakout kháng cự 122k với vol 1.8x TB20" │
└─────────────────────────────────────────────────────┘
```

**Công thức tính Tín hiệu tổng hợp:**
```python
def calc_combined_signal(buffett_score, ta_data) -> dict:
    # Điểm cơ bản: 0-100
    fundamental = (buffett_score / 14) * 100

    # Điểm kỹ thuật: 0-100
    ta_score = 50  # base neutral
    ichi = ta_data.get("ichimoku", {})
    rsi  = ta_data.get("latest", {}).get("rsi", 50)
    vol  = ta_data.get("volume_signal", {})
    macd = ta_data.get("latest", {})

    if ichi.get("signal") == "bullish":   ta_score += 20
    elif ichi.get("signal") == "bearish": ta_score -= 20
    if ichi.get("tk_cross") == "golden_cross": ta_score += 10
    elif ichi.get("tk_cross") == "dead_cross": ta_score -= 10
    if rsi < 35:  ta_score += 10
    elif rsi > 65: ta_score -= 10
    if vol.get("signal") == "bullish": ta_score += 10
    elif vol.get("signal") == "bearish": ta_score -= 15  # penalty nặng hơn
    if macd.get("macd", 0) > macd.get("macd_signal", 0): ta_score += 5
    else: ta_score -= 5

    ta_score = max(0, min(100, ta_score))

    # Tổng hợp: 60% cơ bản + 40% kỹ thuật
    combined = fundamental * 0.6 + ta_score * 0.4

    # Verdict
    if combined >= 70:
        action = "XEM"
        color  = "green"
    elif combined >= 50:
        action = "CHỜ"
        color  = "yellow"
    else:
        action = "TRÁNH"
        color  = "red"

    # Ichimoku position label
    ichi_label = {"bullish": "Trên mây", "bearish": "Dưới mây"}.get(
        ichi.get("signal"), "Trong mây")

    return {
        "combined": round(combined),
        "fundamental": round(fundamental),
        "ta_score": round(ta_score),
        "action": action,
        "color": color,
        "ichi_label": ichi_label,
    }
```

**Endpoint mới:** `GET /api/radar`
```python
@app.get("/api/radar")
async def get_radar(request: Request):
    """
    Trả về danh sách watchlist đã được tính điểm tổng hợp,
    xếp hạng từ cao đến thấp.
    """
    user = get_current_user(request)
    watchlist = load_watchlist(user)
    results = []
    for ticker in watchlist:
        try:
            score_data  = compute_buffett_score(ticker)
            ta_data     = compute_technical(ticker)
            signal      = calc_combined_signal(
                score_data["score"], ta_data)
            results.append({
                "ticker":         ticker,
                "signal":         signal,
                "buffett_score":  score_data["score"],
                "price":          ta_data["latest"].get("close"),
                "volume_warning": ta_data.get("volume_signal", {}).get("warning"),
            })
        except:
            pass
    results.sort(key=lambda x: x["signal"]["combined"], reverse=True)
    return ok({"results": results, "updated_at": datetime.datetime.now().isoformat()})
```

---

### MODULE 2 — PHÂN TÍCH 🔬

**Mục đích:** Mã này có đáng mua không? — Cơ bản + Kỹ thuật gộp lại

**Layout:**
```
┌──────────────────────────────────────────────────────┐
│ [🔍 Nhập mã...]  [Phân tích]                        │
├───────────────────────┬──────────────────────────────┤
│  CƠ BẢN               │  KỸ THUẬT                    │
│  Score: 11/14  🟢     │  [TradingView Chart]         │
│  DCF: 142,000đ        │                              │
│  Giá HT: 125,000đ     │  Ichimoku: Trên mây ✅       │
│  MOS: +13.6% 🟢       │  RSI: 58 — bình thường      │
│  ROE: 28% ✅          │  Vol: 1.2x TB20              │
│  [Xem chi tiết ▼]     │  Fib zone: 0.382–0.5        │
├───────────────────────┴──────────────────────────────┤
│  🤖 AI VERDICT  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                                      │
│  📈 CÓ THỂ MUA — Điểm tổng hợp: 78/100              │
│                                                      │
│  • Cơ bản tốt: ROE 28%, biên gộp 35%, nợ thấp       │
│  • Kỹ thuật ổn: Trên mây Ichimoku, RSI trung tính   │
│  • Vùng mua hợp lý: 118,000–122,000đ                │
│  • Mục tiêu: 140,000đ (+14%)                        │
│  • Cắt lỗ: 112,000đ (-7%)                           │
│  • Risk/Reward: 1:2                                  │
│                                                      │
│  ⚠️ Rủi ro: Kháng cự 130k chưa phá được 2 lần       │
│                                                      │
│  [+ Thêm Watchlist]  [💼 Thêm Danh Mục]             │
└──────────────────────────────────────────────────────┘
```

**Thay đổi so với hiện tại:**
- Gộp tab Phân Tích + tab Kỹ Thuật thành 1 màn hình layout 2 cột
- AI verdict nói thẳng: vùng mua, TP, SL, risk/reward
- Nút "Thêm Danh Mục" → mở modal nhập số CP + giá mua

**AI prompt mới cho Deep Dive:**
```python
DEEP_DIVE_PROMPT = """
Bạn là chuyên gia phân tích chứng khoán Việt Nam.
Dựa trên dữ liệu cơ bản + kỹ thuật dưới đây, đưa ra:

1. VERDICT: CÓ THỂ MUA / CHỜ THÊM / TRÁNH XA (1 dòng đầu)
2. Lý do chính (3 bullet points)
3. Vùng mua hợp lý (nếu MUA)
4. Mục tiêu giá (TP) + Cắt lỗ (SL)
5. Risk/Reward ratio
6. 1 rủi ro quan trọng nhất cần theo dõi

Áp dụng kinh nghiệm thực chiến:
{STREET_SMART_RULES}

Trả lời bằng tiếng Việt, súc tích, thực chiến.
"""
```

---

### MODULE 3 — DANH MỤC 💼

**Mục đích:** Các mã đang giữ đang thế nào? Có cần hành động không?

**Đây là module hoàn toàn mới — chưa có trong app hiện tại.**

**Data structure:**
```python
# portfolio_{username}.json
[
  {
    "id": "...",
    "ticker": "FPT",
    "shares": 1000,
    "avg_price": 118.5,      # nghìn VNĐ
    "bought_at": "2025-01-15",
    "note": "Mua theo breakout",
    "target_price": 140.0,   # TP
    "stop_loss": 110.0,      # SL
  }
]
```

**Backend endpoints:**
```
GET    /api/portfolio              ← danh sách + P&L realtime
POST   /api/portfolio              ← thêm vị thế mới
PUT    /api/portfolio/{id}         ← cập nhật TP/SL/note
DELETE /api/portfolio/{id}         ← xóa vị thế
GET    /api/portfolio/summary      ← tổng danh mục
```

**Layout:**
```
┌──────────────────────────────────────────────────────┐
│ 💼 DANH MỤC                                          │
│ Tổng vốn: 500tr │ Giá trị HT: 541tr │ +8.2% +41tr  │
├───────┬──────┬───────┬──────┬──────┬─────────────────┤
│ Mã    │ Giá  │ Giá   │ P&L  │ %DM  │ Trạng thái      │
│       │ vốn  │ HT    │      │      │                 │
├───────┼──────┼───────┼──────┼──────┼─────────────────┤
│ FPT   │ 118k │ 125k  │ +7tr │ 35%  │ ✅ Đang tốt     │
│ MWG   │  90k │  83k  │ -7tr │ 25%  │ ⚠️ Test hỗ trợ  │
│ VNM   │  65k │  58k  │ -7tr │ 20%  │ 🔴 Thủng SL!    │
├───────┴──────┴───────┴──────┴──────┴─────────────────┤
│ 🤖 "VNM thủng stop loss 60k với vol lớn.             │
│  MWG đang test hỗ trợ 80k lần 2, theo dõi sát.      │
│  FPT tiếp cận kháng cự 130k — cân nhắc chốt 1 phần" │
└──────────────────────────────────────────────────────┘
```

**Logic cảnh báo tự động:**
```python
def check_portfolio_alerts(position, current_price, ta_data):
    warnings = []
    price = current_price / 1000  # về nghìn

    # SL bị thủng
    if position.get("stop_loss") and price < position["stop_loss"]:
        warnings.append({
            "level": "critical",
            "msg": f"🔴 Thủng Stop Loss {position['stop_loss']}k!"
        })

    # Gần TP
    if position.get("target_price") and price >= position["target_price"] * 0.97:
        warnings.append({
            "level": "info",
            "msg": f"🎯 Đã đạt ~97% mục tiêu {position['target_price']}k — cân nhắc chốt"
        })

    # Volume cảnh báo từ street smart rules
    vol_signal = ta_data.get("volume_signal", {})
    if vol_signal.get("warning"):
        warnings.append({
            "level": "warning",
            "msg": vol_signal["warning"]
        })

    # Ichimoku breakdown
    ichi = ta_data.get("ichimoku", {})
    if ichi.get("signal") == "bearish":
        warnings.append({
            "level": "warning",
            "msg": "⚠️ Giá rơi xuống dưới mây Ichimoku"
        })

    return warnings
```

---

### MODULE 4 — CÔNG CỤ 🧮

**Mục đích:** Tính toán trước khi quyết định — kết nối với danh mục thật

**Thay đổi chính so với hiện tại:**
- Thêm **Quick Fill từ Danh Mục** — click vào mã trong portfolio → tự điền số liệu vào calculator
- Sau khi tính xong → nút **"Cập nhật Danh Mục"** → lưu avg price mới vào portfolio

```javascript
// Quick fill từ danh mục
function fillFromPortfolio(position) {
    document.getElementById('avgTicker').value     = position.ticker;
    document.getElementById('avgSharesHeld').value = position.shares;
    document.getElementById('avgPriceHeld').value  = position.avg_price;
}

// Sau khi tính xong, cập nhật lại danh mục
async function updatePortfolioAfterAvg(result) {
    if (!confirm(`Cập nhật giá vốn ${result.ticker} thành ${result.new_avg}k?`)) return;
    await fetch(`/api/portfolio/${result.portfolio_id}`, {
        method: 'PUT',
        headers: {'Content-Type':'application/json','X-Token': authToken},
        body: JSON.stringify({
            avg_price: result.new_avg,
            shares: result.total_shares
        })
    });
    showToast('✓ Đã cập nhật danh mục');
}
```

---

### THỨ TỰ THỰC HIỆN REDESIGN

```
Bước 1: Thêm Portfolio module (backend + frontend) — module duy nhất thiếu
Bước 2: Tính Combined Signal — gộp fundamental + technical thành 1 điểm
Bước 3: Build Radar dùng Combined Signal
Bước 4: Redesign tab Phân Tích — layout 2 cột cơ bản + kỹ thuật
Bước 5: Kết nối Calculator với Portfolio (quick fill + update)
Bước 6: Update AI prompts — verdict thẳng hơn (vùng mua, TP, SL, R/R)
Bước 7: Redesign navigation sidebar — 4 module thay vì tab cũ
```

> ⚠️ Làm từng bước, test xong mới qua bước tiếp.
> Không làm bước 7 (redesign nav) trước bước 1-6 — dễ break UI.

---

### FILES SẼ THAY ĐỔI

| File | Thay đổi |
|---|---|
| `server.py` | Thêm `/api/radar`, `/api/portfolio/*`, `calc_combined_signal()` |
| `dashboard.html` | Redesign nav, thêm Portfolio UI, update Phân Tích layout 2 cột |
| `portfolio_{user}.json` | Tạo mới |


---

## 📱 PWA + MOBILE RESPONSIVE

### Mục tiêu
Biến web app thành PWA để user mobile dùng được như app thật.
Không cần App Store. User vào browser → "Add to Home Screen" → có icon, dùng như app.

---

### BƯỚC 1 — PWA Setup

Tạo file manifest.json trong root project:

{
  "name": "Stock Agent AI",
  "short_name": "StockAI",
  "description": "Bộ công cụ phân tích cổ phiếu Warren Buffett",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#F0F1F5",
  "theme_color": "#4A3F8F",
  "orientation": "portrait",
  "icons": [
    { "src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}

Tạo file static/sw.js (Service Worker):

const CACHE = 'stockai-v1';
const ASSETS = ['/', '/static/icon-192.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
});
self.addEventListener('fetch', e => {
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});

Tạo folder static/, generate icon bằng Python (chạy 1 lần):

# generate_icons.py
from PIL import Image, ImageDraw
import os
os.makedirs("static", exist_ok=True)
for size in [192, 512]:
    img = Image.new("RGB", (size, size), "#4A3F8F")
    draw = ImageDraw.Draw(img)
    draw.text((size//2, size//2), "SA", fill="white", anchor="mm")
    img.save(f"static/icon-{size}.png")

Thêm vào <head> của dashboard.html:

<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#4A3F8F">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="StockAI">
<link rel="apple-touch-icon" href="/static/icon-192.png">
<script>
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js');
  }
</script>

Thêm vào server.py:

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/manifest.json")
async def manifest():
    return FileResponse("manifest.json")

@app.get("/sw.js")
async def sw():
    return FileResponse("static/sw.js")

Thêm vào requirements.txt:
Pillow

---

### BƯỚC 2 — Mobile Responsive CSS

Thêm vào cuối phần <style> trong dashboard.html:

@media (max-width: 768px) {
  .bottom-nav { display: none !important; }
  #mobileNav { display: flex !important; }
  .app { flex-direction: column; }
  .app-right { min-height: calc(100vh - 56px); }
  .app-header { padding: 0 12px; gap: 8px; }
  .header-user { display: none; }
  .card { margin: 8px; padding: 12px; }
  .summary-card { margin: 8px; padding: 12px; }
  .data-table { margin: 8px; }
  .summary-grid { grid-template-columns: repeat(2, 1fr); }
  .table-head { display: none; }
  .table-row {
    grid-template-columns: 1fr 1fr;
    gap: 4px;
    padding: 12px;
    border-radius: 8px;
    margin: 4px 8px;
    border: 1px solid var(--border);
  }
  .fetch-row { flex-direction: column; }
  .fetch-row .fg { flex: unset; width: 100%; }
  input, select { font-size: 16px !important; width: 100%; }
  .calc-grid { grid-template-columns: 1fr !important; }
  .calc-input-group { grid-template-columns: 1fr !important; }
  .calc-metrics { grid-template-columns: repeat(2, 1fr); }
  .dao-compare { grid-template-columns: 1fr !important; }
  .big-score { font-size: 2.4rem; width: 56px; }
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
  .dcf-box { flex-direction: column; gap: 8px; }
  .sub-nav { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  .sub-tab { white-space: nowrap; }
  .sticky-actions { padding: 8px; }
  .btn-buy { padding: 14px; font-size: 1rem; }
  .hist-item { flex-wrap: wrap; gap: 8px; }
  .login-card { margin: 16px; padding: 28px 20px; }
  .app-body { padding-bottom: 64px; }
}

---

### BƯỚC 3 — Bottom Navigation Bar

Thêm HTML này vào cuối <body> trong dashboard.html, trước </body>:

<nav id="mobileNav" style="display:none;position:fixed;bottom:0;left:0;right:0;
  height:56px;background:var(--surface);border-top:1px solid var(--border);
  z-index:200;align-items:stretch;justify-content:space-around;
  box-shadow:0 -2px 8px rgba(0,0,0,.06)">
  <button onclick="showPage('daily')" data-mob="daily"
    style="flex:1;border:none;background:transparent;font-family:var(--sans);
    font-size:.6rem;color:var(--text3);cursor:pointer;display:flex;
    flex-direction:column;align-items:center;justify-content:center;gap:2px;
    border-top:2px solid transparent;transition:all .15s">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
      <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
    </svg>
    Radar
  </button>
  <button onclick="showPage('buffett')" data-mob="buffett"
    style="flex:1;border:none;background:transparent;font-family:var(--sans);
    font-size:.6rem;color:var(--text3);cursor:pointer;display:flex;
    flex-direction:column;align-items:center;justify-content:center;gap:2px;
    border-top:2px solid transparent;transition:all .15s">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
      <polyline points="14 2 14 8 20 8"/>
    </svg>
    Phân Tích
  </button>
  <button onclick="showPage('calculator')" data-mob="calculator"
    style="flex:1;border:none;background:transparent;font-family:var(--sans);
    font-size:.6rem;color:var(--text3);cursor:pointer;display:flex;
    flex-direction:column;align-items:center;justify-content:center;gap:2px;
    border-top:2px solid transparent;transition:all .15s">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
      <rect x="4" y="2" width="16" height="20" rx="2"/>
      <line x1="8" y1="6" x2="16" y2="6"/>
      <line x1="8" y1="10" x2="10" y2="10"/>
      <line x1="14" y1="10" x2="16" y2="10"/>
    </svg>
    Công Cụ
  </button>
  <button onclick="showPage('technical')" data-mob="technical"
    style="flex:1;border:none;background:transparent;font-family:var(--sans);
    font-size:.6rem;color:var(--text3);cursor:pointer;display:flex;
    flex-direction:column;align-items:center;justify-content:center;gap:2px;
    border-top:2px solid transparent;transition:all .15s">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
    </svg>
    Kỹ Thuật
  </button>
</nav>

Update hàm showPage() trong dashboard.html — thêm vào cuối hàm:

  document.querySelectorAll('#mobileNav button').forEach(b => {
    const isActive = b.dataset.mob === id;
    b.style.color = isActive ? 'var(--purple)' : 'var(--text3)';
    b.style.borderTopColor = isActive ? 'var(--purple)' : 'transparent';
  });

---

### BƯỚC 4 — Test Checklist

- [ ] Chrome mobile → Settings → "Add to Home Screen" → có icon
- [ ] Icon màu tím, tên "StockAI"
- [ ] Bottom nav hiện trên mobile, ẩn trên desktop
- [ ] Sidebar ẩn trên mobile
- [ ] Input không zoom khi focus
- [ ] Table readable trên 375px
- [ ] Calculator 1 cột
- [ ] Login OK trên mobile
- [ ] Scroll mượt

Sau khi làm xong báo lại để test trên điện thoại thật.

---

## 🎯 RADAR REDESIGN — UX Đơn Giản Hóa

### Vấn đề hiện tại
- Sidebar 6 mục quá nhiều → gộp còn 3
- Header search thừa, trùng với ô thêm mã bên dưới → bỏ
- Watchlist trống khi mới vào → user không biết làm gì
- Scan chậm vì chạy realtime → dùng pre-computed cache

---

### THAY ĐỔI 1 — Navigation gộp còn 3 module

Bỏ: Alerts (gộp vào Danh Mục), Chart KT (gộp vào Phân Tích)

Sidebar mới:
- 🔍 Khám Phá  ← Radar + Phân Tích + Chart KT
- 💼 Danh Mục  ← Portfolio + Alerts
- 🧮 Công Cụ   ← Calculator + Lịch sử

---

### THAY ĐỔI 2 — Bỏ header search bar

Xóa ô search trong app-header khỏi dashboard.html.
Giữ lại avatar, icon scan, icon notification thôi.
Việc tìm mã và thêm watchlist gộp vào 1 ô duy nhất trong Radar.

---

### THAY ĐỔI 3 — Ô tìm mã duy nhất trong Radar

Logic:
- Gõ mã → autocomplete gợi ý tên công ty
- Enter hoặc click gợi ý → thêm vào watchlist → scan mã đó luôn
- 1 hành động duy nhất, không cần nút riêng

HTML:

```html
<div class="search-add-wrap">
  <input type="text" id="radarSearch" 
    placeholder="🔍 Tìm mã hoặc tên công ty... (VD: FPT, Vinamilk)"
    autocomplete="off"
    oninput="showAutocomplete(this.value)"
    onkeydown="if(event.key==='Enter') addAndScan(this.value)">
  <div id="autocompleteList" class="autocomplete-dropdown"></div>
</div>
```

CSS:

```css
.search-add-wrap {
  position: relative;
  margin-bottom: 14px;
}
.search-add-wrap input {
  width: 100%;
  padding: 12px 16px;
  border: 1.5px solid var(--border);
  border-radius: 10px;
  font-size: .9rem;
  background: #fff;
  outline: none;
  transition: border-color .15s;
}
.search-add-wrap input:focus { border-color: var(--purple); }
.autocomplete-dropdown {
  position: absolute;
  top: 100%; left: 0; right: 0;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0,0,0,.1);
  z-index: 100;
  display: none;
  max-height: 240px;
  overflow-y: auto;
}
.autocomplete-item {
  padding: 10px 14px;
  cursor: pointer;
  font-size: .85rem;
  display: flex;
  gap: 10px;
  align-items: center;
}
.autocomplete-item:hover { background: var(--purple-light); }
.autocomplete-item .sym { font-weight: 700; color: var(--purple); width: 48px; }
.autocomplete-item .name { color: var(--text2); }
```

JS — Autocomplete từ danh sách VN100 hardcode:

```javascript
const VN100_LIST = [
  {sym:"ACB",name:"Ngân hàng ACB"},
  {sym:"BCM",name:"Becamex IDC"},
  {sym:"BID",name:"Ngân hàng BIDV"},
  {sym:"BVH",name:"Tập đoàn Bảo Việt"},
  {sym:"CTG",name:"Ngân hàng VietinBank"},
  {sym:"FPT",name:"Tập đoàn FPT"},
  {sym:"GAS",name:"PV GAS"},
  {sym:"GVR",name:"Tập đoàn Công nghiệp Cao su"},
  {sym:"HDB",name:"Ngân hàng HDBank"},
  {sym:"HPG",name:"Tập đoàn Hòa Phát"},
  {sym:"MBB",name:"Ngân hàng MBBank"},
  {sym:"MSN",name:"Tập đoàn Masan"},
  {sym:"MWG",name:"Thế Giới Di Động"},
  {sym:"NVL",name:"Novaland"},
  {sym:"PDR",name:"Phát Đạt"},
  {sym:"PLX",name:"Petrolimex"},
  {sym:"POW",name:"PV Power"},
  {sym:"SAB",name:"Sabeco"},
  {sym:"SHB",name:"Ngân hàng SHB"},
  {sym:"SSB",name:"Ngân hàng SeABank"},
  {sym:"SSI",name:"Chứng khoán SSI"},
  {sym:"STB",name:"Ngân hàng Sacombank"},
  {sym:"TCB",name:"Ngân hàng Techcombank"},
  {sym:"TPB",name:"Ngân hàng TPBank"},
  {sym:"VCB",name:"Ngân hàng Vietcombank"},
  {sym:"VHM",name:"Vinhomes"},
  {sym:"VIB",name:"Ngân hàng VIB"},
  {sym:"VIC",name:"Tập đoàn Vingroup"},
  {sym:"VJC",name:"Vietjet Air"},
  {sym:"VNM",name:"Vinamilk"},
  {sym:"VPB",name:"Ngân hàng VPBank"},
  {sym:"VRE",name:"Vincom Retail"},
  {sym:"VSH",name:"Thủy điện Vĩnh Sơn"},
  {sym:"VTO",name:"Vận tải Xăng dầu Vitaco"},
  {sym:"DGC",name:"Hóa chất Đức Giang"},
  {sym:"DXG",name:"Đất Xanh Group"},
  {sym:"EIB",name:"Ngân hàng Eximbank"},
  {sym:"EVF",name:"Tài chính Điện lực"},
  {sym:"GEX",name:"Tập đoàn Gelex"},
  {sym:"GMD",name:"Gemadept"},
  {sym:"HAG",name:"Hoàng Anh Gia Lai"},
  {sym:"HCM",name:"Chứng khoán HCM"},
  {sym:"HDG",name:"Tập đoàn Hà Đô"},
  {sym:"HSG",name:"Hoa Sen Group"},
  {sym:"IDC",name:"Kinh doanh và Phát triển Bình Dương"},
  {sym:"IMP",name:"Imexpharm"},
  {sym:"KBC",name:"Khu công nghiệp Kinh Bắc"},
  {sym:"KDH",name:"Khang Điền"},
  {sym:"LPB",name:"Ngân hàng LienVietPostBank"},
  {sym:"NAB",name:"Ngân hàng Nam Á"},
  {sym:"OCB",name:"Ngân hàng OCB"},
  {sym:"PNJ",name:"Vàng bạc Đá quý Phú Nhuận"},
  {sym:"PVD",name:"PV Drilling"},
  {sym:"PVT",name:"Vận tải Dầu khí"},
  {sym:"REE",name:"Cơ điện lạnh REE"},
  {sym:"SBT",name:"Đường TTC Biên Hòa"},
  {sym:"VCI",name:"Chứng khoán Vietcap"},
  {sym:"VGC",name:"Viglacera"},
  {sym:"VGI",name:"Viettel Global"},
  {sym:"VHC",name:"Vĩnh Hoàn"},
  {sym:"VND",name:"Chứng khoán VNDirect"},
  {sym:"VPI",name:"Văn Phú Invest"},
];

function showAutocomplete(val) {
  const q = val.trim().toUpperCase();
  const dropdown = document.getElementById('autocompleteList');
  if (!q || q.length < 1) { dropdown.style.display = 'none'; return; }
  const matches = VN100_LIST.filter(s =>
    s.sym.includes(q) || s.name.toUpperCase().includes(q)
  ).slice(0, 8);
  if (!matches.length) { dropdown.style.display = 'none'; return; }
  dropdown.innerHTML = matches.map(s =>
    `<div class="autocomplete-item" onclick="addAndScan('${s.sym}')">
      <span class="sym">${s.sym}</span>
      <span class="name">${s.name}</span>
    </div>`
  ).join('');
  dropdown.style.display = 'block';
}

async function addAndScan(ticker) {
  const t = ticker.trim().toUpperCase();
  if (!t) return;
  document.getElementById('radarSearch').value = '';
  document.getElementById('autocompleteList').style.display = 'none';
  // Thêm vào watchlist
  await fetch('/api/watchlist/add', {
    method: 'POST',
    headers: {'Content-Type':'application/json','X-Token': authToken},
    body: JSON.stringify({ticker: t})
  });
  // Reload radar
  loadRadar();
}

// Đóng dropdown khi click ra ngoài
document.addEventListener('click', e => {
  if (!e.target.closest('.search-add-wrap')) {
    document.getElementById('autocompleteList').style.display = 'none';
  }
});
```

---

### THAY ĐỔI 4 — Default VN30 Top 10 khi watchlist trống

```python
VN30_TOP10 = ["VCB","BID","CTG","FPT","MWG","HPG","GAS","VIC","VHM","MSN"]

def load_watchlist(username: str) -> list:
    fp = f"watchlist_{username}.json"
    if os.path.exists(fp):
        try:
            with open(fp) as f:
                wl = json.load(f)
                if wl: return wl
        except: pass
    # Watchlist trống → trả về VN30 top 10 mặc định
    return VN30_TOP10
```

---

### THAY ĐỔI 5 — Pre-computed Radar Cache

Scheduler chạy lúc 8:00 sáng mỗi ngày (giờ VN), scan VN30_TOP10 + watchlist của tất cả users, lưu vào cache:

```python
# File: vn30_radar_cache.json
{
  "updated_at": "2025-06-03T08:00:00",
  "results": [
    {
      "ticker": "FPT",
      "signal": {"combined": 85, "action": "XEM", "color": "green"},
      "buffett_score": 11,
      "price": 125000,
      "ichi_label": "Trên mây",
      "volume_warning": null
    },
    ...
  ]
}

# Scheduler job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler(timezone="Asia/Ho_Chi_Minh")

@scheduler.scheduled_job(CronTrigger(hour=8, minute=0))
async def daily_vn30_scan():
    results = []
    for ticker in VN30_TOP10:
        try:
            score  = compute_buffett_score(ticker)
            ta     = compute_technical(ticker, period=90)
            signal = calc_combined_signal(score["score"], ta)
            results.append({
                "ticker": ticker,
                "signal": signal,
                "buffett_score": score["score"],
                "price": ta["latest"].get("close"),
                "ichi_label": signal["ichi_label"],
                "volume_warning": ta.get("volume_signal", {}).get("warning"),
            })
            await asyncio.sleep(1)  # tránh spam API
        except Exception as e:
            print(f"Scan {ticker} lỗi: {e}")
    results.sort(key=lambda x: x["signal"]["combined"], reverse=True)
    with open("vn30_radar_cache.json", "w") as f:
        json.dump({"updated_at": datetime.datetime.now().isoformat(),
                   "results": results}, f, ensure_ascii=False, cls=SafeEncoder)

# Endpoint Radar đọc từ cache trước, fallback realtime
@app.get("/api/radar")
async def get_radar(request: Request):
    user = get_current_user(request)
    watchlist = load_watchlist(user)

    # Load VN30 cache
    vn30_results = []
    if os.path.exists("vn30_radar_cache.json"):
        with open("vn30_radar_cache.json") as f:
            cache = json.load(f)
            vn30_results = cache.get("results", [])
            updated_at   = cache.get("updated_at")

    # Merge watchlist cá nhân (ưu tiên hiện trước)
    wl_tickers = set(watchlist) - set(VN30_TOP10)
    wl_results = []
    for ticker in wl_tickers:
        try:
            score  = compute_buffett_score(ticker)
            ta     = compute_technical(ticker, period=90)
            signal = calc_combined_signal(score["score"], ta)
            wl_results.append({
                "ticker": ticker,
                "signal": signal,
                "buffett_score": score["score"],
                "price": ta["latest"].get("close"),
                "ichi_label": signal["ichi_label"],
                "volume_warning": ta.get("volume_signal",{}).get("warning"),
                "in_watchlist": True,
            })
        except: pass

    all_results = wl_results + vn30_results
    return ok({"results": all_results, "updated_at": updated_at})
```

---

### CHECKLIST

- [ ] Gộp sidebar còn 3 module: Khám Phá, Danh Mục, Công Cụ
- [ ] Xóa header search bar
- [ ] Thêm ô tìm mã + autocomplete VN100 vào Radar
- [ ] Default VN30 top 10 khi watchlist trống
- [ ] Pre-computed cache scan VN30 lúc 8:00 sáng
- [ ] Scheduler chạy khi server start
- [ ] Test autocomplete gõ tên tiếng Việt + mã
