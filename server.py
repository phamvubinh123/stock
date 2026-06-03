#!/usr/bin/env python3
"""
Stock Agent AI — FastAPI Edition
Gộp app.py (Warren Buffett analyzer) + main.py (price board, technical, Claude AI)
+ APScheduler daily scan + watchlist management

Chạy: uvicorn server:app --reload --port 8000
"""

import json
import math
import os
import asyncio
import datetime
import logging
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("stock-agent")

# ─────────────────────────────────────────────
# DATABASE — PostgreSQL via psycopg2
# ─────────────────────────────────────────────
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from psycopg2.pool import ThreadedConnectionPool
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

DATABASE_URL = os.environ.get("DATABASE_URL", "")
_pool = None

def get_pool():
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL chưa được set")
        _pool = ThreadedConnectionPool(1, 10, DATABASE_URL, sslmode="require")
    return _pool

def get_conn():
    return get_pool().getconn()

def put_conn(conn):
    get_pool().putconn(conn)

def db_exec(sql: str, params=None, fetch: str = None):
    """Chạy SQL, fetch='one'|'all'|None."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            conn.commit()
            if fetch == "one":  return cur.fetchone()
            if fetch == "all":  return cur.fetchall()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        put_conn(conn)

def init_db():
    """Tạo tables nếu chưa có."""
    ddl = """
    CREATE TABLE IF NOT EXISTS users (
        username VARCHAR(50) PRIMARY KEY,
        pin      VARCHAR(10) NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS watchlists (
        username VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
        ticker   VARCHAR(10) NOT NULL,
        position INT DEFAULT 0,
        PRIMARY KEY (username, ticker)
    );
    CREATE TABLE IF NOT EXISTS dca_transactions (
        id       SERIAL PRIMARY KEY,
        username VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
        ticker   VARCHAR(10) NOT NULL,
        shares   FLOAT NOT NULL,
        price    FLOAT NOT NULL,
        date     DATE,
        created_at TIMESTAMP DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS daily_picks (
        username   VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
        data       JSONB NOT NULL,
        scanned_at TIMESTAMP DEFAULT NOW(),
        PRIMARY KEY (username)
    );
    CREATE TABLE IF NOT EXISTS calc_history (
        id         VARCHAR(50) PRIMARY KEY,
        username   VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
        type       VARCHAR(50),
        data       JSONB,
        created_at TIMESTAMP DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS portfolio (
        id           VARCHAR(50) PRIMARY KEY,
        username     VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
        ticker       VARCHAR(10) NOT NULL,
        shares       FLOAT NOT NULL,
        avg_price    FLOAT NOT NULL,
        bought_at    DATE,
        note         TEXT DEFAULT '',
        target_price FLOAT,
        stop_loss    FLOAT,
        created_at   TIMESTAMP DEFAULT NOW(),
        updated_at   TIMESTAMP DEFAULT NOW()
    );
    CREATE TABLE IF NOT EXISTS alerts (
        id           VARCHAR(50) PRIMARY KEY,
        username     VARCHAR(50) REFERENCES users(username) ON DELETE CASCADE,
        ticker       VARCHAR(10) NOT NULL,
        type         VARCHAR(30) NOT NULL,
        value        FLOAT NOT NULL,
        active       BOOLEAN DEFAULT TRUE,
        triggered_at TIMESTAMP,
        last_checked TIMESTAMP,
        created_at   TIMESTAMP DEFAULT NOW()
    );
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
        log.info("✅ DB tables ready")
    except Exception as e:
        conn.rollback()
        log.error(f"DB init error: {e}")
    finally:
        put_conn(conn)

USE_DB = bool(DATABASE_URL) and HAS_PSYCOPG2

# ─────────────────────────────────────────────
# WATCHLIST CONFIG — sửa thoải mái
# ─────────────────────────────────────────────
DEFAULT_WATCHLIST = ["FPT", "VCB"]

# File lưu watchlist & daily results (fallback khi không có DB)
WATCHLIST_FILE  = "watchlist.json"
DAILY_RESULT_FILE = "daily_picks.json"


# ─────────────────────────────────────────────
# SAFE JSON
# ─────────────────────────────────────────────
class SafeEncoder(json.JSONEncoder):
    def default(self, obj):
        try:
            import numpy as np
            if isinstance(obj, np.integer): return int(obj)
            if isinstance(obj, np.floating):
                v = float(obj)
                return None if (math.isnan(v) or math.isinf(v)) else v
            if isinstance(obj, np.ndarray): return obj.tolist()
        except ImportError:
            pass
        if isinstance(obj, datetime.date): return str(obj)
        return super().default(obj)

    def encode(self, obj):
        return super().encode(self._fix(obj))

    def _fix(self, obj):
        if isinstance(obj, float):
            return None if (math.isnan(obj) or math.isinf(obj)) else obj
        if isinstance(obj, dict):  return {k: self._fix(v) for k, v in obj.items()}
        if isinstance(obj, list):  return [self._fix(i) for i in obj]
        return obj


def ok(data, status=200):
    return Response(
        content=json.dumps(data, cls=SafeEncoder, ensure_ascii=False),
        media_type="application/json",
        status_code=status,
    )


def safe_float(val, default=0.0):
    try:
        v = float(val)
        return None if (math.isnan(v) or math.isinf(v)) else v
    except:
        return default


def safe_int(val, default=0):
    try:
        return int(val) if val is not None else default
    except:
        return default


# ─────────────────────────────────────────────
# WATCHLIST HELPERS
# ─────────────────────────────────────────────
def load_watchlist(username: str = "default") -> List[str]:
    if USE_DB:
        try:
            rows = db_exec("SELECT ticker FROM watchlists WHERE username=%s ORDER BY position", (username,), fetch="all")
            if rows is not None:
                tickers = [r["ticker"] for r in rows]
                return tickers if tickers else list(DEFAULT_WATCHLIST)
        except Exception as e:
            log.warning(f"DB load_watchlist error: {e}")
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE) as f:
                return json.load(f)
        except:
            pass
    return list(DEFAULT_WATCHLIST)


def save_watchlist(symbols: List[str], username: str = "default"):
    symbols = [s.upper().strip() for s in symbols]
    if USE_DB:
        try:
            db_exec("DELETE FROM watchlists WHERE username=%s", (username,))
            for i, t in enumerate(symbols):
                db_exec("INSERT INTO watchlists (username,ticker,position) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (username, t, i))
            return
        except Exception as e:
            log.warning(f"DB save_watchlist error: {e}")
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(symbols, f)


def load_daily_result(username: str = "default"):
    if USE_DB:
        try:
            row = db_exec("SELECT data FROM daily_picks WHERE username=%s", (username,), fetch="one")
            if row:
                return row["data"]
        except Exception as e:
            log.warning(f"DB load_daily_result error: {e}")
    if os.path.exists(DAILY_RESULT_FILE):
        try:
            with open(DAILY_RESULT_FILE) as f:
                return json.load(f)
        except:
            pass
    return None


def save_daily_result(data, username: str = "default"):
    if USE_DB:
        try:
            data_json = json.dumps(data, cls=SafeEncoder, ensure_ascii=False)
            db_exec("""
                INSERT INTO daily_picks (username, data, scanned_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (username) DO UPDATE SET data=%s::jsonb, scanned_at=NOW()
            """, (username, data_json, data_json))
            return
        except Exception as e:
            log.warning(f"DB save_daily_result error: {e}")
    with open(DAILY_RESULT_FILE, "w") as f:
        json.dump(data, cls=SafeEncoder, fp=f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# VNSTOCK HELPERS
# ─────────────────────────────────────────────
def get_finance():
    """Import Finance từ vnstock 4.x (thay thế Vnstock().stock().finance)."""
    try:
        from vnstock import Finance
        return Finance
    except (ImportError, ModuleNotFoundError):
        raise HTTPException(503, "Chưa cài vnstock. Chạy: pip install vnstock --upgrade")


def get_trading():
    from vnstock import Trading
    return Trading


# ─────────────────────────────────────────────
# CACHE — in-memory, TTL theo loại dữ liệu
# ─────────────────────────────────────────────
import time as _time
_cache: dict = {}
CACHE_TTL = {"bctc": 86400, "price": 300, "technical": 300, "scan": 300}

def cache_get(key: str):
    if key in _cache:
        data, ts = _cache[key]
        ttl_type = key.split(":")[0]
        ttl = CACHE_TTL.get(ttl_type, 300)
        if _time.time() - ts < ttl:
            return data
        del _cache[key]
    return None

def cache_set(key: str, data):
    _cache[key] = (data, _time.time())

def cache_clear(prefix: str = ""):
    keys = [k for k in _cache if k.startswith(prefix)] if prefix else list(_cache.keys())
    for k in keys:
        del _cache[k]


def get_quote():
    from vnstock import Quote
    return Quote


def safe_float_v(v, default=0.0):
    try:
        if v is None:
            return default
        # vnstock3 ratio() có thể trả Series khi cột trùng tên
        if hasattr(v, "iloc") and not isinstance(v, (str, bytes)):
            v = v.iloc[-1] if len(v) else default
        return float(v)
    except (TypeError, ValueError):
        return default


def pad5(arr, n=5):
    arr = list(arr)
    while len(arr) < n:
        arr.insert(0, 0)
    return arr[-n:]


def find_row(df, *keywords):
    for col in ["item_en", "item"]:
        if col not in df.columns:
            continue
        for kw in keywords:
            # literal match — tránh (loss), Lãi/(lỗ) bị hiểu là regex group
            mask = df[col].astype(str).str.contains(kw, case=False, na=False, regex=False)
            if mask.any():
                return df[mask].iloc[0]
    return None


def get_year_cols(df):
    skip = {"item", "item_en", "item_id", "period"}
    seen = set()
    cols = []
    for c in df.columns:
        if c in skip:
            continue
        s = str(c)
        if len(s) == 4 and s.isdigit() and s not in seen:
            cols.append(c)
            seen.add(s)
    return sorted(cols, key=str)


def row_cell(row, col):
    """Đọc 1 ô — xử lý cột trùng tên (ratio vnstock3)."""
    if row is None:
        return 0.0
    try:
        val = row[col]
        if hasattr(val, "iloc") and not isinstance(val, (str, bytes)):
            val = val.iloc[-1] if len(val) else 0
        return safe_float_v(val)
    except (KeyError, TypeError):
        return 0.0


def ratio_row_values(df, n, *keywords):
    """Lấy chuỗi chỉ số từ ratio() — cột năm có thể trùng tên (lặp theo chu kỳ 4)."""
    row = find_row(df, *keywords)
    if row is None:
        return [0.0] * n
    vals = [safe_float_v(v) for v in row.iloc[3:]]
    if not vals:
        return [0.0] * n
    period = 4
    chunk = vals[-period:] if len(vals) >= period else vals[-n:]
    while len(vals) > period and chunk == vals[-2 * period : -period]:
        vals = vals[:-period]
        chunk = vals[-period:]
    return pad5(chunk, n)


def row_values(df, n, *keywords):
    year_cols = get_year_cols(df)[-n:]
    row = find_row(df, *keywords)
    if row is None:
        return [0] * n, year_cols
    return [row_cell(row, c) for c in year_cols], year_cols


def to_mil(vals):
    return [round(v / 1_000_000, 2) for v in vals]


# ─────────────────────────────────────────────
# BUFFETT SCORE ENGINE
# ─────────────────────────────────────────────
def compute_buffett_score(data: dict, n: int = 5) -> dict:
    """
    Tính 14 tiêu chí Buffett từ data dict.
    Trả về dict { score, details, key_metrics }
    """
    N = n - 1

    def g(key):
        return data.get(key, [0] * n)

    dt   = g("dt");   lng  = g("lng"); lv   = g("lv")
    cpbh = g("cpbh"); cpql = g("cpql"); lntt = g("lntt")
    thue = g("thue"); lnst = g("lnst"); pe   = g("pe")
    gssk = g("gssk"); tien = g("tien"); htk  = g("htk")
    tsnh = g("tsnh"); ts   = g("ts");   nnh  = g("nnh")
    ndh  = g("ndh");  phcp = g("phcp")
    vcsh_direct = g("vcsh")  # lấy trực tiếp từ balance nếu có

    totNo = [nnh[i] + ndh[i] for i in range(n)]
    # Ưu tiên VCSH trực tiếp (chính xác hơn), fallback tính từ TS - Nợ
    vcsh  = [vcsh_direct[i] if vcsh_direct[i] != 0 else ts[i] - totNo[i] for i in range(n)]

    def div(a, b):
        return a / b if b and b != 0 else float("nan")

    # ROE = LNST_total / VCSH_total bình quân (khớp SSI vì tỷ lệ minority ~đồng đều)
    # ROA = LNST_parent / TS bình quân (SSI dùng LNST cổ đông công ty mẹ cho ROA)
    lnst_parent = g("lnst_parent")
    lnst_for_roa = [lnst_parent[i] if lnst_parent[i] != 0 else lnst[i] for i in range(n)]
    avg_vcsh = [(vcsh[i] + vcsh[i-1]) / 2 if i > 0 and vcsh[i-1] != 0 else vcsh[i] for i in range(n)]
    avg_ts   = [(ts[i]   + ts[i-1])   / 2 if i > 0 and ts[i-1]   != 0 else ts[i]   for i in range(n)]
    roe   = [div(lnst[i],         avg_vcsh[i]) for i in range(n)]
    roa   = [div(lnst_for_roa[i], avg_ts[i])   for i in range(n)]
    gm    = [div(lng[i], dt[i])     for i in range(n)]
    lvR   = [div(abs(lv[i]), lng[i])   for i in range(n)]
    cpbhR = [div(abs(cpbh[i]), dt[i])  for i in range(n)]
    cpqlR = [div(abs(cpql[i]), lng[i]) for i in range(n)]
    htkR  = [div(htk[i], tsnh[i])   for i in range(n)]
    noR   = [div(totNo[i], ts[i])   for i in range(n)]
    ttnR  = [div(tien[i], nnh[i])   for i in range(n)]

    def is_consistent(arr):
        drops = sum(1 for i in range(1, len(arr)) if arr[i] < arr[i-1])
        return drops <= 1

    score = 0
    details = []

    def add(num, label, threshold, result, passed, remark):
        nonlocal score
        if passed: score += 1
        details.append({
            "num": num, "label": label, "threshold": threshold,
            "result": result, "passed": passed, "remark": remark
        })

    dC = is_consistent(dt); lC = is_consistent(lng)
    add(1, "Tăng trưởng nhất quán DT & LNG", "Nhất quán",
        f"DT:{'✓' if dC else '✗'} LNG:{'✓' if lC else '✗'}",
        dC or lC,
        "Cả hai nhất quán" if dC and lC else "Một chỉ số nhất quán" if dC or lC else "Thiếu nhất quán")

    gmL = gm[N] if not math.isnan(gm[N]) else 0
    add(2, "Biên LN Gộp ≥20%", "≥20%", f"{gmL*100:.1f}%", gmL >= 0.20,
        "Siêu hạng ≥30%" if gmL >= 0.30 else "Đạt chuẩn" if gmL >= 0.20 else "Thấp")

    lvL = lvR[N] if not math.isnan(lvR[N]) else 999
    add(3, "Lãi vay / LNG ≤40%", "≤40%", f"{lvL*100:.1f}%", lvL <= 0.40,
        "Ít phụ thuộc vốn vay" if lvL <= 0.10 else "OK" if lvL <= 0.40 else "Lãi vay cao")

    roaL = roa[N] if not math.isnan(roa[N]) else 0
    roeL = roe[N] if not math.isnan(roe[N]) else 0
    add(4, "ROA < ROE", "ROA < ROE", f"ROA:{roaL*100:.1f}% ROE:{roeL*100:.1f}%",
        roaL < roeL, "Cấu trúc vốn hợp lý" if roaL < roeL else "Bất thường")

    add(5, "ROE ≥ 20%", "≥20%", f"{roeL*100:.1f}%", roeL >= 0.20,
        "Đạt chuẩn Buffett" if roeL >= 0.20 else "Dưới chuẩn")

    cpbhL = cpbhR[N] if not math.isnan(cpbhR[N]) else 999
    add(6, "CPBH / DT nhất quán", "Ổn định", f"{cpbhL*100:.1f}%",
        is_consistent([-v for v in cpbhR]),
        "Thương hiệu mạnh" if cpbhL < 0.05 else "Theo dõi xu hướng")

    peL = pe[N] if pe[N] and not math.isnan(pe[N]) else 0
    add(7, "P/E ≤ 10", "≤10", f"{peL:.1f}x" if peL > 0 else "—",
        0 < peL <= 10,
        f"Vùng mua vào" if peL <= 10 and peL > 0 else "Định giá cao" if peL > 15 else "Trung bình")

    cpqlL = cpqlR[N] if not math.isnan(cpqlR[N]) else 999
    add(8, "CPQLDN / LNG ≤20%", "≤20%", f"{cpqlL*100:.1f}%", cpqlL <= 0.20,
        "QLDN tốt" if cpqlL <= 0.20 else "Cần cải thiện")

    nhL = tsnh[N] >= nnh[N]
    add(9, "TSNH ≥ Nợ Ngắn Hạn", "TSNH≥NNH",
        f"Dư {round(tsnh[N]-nnh[N]):,}tr" if nhL else f"Thiếu {round(nnh[N]-tsnh[N]):,}tr",
        nhL, "OK" if nhL else "Theo dõi thanh khoản")

    ttnL = ttnR[N] if not math.isnan(ttnR[N]) else 0
    add(10, "Tiền / NNH ≥ 1x", "≥1x", f"{ttnL:.2f}x", ttnL >= 1,
        "Tiền mặt đủ trả NNH" if ttnL >= 1 else "Rủi ro thanh khoản")

    ndhL = ndh[N] <= 4 * lntt[N] if lntt[N] > 0 else False
    add(11, "NDH ≤ 4× LNTT", "≤4×LNTT", "Trả được" if ndhL else "Không trả được",
        ndhL, "OK" if ndhL else f"NDH quá lớn")

    tmL = tien[N] >= totNo[N]
    add(12, "Tiền ≥ Tổng Nợ", "Tiền≥Nợ", "An toàn" if tmL else "Cần theo dõi",
        tmL, "Cực kỳ an toàn" if tmL else "Bình thường")

    htkL = htkR[N] if not math.isnan(htkR[N]) else 999
    add(13, "HTK / TSNH ≤40%", "≤40%", f"{htkL*100:.1f}%", htkL <= 0.40,
        "Xuất sắc" if htkL <= 0.10 else "Chấp nhận" if htkL <= 0.40 else "Cao")

    noPH = all(v == 0 for v in phcp)
    add(14, "Không phát hành CP", "Không PH",
        "Không PH" if noPH else f"{sum(1 for v in phcp if v > 0)} năm có PH",
        noPH, "Không pha loãng" if noPH else "Có phát hành CP")

    # Fallback ratio vnstock khi tính từ BCTC không ra (lnst/vcsh/ts = 0)
    roe_ratio = g("roe_ratio")
    roa_ratio = g("roa_ratio")
    if (roeL == 0 or math.isnan(roeL)) and roe_ratio[N] and abs(roe_ratio[N]) < 5:
        roeL = roe_ratio[N]
    if (roaL == 0 or math.isnan(roaL)) and roa_ratio[N] and abs(roa_ratio[N]) < 5:
        roaL = roa_ratio[N]

    # Key metrics summary
    key_metrics = {
        "score": score,
        "roe": round(roeL * 100, 1) if abs(roeL) < 5 else round(roeL, 1),
        "roa": round(roaL * 100, 1) if abs(roaL) < 5 else round(roaL, 1),
        "gross_margin": round(gmL * 100, 1),
        "pe": round(peL, 1),
        "debt_ratio": round((noR[N] if not math.isnan(noR[N]) else 0) * 100, 1),
        "quick_ratio": round(ttnL, 2),
    }

    # Trả thêm mảng ROE/ROA theo từng năm để frontend dùng (đã dùng bình quân)
    def pct(v): return round(v * 100, 2) if not math.isnan(v) else None
    return {
        "score": score, "max": 14, "details": details, "key_metrics": key_metrics,
        "roe_arr": [pct(v) for v in roe],
        "roa_arr": [pct(v) for v in roa],
    }


# ─────────────────────────────────────────────
# DAILY SCAN ENGINE
# ─────────────────────────────────────────────
def scan_one_ticker(ticker: str) -> dict:
    """Scan 1 mã (sync), trả về dict tóm tắt."""
    try:
        Finance = get_finance()
        fin = Finance(symbol=ticker, source="VCI")
        n = 5

        data = {}
        years = []

        # Income
        try:
            inc = fin.income_statement(period="year")
            if inc is not None and not inc.empty:
                year_cols = get_year_cols(inc)[-n:]
                years = list(year_cols)
                is_bank = find_row(inc, "Net Interest Income") is not None

                def gv(*kws):
                    row = find_row(inc, *kws)
                    if row is None: return [0] * n
                    return to_mil(pad5([row_cell(row, c) for c in year_cols], n))

                if is_bank:
                    data["dt"]   = gv("Total Operating Income", "Tổng thu nhập hoạt động")
                    data["lng"]  = gv("Net Interest Income", "Thu nhập lãi thuần")
                    data["lv"]   = gv("Interest and Similar Expenses")
                    data["cpbh"] = gv("Fees and Commission Expenses")
                    data["cpql"] = gv("General and Admin")
                    data["lntt"] = gv("Net Accounting Profit")
                    data["lnst"] = gv("Net profit")
                else:
                    data["dt"]   = gv("Net sales", "Doanh thu thuần", "Net revenue")
                    data["lng"]  = gv("Gross Profit", "Gross profit", "Lợi nhuận gộp")
                    data["lv"]   = gv("Interest expenses", "Interest expense", "Chi phí lãi vay")
                    data["cpbh"] = gv("Selling expenses", "Selling expense", "Chi phí bán hàng")
                    data["cpql"] = gv("General and admin expenses", "General", "Chi phí quản lý doanh nghiệp", "Chi phí quản lý")
                    data["lntt"] = gv("Net accounting profit/(loss) before tax", "Profit before tax", "Lãi/(lỗ) trước thuế", "Lợi nhuận trước thuế")
                    data["lnst"] = gv("Net profit/(loss) after tax", "Profit after tax", "Lãi/(lỗ) thuần sau thuế", "Lợi nhuận sau thuế")
                    data["lnst_parent"] = gv(
                        "Attributable to parent company",
                        "Lợi nhuận của Cổ đông của Công ty mẹ",
                        "Profit attributable to owners",
                    )

                data["thue"] = [max(0, round(a - b, 2)) for a, b in zip(data["lntt"], data["lnst"])]
        except Exception as e:
            log.warning(f"{ticker} income: {e}")

        for k in ["dt", "lng", "lv", "cpbh", "cpql", "lntt", "lnst", "thue"]:
            if k not in data: data[k] = [0] * n

        # Balance
        try:
            bal = fin.balance_sheet(period="year")
            if bal is not None and not bal.empty:
                year_cols_b = get_year_cols(bal)[-n:]
                # DEBUG: xem tên các dòng trong balance sheet
                for col in ["item_en", "item"]:
                    if col in bal.columns:
                        log.info(f"[DEBUG balance cols={col}] {list(bal[col].astype(str))}")
                        break
                def gvb(*kws):
                    row = find_row(bal, *kws)
                    if row is None: return [0] * n
                    return to_mil(pad5([row_cell(row, c) for c in year_cols_b], n))

                data["tien"] = gvb("Cash and cash equivalents", "Cash", "Tiền và tương đương tiền")
                data["htk"]  = gvb("Inventories, Net", "Inventories", "Hàng tồn kho", "Hàng tồn kho, ròng")
                data["tsnh"] = gvb("CURRENT ASSETS", "Total current assets", "TÀI SẢN NGẮN HẠN", "Tổng tài sản ngắn hạn")
                data["ts"]   = gvb("Total Assets", "TOTAL ASSETS", "TỔNG CỘNG TÀI SẢN", "TỔNG TÀI SẢN", "Tổng cộng tài sản")
                data["nnh"]  = gvb("Current liabilities", "Total current liabilities", "Nợ ngắn hạn")
                data["ndh"]  = gvb("Long-term liabilities", "Long.term liabilities", "Nợ dài hạn")
                equity = gvb("Owner's Equity", "Vốn chủ sở hữu", "Vốn và các quỹ", "Capital and reserves")
                minority = gvb("Minority interests", "Lợi ích cổ đông thiểu số", "Minority interest")
                # VCSH đúng = Owner's Equity - Minority interests (theo spec)
                data["vcsh"] = [max(0, e - m) if e > 0 else e for e, m in zip(equity, minority)]
        except Exception as e:
            log.warning(f"{ticker} balance: {e}")

        for k in ["tien", "htk", "tsnh", "ts", "nnh", "ndh", "vcsh"]:
            if k not in data: data[k] = [0] * n

        # PE from ratio
        try:
            ratio = fin.ratio(period="year")
            if ratio is not None and not ratio.empty:
                year_cols_r = get_year_cols(ratio)[-n:]
                row = find_row(ratio, "P/E", "price_to_earning", "priceToEarning")
                data["pe"] = (
                    pad5([row_cell(row, c) for c in year_cols_r], n)
                    if row is not None and year_cols_r
                    else ratio_row_values(ratio, n, "P/E", "price_to_earning", "priceToEarning")
                )
                data["roe_ratio"] = ratio_row_values(ratio, n, "ROE (%)", "ROE", "roe")
                data["roa_ratio"] = ratio_row_values(ratio, n, "ROA (%)", "ROA", "roa")
            else:
                data["pe"] = [0] * n
                data["roe_ratio"] = [0] * n
                data["roa_ratio"] = [0] * n
        except:
            data["pe"] = [0] * n
            data["roe_ratio"] = [0] * n
            data["roa_ratio"] = [0] * n

        data["phcp"] = [0] * n

        # Price
        price = 0.0
        try:
            today = datetime.date.today().strftime("%Y-%m-%d")
            m_ago = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
            quote = stock.quote.history(start=m_ago, end=today)
            if quote is not None and not quote.empty:
                raw_p = safe_float_v(quote["close"].iloc[-1])
                price = round(raw_p / 1000, 2) if raw_p > 1000 else round(raw_p, 2)
        except:
            pass

        buffett = compute_buffett_score(data, n)

        # DCF — dùng năm đầu tiên có data thật (tránh lỗi khi năm đầu bị pad = 0)
        n_idx = n - 1
        cagr = float("nan")
        iv = float("nan")
        lntt_arr = data["lntt"]
        first_idx = next((i for i, v in enumerate(lntt_arr) if v and v > 0), None)
        if first_idx is not None and first_idx < n_idx and lntt_arr[n_idx] > 0:
            years_span = n_idx - first_idx
            cagr = math.pow(lntt_arr[n_idx] / lntt_arr[first_idx], 1 / years_span) - 1

        return {
            "ticker": ticker,
            "price": price,
            "score": buffett["score"],
            "key_metrics": buffett["key_metrics"],
            "years": years,
            "cagr_lntt": round(cagr * 100, 1) if not math.isnan(cagr) else None,
            "ok": True,
        }

    except Exception as e:
        log.error(f"{ticker} scan failed: {e}")
        return {"ticker": ticker, "ok": False, "error": str(e), "score": 0}


async def run_daily_scan(watchlist: Optional[List[str]] = None, username: str = "default") -> dict:
    """Scan toàn bộ watchlist, sắp xếp theo score."""
    symbols = watchlist or load_watchlist(username)
    log.info(f"Daily scan bắt đầu — {len(symbols)} mã: {symbols}")

    loop = asyncio.get_event_loop()
    sem = asyncio.Semaphore(3)

    async def _run(t):
        async with sem:
            return await loop.run_in_executor(None, scan_one_ticker, t)

    results = await asyncio.gather(*[_run(t) for t in symbols])

    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    top_picks = [r for r in results if r.get("score", 0) >= 9]
    watchable  = [r for r in results if 6 <= r.get("score", 0) < 9]
    avoid      = [r for r in results if r.get("score", 0) < 6]

    output = {
        "scanned_at": datetime.datetime.now().isoformat(),
        "total": len(results),
        "top_picks": top_picks,
        "watchable": watchable,
        "avoid": avoid,
        "all_results": results,
    }

    save_daily_result(output, username)
    log.info(f"Daily scan xong — {len(top_picks)} top picks, {len(watchable)} cần theo dõi")
    return output


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    if USE_DB:
        try:
            init_db()
            # Tạo user "default" cho watchlist/daily picks chung
            db_exec("INSERT INTO users (username, pin) VALUES ('default','0000') ON CONFLICT DO NOTHING")
        except Exception as e:
            log.warning(f"DB init warning: {e}")
    log.info("🚀 Stock Agent AI started")

    # Alert checker — chạy mỗi 15 phút
    async def _alert_loop():
        while True:
            await asyncio.sleep(900)  # 15 phút
            await _check_all_alerts()

    alert_task = asyncio.create_task(_alert_loop())
    yield
    alert_task.cancel()
    log.info("Stock Agent AI stopped")


# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────
app = FastAPI(title="Stock Agent AI", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# ROUTES — CORE
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = "dashboard.html"
    if os.path.exists(html_path):
        with open(html_path) as f:
            return f.read()
    return HTMLResponse("<h1>Stock Agent AI — dashboard.html not found</h1>")


@app.get("/api/health")
def health():
    return ok({"status": "ok", "version": "2.0.0", "time": datetime.datetime.now().isoformat()})


def get_username(request: Request) -> str:
    """Lấy username từ session, fallback 'default'."""
    token = request.cookies.get("sa_token") or request.headers.get("X-Token", "")
    return SESSIONS.get(token, "default")

# ─────────────────────────────────────────────
# ROUTES — WATCHLIST
# ─────────────────────────────────────────────

@app.get("/api/watchlist")
def get_watchlist(request: Request):
    return ok({"watchlist": load_watchlist(get_username(request))})


@app.post("/api/watchlist")
async def set_watchlist(request: Request, body: dict = Body(...)):
    symbols = [s.upper().strip() for s in body.get("symbols", []) if s.strip()]
    if not symbols:
        raise HTTPException(400, "Cần ít nhất 1 mã")
    save_watchlist(symbols, get_username(request))
    return ok({"watchlist": symbols, "count": len(symbols)})


@app.post("/api/watchlist/add")
async def add_to_watchlist(request: Request, body: dict = Body(...)):
    ticker = body.get("ticker", "").upper().strip()
    if not ticker:
        raise HTTPException(400, "Thiếu ticker")
    u = get_username(request)
    wl = load_watchlist(u)
    if ticker not in wl:
        wl.append(ticker)
        save_watchlist(wl, u)
    return ok({"watchlist": wl})


@app.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str, request: Request):
    u = get_username(request)
    ticker = ticker.upper()
    wl = [s for s in load_watchlist(u) if s != ticker]
    save_watchlist(wl, u)
    return ok({"watchlist": wl})


# ─────────────────────────────────────────────
# ROUTES — DAILY SCAN & PICKS
# ─────────────────────────────────────────────

@app.get("/api/daily-picks")
def get_daily_picks(request: Request):
    u = get_username(request)
    result = load_daily_result(u)
    if result:
        return ok(result)
    return ok({"message": "Chưa có kết quả. Gọi /api/scan để chạy ngay.", "top_picks": [], "watchable": [], "avoid": []})


@app.post("/api/scan")
async def trigger_scan(request: Request, body: dict = Body(default={})):
    """Trigger scan thủ công. Có thể pass watchlist riêng."""
    u = get_username(request)
    custom = body.get("watchlist")
    result = await run_daily_scan(custom, username=u)
    return ok(result)


@app.get("/api/scan-stream")
async def scan_stream(request: Request):
    """SSE: stream từng mã khi scan xong, frontend render ngay."""
    u = get_username(request)
    symbols = load_watchlist(u)

    async def event_gen():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        sem = asyncio.Semaphore(3)  # tối đa 3 mã song song

        async def _scan_and_put(t):
            async with sem:
                r = await loop.run_in_executor(None, scan_one_ticker, t)
                await queue.put(r)

        tasks = [asyncio.create_task(_scan_and_put(t)) for t in symbols]

        results = []
        for _ in range(len(symbols)):
            r = await queue.get()
            results.append(r)
            yield f"data: {json.dumps(r, ensure_ascii=False, cls=SafeEncoder)}\n\n"

        await asyncio.gather(*tasks)

        # Sau khi xong hết, lưu kết quả và gửi event done
        results.sort(key=lambda x: x.get("score", 0), reverse=True)
        top_picks = [r for r in results if r.get("score", 0) >= 9]
        watchable  = [r for r in results if 6 <= r.get("score", 0) < 9]
        avoid      = [r for r in results if r.get("score", 0) < 6]
        output = {
            "scanned_at": datetime.datetime.now().isoformat(),
            "total": len(results),
            "top_picks": top_picks,
            "watchable": watchable,
            "avoid": avoid,
            "all_results": results,
        }
        save_daily_result(output, u)
        yield f"data: {json.dumps({'__done__': True, **output}, ensure_ascii=False)}\n\n"

    from fastapi.responses import StreamingResponse
    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ─────────────────────────────────────────────
# ROUTES — BUFFETT ANALYSIS (từ app.py)
# ─────────────────────────────────────────────

@app.get("/api/fetch/{ticker}")
async def fetch_ticker(
    ticker: str,
    yearly: str = Query("1"),
    n: int = Query(5),
):
    ticker = ticker.upper().strip()
    period = "year" if yearly == "1" else "quarter"
    cache_key = f"bctc:{ticker}:{period}:{n}"
    cached = cache_get(cache_key)
    if cached:
        log.info(f"[CACHE HIT] {cache_key}")
        return ok(cached)
    Finance = get_finance()

    result = {
        "ticker": ticker, "ok": True, "errors": [],
        "data": {}, "years": [], "is_bank": False,
    }

    try:
        fin = Finance(symbol=ticker, source="VCI")

        # Income
        try:
            inc = fin.income_statement(period=period)
            assert inc is not None and not inc.empty

            year_cols = get_year_cols(inc)[-n:]
            # Pad years với chuỗi rỗng thay vì 0 để frontend không hiện cột trống
            yc = list(year_cols)
            while len(yc) < n:
                yc.insert(0, "")
            result["years"] = yc[-n:]

            def gv(*kws):
                row = find_row(inc, *kws)
                if row is None: return [0] * n
                return to_mil(pad5([row_cell(row, c) for c in year_cols], n))

            is_bank = find_row(inc, "Net Interest Income", "Thu nhập lãi thuần") is not None
            result["is_bank"] = is_bank

            if is_bank:
                result["data"]["dt"]   = gv("Total Operating Income", "Tổng thu nhập hoạt động")
                result["data"]["lng"]  = gv("Net Interest Income", "Thu nhập lãi thuần")
                result["data"]["lv"]   = gv("Interest and Similar Expenses")
                result["data"]["cpbh"] = gv("Fees and Commission Expenses")
                result["data"]["cpql"] = gv("General and Admin")
                result["data"]["lntt"] = gv("Net Accounting Profit", "Tổng lợi nhuận/lỗ trước thuế")
                result["data"]["lnst"] = gv("Net profit", "Lợi nhuận sau thuế")
                result["data"]["lnst_parent"] = gv(
                    "Attributable to parent company",
                    "Cổ đông của Công ty mẹ",
                    "Profit attributable to owners",
                )
            else:
                result["data"]["dt"]   = gv("Net sales", "Doanh thu thuần", "Net revenue", "Total revenue")
                result["data"]["lng"]  = gv("Gross Profit", "Gross profit", "Lợi nhuận gộp")
                result["data"]["lv"]   = gv("Interest expenses", "Interest expense", "Chi phí lãi vay")
                result["data"]["cpbh"] = gv("Selling expenses", "Selling expense", "Chi phí bán hàng")
                result["data"]["cpql"] = gv("General and admin expenses", "General", "Chi phí quản lý doanh nghiệp", "Chi phí quản lý")
                result["data"]["lntt"] = gv("Net accounting profit/(loss) before tax", "Profit before tax", "Lãi/(lỗ) trước thuế", "Lợi nhuận trước thuế")
                result["data"]["lnst"] = gv("Net profit/(loss) after tax", "Profit after tax", "Lãi/(lỗ) thuần sau thuế", "Lợi nhuận sau thuế")
                # LNST cổ đông công ty mẹ — dùng cho ROA (khớp SSI)
                result["data"]["lnst_parent"] = gv(
                    "Attributable to parent company",
                    "Lợi nhuận của Cổ đông của Công ty mẹ",
                    "Profit attributable to owners",
                    "Minority interest",
                )

            result["data"]["thue"] = [
                max(0, round(a - b, 2))
                for a, b in zip(result["data"]["lntt"], result["data"]["lnst"])
            ]
            result["income_ok"] = True

        except Exception as e:
            result["income_ok"] = False
            result["errors"].append(f"Income: {str(e)[:150]}")
            for k in ["dt", "lng", "lv", "cpbh", "cpql", "lntt", "lnst", "thue"]:
                result["data"][k] = [0] * n

        # Balance
        try:
            bal = fin.balance_sheet(period=period)
            assert bal is not None and not bal.empty
            year_cols_b = get_year_cols(bal)[-n:]

            # DEBUG: xem tên các dòng trong balance sheet
            for _col in ["item_en", "item"]:
                if _col in bal.columns:
                    log.info(f"[DEBUG bal rows] {list(bal[_col].astype(str))}")
                    break

            def gvb(*kws):
                row = find_row(bal, *kws)
                if row is None: return [0] * n
                return to_mil(pad5([row_cell(row, c) for c in year_cols_b], n))

            is_bank = result.get("is_bank", False)
            if is_bank:
                result["data"]["tien"] = gvb("Cash and precious", "Tiền mặt, vàng bạc")
                result["data"]["htk"]  = [0] * n
                result["data"]["tsnh"] = [0] * n
                result["data"]["ts"]   = gvb("Total Assets", "TOTAL ASSETS", "TỔNG CỘNG TÀI SẢN", "TỔNG TÀI SẢN")
                result["data"]["nnh"]  = gvb("Deposits from customers", "Tiền gửi của khách hàng")
                result["data"]["ndh"]  = gvb("Convertible bonds", "Phát hành giấy tờ có giá")
            else:
                result["data"]["tien"] = gvb("Cash and cash equivalents", "Cash", "Tiền và tương đương tiền")
                result["data"]["htk"]  = gvb("Inventories, Net", "Inventories", "Hàng tồn kho", "Hàng tồn kho, ròng")
                result["data"]["tsnh"] = gvb("CURRENT ASSETS", "Total current assets", "TÀI SẢN NGẮN HẠN", "Tổng tài sản ngắn hạn")
                result["data"]["ts"]   = gvb("Total Assets", "TOTAL ASSETS", "TỔNG CỘNG TÀI SẢN", "TỔNG TÀI SẢN", "Tổng cộng tài sản")
                result["data"]["nnh"]  = gvb("Current liabilities", "Total current liabilities", "Nợ ngắn hạn")
                result["data"]["ndh"]  = gvb("Long-term liabilities", "Long.term liabilities", "Nợ dài hạn")

            # VCSH đúng = Owner's Equity - Minority interests (theo spec CONTEXT.md)
            equity_arr  = gvb("Owner's Equity", "Vốn chủ sở hữu", "Vốn và các quỹ", "Capital and reserves")
            minority_arr = gvb("Minority interests", "Lợi ích cổ đông thiểu số", "Minority interest")
            result["data"]["vcsh"] = [max(0, e - m) if e > 0 else e for e, m in zip(equity_arr, minority_arr)]

            gssk_row = find_row(bal, "Book value", "Giá trị sổ sách")
            if gssk_row is not None:
                result["data"]["gssk"] = pad5([
                    round(row_cell(gssk_row, c) / 1000, 2) for c in year_cols_b
                ], n)
            else:
                result["data"]["gssk"] = [0] * n

            result["data"]["kl"] = [0] * n
            result["balance_ok"] = True

        except Exception as e:
            result["balance_ok"] = False
            result["errors"].append(f"Balance: {str(e)[:150]}")
            for k in ["tien", "htk", "tsnh", "ts", "nnh", "ndh", "vcsh", "gssk", "kl"]:
                result["data"][k] = [0] * n

        # Cash flow
        try:
            cf = fin.cash_flow(period=period)
            if cf is not None and not cf.empty:
                year_cols_c = get_year_cols(cf)[-n:]
                row = find_row(cf, "Issue", "Phát hành", "issuedShare", "cổ phiếu")
                result["data"]["phcp"] = to_mil(pad5(
                    [safe_float_v(row.get(c, 0)) for c in year_cols_c], n
                )) if row is not None else [0] * n
            else:
                result["data"]["phcp"] = [0] * n
        except Exception as e:
            result["errors"].append(f"Cashflow: {str(e)[:80]}")
            result["data"]["phcp"] = [0] * n

        # Ratio / PE
        # Ratio / PE
        try:
            ratio = fin.ratio(period=period)
            if ratio is not None and not ratio.empty:
                result["data"]["roe_ratio"] = ratio_row_values(ratio, n, "ROE (%)", "ROE", "roe")
                result["data"]["roa_ratio"] = ratio_row_values(ratio, n, "ROA (%)", "ROA", "roa")
                # EPS và BVPS từ ratio table (VND) — dùng cho DCF và P/E
                result["data"]["eps_ratio"]  = ratio_row_values(ratio, n, "EPS (VND)", "EPS", "eps")
                result["data"]["bvps_ratio"] = ratio_row_values(ratio, n, "BVPS (VND)", "BVPS", "bvps")
            else:
                result["data"]["roe_ratio"]  = [0] * n
                result["data"]["roa_ratio"]  = [0] * n
                result["data"]["eps_ratio"]  = [0] * n
                result["data"]["bvps_ratio"] = [0] * n
        except Exception as e:
            result["errors"].append(f"Ratio: {str(e)[:80]}")
            result["data"]["roe_ratio"]  = [0] * n
            result["data"]["roa_ratio"]  = [0] * n
            result["data"]["eps_ratio"]  = [0] * n
            result["data"]["bvps_ratio"] = [0] * n

        # Fallback EPS từ income statement nếu ratio table không có (ví dụ: ngân hàng)
        if all(v == 0 for v in result["data"].get("eps_ratio", [0])):
            try:
                eps_row = find_row(inc, "EPS basic", "Lãi cơ bản trên cổ phiếu", "EPS diluted", "Lãi trên cổ phiếu")
                if eps_row is not None:
                    year_cols_inc = get_year_cols(inc)[-n:]
                    result["data"]["eps_ratio"] = pad5(
                        [round(safe_float_v(row_cell(eps_row, c)), 0) for c in year_cols_inc], n
                    )
            except Exception as e_eps:
                result["errors"].append(f"EPS fallback: {str(e_eps)[:60]}")

        # P/E = Giá / EPS, EPS = LNST_gốc / (Vốn_góp_gốc / 10,000)
        # Dữ liệu gốc từ bal/inc DataFrame đơn vị đồng VNĐ
        pe_list = [0] * n
        try:
            pc_row = find_row(bal, "Paid-in capital", "Vốn góp", "Common shares")
            ln_row = find_row(inc, "Attributable to parent company",
                              "Lợi nhuận của Cổ đông của Công ty mẹ")
            yr_b = get_year_cols(bal)[-n:]
            yr_i = get_year_cols(inc)[-n:]
            if pc_row is not None and ln_row is not None:
                for i in range(min(n, len(yr_b), len(yr_i))):
                    paid = safe_float_v(pc_row.get(yr_b[i], 0))   # đồng gốc
                    lnst = safe_float_v(ln_row.get(yr_i[i], 0))   # đồng gốc
                    shares = paid / 10_000
                    if shares > 100_000 and lnst > 0:
                        eps = lnst / shares  # đồng/CP
                        # Chỉ tính P/E năm cuối vì không có giá lịch sử
                        if i == n - 1:
                            # Giá lấy sau khi fetch overview
                            pe_list[i] = -1  # sentinel: tính sau khi có giá
        except Exception as ep:
            result["errors"].append(f"PE prep: {str(ep)[:60]}")
        result["data"]["pe"] = pe_list
        result["_pe_shares_lnst"] = {}  # store for price section
        try:
            pc_row2 = find_row(bal, "Paid-in capital", "Vốn góp", "Common shares")
            ln_row2 = find_row(inc, "Attributable to parent company",
                               "Lợi nhuận của Cổ đông của Công ty mẹ")
            yr_b2 = get_year_cols(bal)
            yr_i2 = get_year_cols(inc)
            if pc_row2 is not None and ln_row2 is not None and yr_b2 and yr_i2:
                paid2 = safe_float_v(pc_row2.get(yr_b2[-1], 0))
                lnst2 = safe_float_v(ln_row2.get(yr_i2[-1], 0))
                result["_pe_shares_lnst"] = {"paid": paid2, "lnst": lnst2}
        except:
            pass

        # Price
        try:
            from vnstock import Quote as VnQuote
            today = datetime.date.today().strftime("%Y-%m-%d")
            m_ago = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
            quote = VnQuote(symbol=ticker, source="KBS").history(start=m_ago, end=today)
            if quote is not None and not quote.empty:
                last_price = safe_float_v(quote["close"].iloc[-1])
                price_k = round(last_price / 1000, 2) if last_price > 1000 else round(last_price, 2)
                price_dong = last_price if last_price > 1000 else last_price * 1000

                # Tính P/E từ giá thực + paid_in/lnst đã lưu
                pe_data = result.get("_pe_shares_lnst", {})
                paid = pe_data.get("paid", 0)
                lnst = pe_data.get("lnst", 0)
                if paid > 0 and lnst > 0 and price_dong > 0:
                    shares = paid / 10_000
                    eps = lnst / shares
                    pe_calc = round(price_dong / eps, 1)
                    if 1 < pe_calc < 500:
                        pe_list = result["data"].get("pe", [0] * n)
                        pe_list[-1] = pe_calc
                        result["data"]["pe"] = pe_list

                # KL lưu hành: ưu tiên tính từ EPS ratio (đáng tin hơn paid-in capital)
                eps_arr = result["data"].get("eps_ratio", [0]*n)
                lnst_arr2 = result["data"].get("lnst_parent") or result["data"].get("lnst", [0]*n)
                kl_trieu = 0
                # Tìm năm có cả EPS và LNST
                for i in range(n-1, -1, -1):
                    eps_vnd = eps_arr[i] if eps_arr[i] else 0
                    lnst_trieu = lnst_arr2[i] if lnst_arr2[i] else 0
                    if eps_vnd > 0 and lnst_trieu > 0:
                        # shares = LNST(triệu VND) / EPS(VND) = số CP, rồi / 1M = triệu CP
                        kl_trieu = round(lnst_trieu / eps_vnd, 2)
                        break
                # Fallback: paid-in capital
                if kl_trieu == 0:
                    pe_data = result.get("_pe_shares_lnst", {})
                    paid_vnd = pe_data.get("paid", 0)
                    kl_trieu = round(paid_vnd / 10_000 / 1_000_000, 2) if paid_vnd > 0 else 0
                # P/E fallback từ EPS ratio (cho ngân hàng không có paid-in)
                pe_list = result["data"].get("pe", [0] * n)
                eps_arr2 = result["data"].get("eps_ratio", [0] * n)
                if price_dong > 0:
                    for i in range(n):
                        if (pe_list[i] == 0 or pe_list[i] == -1) and eps_arr2[i] and eps_arr2[i] > 0:
                            pe_calc2 = round(price_dong / eps_arr2[i], 1)
                            if 1 < pe_calc2 < 500:
                                pe_list[i] = pe_calc2
                result["data"]["pe"] = pe_list

                # GSSK = VCSH / KL lưu hành (fallback khi không có "Book value" row)
                if all(v == 0 for v in result["data"].get("gssk", [0])) and kl_trieu > 0:
                    vcsh_arr = result["data"].get("vcsh", [0] * n)
                    result["data"]["gssk"] = [
                        round(v / kl_trieu, 2) if v and kl_trieu > 0 else 0
                        for v in vcsh_arr
                    ]

                result["overview"] = {
                    "ten_cty": ticker,
                    "gia_hien_tai": price_k,
                    "gia_so_sach": 0,
                    "kl_luu_hanh": kl_trieu,
                }
            else:
                result["overview"] = {"ten_cty": ticker, "gia_hien_tai": 0, "gia_so_sach": 0, "kl_luu_hanh": 0}
        except Exception as e:
            result["errors"].append(f"Price: {str(e)[:80]}")
            result["overview"] = {"ten_cty": ticker, "gia_hien_tai": 0, "gia_so_sach": 0, "kl_luu_hanh": 0}

        # Tính ROE/ROA mảng đúng (bình quân) để frontend dùng
        try:
            bs = compute_buffett_score(result["data"], n)
            result["data"]["roe_arr"] = bs.get("roe_arr", [])
            result["data"]["roa_arr"] = bs.get("roa_arr", [])
        except Exception:
            pass

        # Xoá key tạm
        result.pop("_pe_shares_lnst", None)

        if not result.get("income_ok") and not result.get("balance_ok"):
            result["ok"] = False
            result["message"] = f"Không tìm thấy dữ liệu cho {ticker}"

    except Exception as e:
        result["ok"] = False
        result["message"] = str(e)

    if result.get("ok"):
        cache_set(cache_key, result)
    return ok(result)


@app.get("/api/buffett-score/{ticker}")
async def buffett_score(ticker: str, n: int = Query(5)):
    """Tính điểm Buffett nhanh cho 1 mã."""
    raw = await fetch_ticker(ticker, yearly="1", n=n)
    body = json.loads(raw.body)
    if not body.get("ok", True):
        raise HTTPException(404, body.get("message", "Không tìm thấy"))
    score_result = compute_buffett_score(body["data"], n)
    return ok({
        "ticker": ticker,
        "price": body.get("overview", {}).get("gia_hien_tai", 0),
        "years": body.get("years", []),
        **score_result,
    })


# ─────────────────────────────────────────────
# ROUTES — MARKET DATA (từ main.py)
# ─────────────────────────────────────────────

@app.get("/api/price-board")
def get_price_board(tickers: str = Query(...)):
    try:
        Trading = get_trading()
        symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()]
        if not symbols:
            raise HTTPException(400, "Cần ít nhất 1 mã")
        df = Trading(source="KBS").price_board(symbols)
        if hasattr(df.columns, "levels"):
            df.columns = ["_".join(str(c) for c in col).strip("_") for col in df.columns]
        df = df.reset_index()
        records = []
        for _, row in df.iterrows():
            r = row.to_dict()
            def find(patterns):
                for k, v in r.items():
                    if any(p in str(k).lower() for p in patterns):
                        return v
                return None
            records.append({
                "ticker":       str(r.get("ticker", r.get("symbol", ""))).upper(),
                "price":        safe_float(find(["close", "match", "price"])),
                "change":       safe_float(find(["change_pct", "change_percent"])),
                "open":         safe_float(find(["open"])),
                "high":         safe_float(find(["high"])),
                "low":          safe_float(find(["low"])),
                "volume":       safe_int(find(["volume", "vol"])),
                "foreign_buy":  safe_int(find(["foreign_buy"])),
                "foreign_sell": safe_int(find(["foreign_sell"])),
            })
        return ok({"data": records, "count": len(records)})
    except HTTPException:
        raise
    except ImportError:
        raise HTTPException(503, "vnstock chưa cài. Chạy: pip install vnstock --upgrade")

@app.get("/api/history/{ticker}")
def get_history(
    ticker: str,
    start: str = Query("2024-01-01"),
    end: Optional[str] = Query(None),
    interval: str = Query("d"),
):
    try:
        Quote = get_quote()
        end_date = end or str(datetime.date.today())
        ticker = ticker.upper()
        df = Quote(symbol=ticker, source="KBS").history(start=start, end=end_date, interval=interval)
        if df is None or df.empty:
            raise HTTPException(404, f"Không có dữ liệu cho {ticker}")
        df = df.reset_index() if df.index.name == "time" else df
        df["time"] = df["time"].astype(str)
        records = df[["time", "open", "high", "low", "close", "volume"]].to_dict(orient="records")
        return ok({"ticker": ticker, "interval": interval, "count": len(records), "data": records})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Lỗi: {e}")


@app.get("/api/technical/{ticker}")
def get_technical(ticker: str, period: int = Query(90)):
    try:
        import pandas as pd
        Quote = get_quote()

        ticker = ticker.upper()
        cache_key = f"technical:{ticker}:{period}"
        cached = cache_get(cache_key)
        if cached:
            log.info(f"[CACHE HIT] {cache_key}")
            return ok(cached)
        end_date = str(datetime.date.today())
        start_date = str(datetime.date.today() - datetime.timedelta(days=period * 2))

        df = Quote(symbol=ticker, source="KBS").history(start=start_date, end=end_date, interval="d")
        if df is None or df.empty:
            raise HTTPException(404, f"Không có dữ liệu cho {ticker}")

        df = df.reset_index() if df.index.name == "time" else df
        df = df.tail(period + 50).copy()
        close = df["close"].astype(float)

        df["ma20"] = close.rolling(20).mean().round(0)
        df["ma50"] = close.rolling(50).mean().round(0)

        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, 1e-9)
        df["rsi"] = (100 - 100 / (1 + rs)).round(2)

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        df["macd"]        = (ema12 - ema26).round(2)
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean().round(2)
        df["macd_hist"]   = (df["macd"] - df["macd_signal"]).round(2)
        df["bb_mid"]      = close.rolling(20).mean().round(0)
        df["bb_upper"]    = (df["bb_mid"] + 2 * close.rolling(20).std()).round(0)
        df["bb_lower"]    = (df["bb_mid"] - 2 * close.rolling(20).std()).round(0)

        df = df.tail(period).copy()
        df["time"] = df["time"].astype(str)
        df = df.where(pd.notnull(df), None)

        latest = df.iloc[-1]
        prev   = df.iloc[-2] if len(df) > 1 else latest

        rsi_val   = safe_float(latest.get("rsi"))
        macd_val  = safe_float(latest.get("macd"))
        macd_sig  = safe_float(latest.get("macd_signal"))
        macd_prev = safe_float(prev.get("macd"))
        macd_sp   = safe_float(prev.get("macd_signal"))
        price     = safe_float(latest.get("close"))
        ma20      = safe_float(latest.get("ma20"))
        ma50      = safe_float(latest.get("ma50"))

        ma20_prev  = safe_float(prev.get("ma20"))
        ma50_prev  = safe_float(prev.get("ma50"))
        bb_upper   = safe_float(latest.get("bb_upper"))
        bb_lower   = safe_float(latest.get("bb_lower"))
        bb_mid     = safe_float(latest.get("bb_mid"))
        vol_cur    = safe_float(latest.get("volume"))
        avg_vol    = float(df["volume"].tail(20).mean()) if "volume" in df.columns else None

        signals = []

        # RSI
        if rsi_val:
            if rsi_val < 30:
                signals.append({"type":"bull","text":f"RSI={rsi_val:.1f} — quá bán, tín hiệu mua"})
            elif rsi_val > 70:
                signals.append({"type":"bear","text":f"RSI={rsi_val:.1f} — quá mua, cảnh báo bán"})
            elif 30 <= rsi_val <= 45:
                signals.append({"type":"neutral","text":f"RSI={rsi_val:.1f} — vùng trung lập, đang hồi phục"})

        # MACD crossover
        if all([macd_val, macd_sig, macd_prev, macd_sp]):
            if macd_val > macd_sig and macd_prev <= macd_sp:
                signals.append({"type":"bull","text":"MACD cắt lên đường Signal — xu hướng tăng"})
            elif macd_val < macd_sig and macd_prev >= macd_sp:
                signals.append({"type":"bear","text":"MACD cắt xuống đường Signal — xu hướng giảm"})
            # MACD dương/âm
            if macd_val and macd_val > 0:
                signals.append({"type":"bull","text":f"MACD={macd_val:.2f} dương — momentum tăng"})
            elif macd_val and macd_val < 0:
                signals.append({"type":"bear","text":f"MACD={macd_val:.2f} âm — momentum giảm"})

        rsi_prev_val = safe_float(prev.get("rsi"))

        # MA alignment (không cần crossover — chỉ cần tương quan hiện tại)
        if all([price, ma20, ma50]):
            if price > ma20 > ma50:
                signals.append({"type":"bull","text":f"Giá ({price:.1f}) > MA20 ({ma20:.1f}) > MA50 ({ma50:.1f}) — uptrend rõ ràng"})
            elif price < ma20 < ma50:
                signals.append({"type":"bear","text":f"Giá ({price:.1f}) < MA20 ({ma20:.1f}) < MA50 ({ma50:.1f}) — downtrend rõ ràng"})
            elif ma20 < ma50:
                signals.append({"type":"bear","text":f"MA20 ({ma20:.1f}) < MA50 ({ma50:.1f}) — xu hướng ngắn hạn yếu hơn dài hạn"})
            elif ma20 > ma50 and price < ma20:
                signals.append({"type":"neutral","text":f"Giá dưới MA20 ({ma20:.1f}) nhưng MA uptrend — điều chỉnh ngắn hạn"})

        # Giá vs MA50
        if all([price, ma50]):
            diff_pct = (price - ma50) / ma50 * 100
            if price > ma50:
                signals.append({"type":"bull","text":f"Giá trên MA50 {diff_pct:+.1f}% — nền tảng trung hạn tích cực"})
            else:
                signals.append({"type":"bear","text":f"Giá dưới MA50 {diff_pct:+.1f}% — áp lực trung hạn"})

        # Golden cross / Death cross
        if all([ma20, ma50, ma20_prev, ma50_prev]):
            if ma20 > ma50 and ma20_prev <= ma50_prev:
                signals.append({"type":"bull","text":"🌟 Golden Cross — MA20 vừa cắt lên MA50 (tín hiệu mua mạnh)"})
            elif ma20 < ma50 and ma20_prev >= ma50_prev:
                signals.append({"type":"bear","text":"💀 Death Cross — MA20 vừa cắt xuống MA50 (tín hiệu bán mạnh)"})

        # RSI direction
        if rsi_val and rsi_prev_val:
            if rsi_val > rsi_prev_val + 2:
                signals.append({"type":"bull","text":f"RSI đang tăng ({rsi_prev_val:.1f}→{rsi_val:.1f}) — momentum đang cải thiện"})
            elif rsi_val < rsi_prev_val - 2:
                signals.append({"type":"bear","text":f"RSI đang giảm ({rsi_prev_val:.1f}→{rsi_val:.1f}) — momentum suy yếu"})

        # Bollinger Bands
        if all([price, bb_upper, bb_lower, bb_mid]):
            bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid else None
            if price >= bb_upper:
                signals.append({"type":"bear","text":f"Giá chạm Bollinger Upper ({bb_upper:.1f}k) — có thể điều chỉnh"})
            elif price <= bb_lower:
                signals.append({"type":"bull","text":f"Giá chạm Bollinger Lower ({bb_lower:.1f}k) — cơ hội mua"})
            elif price > bb_mid:
                signals.append({"type":"bull","text":f"Giá trên BB Mid ({bb_mid:.1f}k) — nửa trên dải Bollinger"})
            else:
                signals.append({"type":"bear","text":f"Giá dưới BB Mid ({bb_mid:.1f}k) — nửa dưới dải Bollinger"})
            if bb_width and bb_width < 0.04:
                signals.append({"type":"neutral","text":"Bollinger Bands co hẹp — sắp có breakout mạnh"})

        # Volume
        if vol_cur and avg_vol and avg_vol > 0:
            ratio = vol_cur / avg_vol
            if ratio >= 2.0:
                signals.append({"type":"neutral","text":f"Volume đột biến {ratio:.1f}x TB20 phiên — chú ý biến động"})
            elif ratio >= 1.3:
                signals.append({"type":"bull","text":f"Volume tăng {ratio:.1f}x TB — tín hiệu tham gia thị trường"})
            elif ratio <= 0.4:
                signals.append({"type":"neutral","text":f"Volume thấp ({ratio:.1f}x TB) — giao dịch thờ ơ"})

        # Verdict tổng hợp
        bull_count = sum(1 for s in signals if s["type"] == "bull")
        bear_count = sum(1 for s in signals if s["type"] == "bear")
        if bull_count > bear_count + 1:
            verdict = {"type":"bull","text":f"NHẬN ĐỊNH: TÍCH CỰC — {bull_count} tín hiệu tăng vs {bear_count} giảm"}
        elif bear_count > bull_count + 1:
            verdict = {"type":"bear","text":f"NHẬN ĐỊNH: TIÊU CỰC — {bear_count} tín hiệu giảm vs {bull_count} tăng"}
        else:
            verdict = {"type":"neutral","text":f"NHẬN ĐỊNH: TRUNG LẬP — thị trường giằng co ({bull_count} tăng / {bear_count} giảm)"}
        signals.insert(0, verdict)

        cols = ["time","open","high","low","close","volume","rsi","macd","macd_signal","macd_hist","ma20","ma50","bb_upper","bb_mid","bb_lower"]
        payload = {
            "ticker": ticker, "period": period,
            "latest": {
                "time": str(latest.get("time")), "close": price,
                "rsi": rsi_val, "macd": macd_val, "macd_signal": macd_sig,
                "macd_hist": safe_float(latest.get("macd_hist")),
                "ma20": safe_float(latest.get("ma20")), "ma50": safe_float(latest.get("ma50")),
                "bb_upper": safe_float(latest.get("bb_upper")), "bb_lower": safe_float(latest.get("bb_lower")),
            },
            "signals": signals,
            "history": df[cols].to_dict(orient="records"),
        }
        cache_set(cache_key, payload)
        return ok(payload)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Lỗi: {e}")


# ─────────────────────────────────────────────
# ROUTES — CLAUDE AI ANALYSIS
# ─────────────────────────────────────────────

@app.post("/api/analyze-claude")
async def analyze_claude(request: Request):
    """Gọi Claude API để phân tích cổ phiếu."""
    import httpx
    body = await request.json()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "Chưa set ANTHROPIC_API_KEY. Chạy: export ANTHROPIC_API_KEY=sk-ant-...")

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1500,
                "system": body.get("system", "") + "\n\nQUAN TRỌNG: JSON ngắn gọn. Mỗi value tối đa 40 ký tự. risks/catalysts tối đa 2 items.",
                "messages": body.get("messages", []),
            },
        )
    data = res.json()
    if not res.is_success:
        raise HTTPException(res.status_code, data.get("error", {}).get("message", "Lỗi Claude API"))
    return ok(data)


@app.get("/api/ai-picks")
async def ai_daily_picks():
    """Lấy daily picks rồi nhờ Claude viết nhận xét tổng hợp."""
    import httpx

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    picks = load_daily_result()

    if not picks or not picks.get("top_picks"):
        return ok({"message": "Chưa có dữ liệu scan. Gọi /api/scan trước.", "commentary": ""})

    top = picks["top_picks"][:5]
    summary = []
    for t in top:
        km = t.get("key_metrics", {})
        summary.append(
            f"{t['ticker']}: score={t['score']}/14, ROE={km.get('roe')}%, "
            f"biên gộp={km.get('gross_margin')}%, PE={km.get('pe')}x, "
            f"CAGR LNTT={t.get('cagr_lntt')}%, giá={t.get('price')} nghìn VNĐ"
        )

    prompt = (
        "Đây là danh sách cổ phiếu top picks theo tiêu chí Warren Buffett hôm nay:\n\n"
        + "\n".join(summary)
        + "\n\nHãy viết nhận xét ngắn gọn (3-5 câu), nêu top 2-3 mã đáng chú ý nhất và lý do. "
        "Dùng tiếng Việt, ngắn gọn, thực tế. Không phải lời khuyên đầu tư chính thức."
    )

    if not api_key:
        return ok({"top_picks": top, "commentary": "(Cần ANTHROPIC_API_KEY để có nhận xét AI)"})

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            res = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        data = res.json()
        commentary = data.get("content", [{}])[0].get("text", "") if res.is_success else ""
    except Exception as e:
        commentary = f"Lỗi AI: {e}"

    return ok({
        "scanned_at": picks.get("scanned_at"),
        "top_picks": top,
        "watchable": picks.get("watchable", [])[:5],
        "commentary": commentary,
    })


@app.get("/api/debug/{ticker}")
def debug_ticker(ticker: str):
    ticker = ticker.upper()
    out = {"ticker": ticker}
    try:
        Finance = get_finance()
        fin = Finance(symbol=ticker, source="VCI")
        inc = fin.income_statement(period="year")
        out["income_rows"] = inc[["item", "item_en"]].to_dict("records") if inc is not None and not inc.empty else []
        bal = fin.balance_sheet(period="year")
        out["balance_rows"] = bal[["item", "item_en"]].to_dict("records") if bal is not None and not bal.empty else []
    except Exception as e:
        out["error"] = str(e)
    return ok(out)


# ─────────────────────────────────────────────
# AUTH — PIN 4 số, lưu users.json
# ─────────────────────────────────────────────
USERS_FILE = "users.json"
SESSIONS: dict = {}  # token -> username (in-memory, reset khi restart)


def load_users() -> dict:
    if USE_DB:
        try:
            rows = db_exec("SELECT username, pin, created_at FROM users", fetch="all")
            return {r["username"]: {"pin": r["pin"], "created_at": str(r["created_at"])} for r in (rows or [])}
        except Exception as e:
            log.warning(f"DB load_users error: {e}")
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}


def save_users(users: dict):
    if USE_DB:
        try:
            for username, info in users.items():
                db_exec("""
                    INSERT INTO users (username, pin) VALUES (%s, %s)
                    ON CONFLICT (username) DO UPDATE SET pin=%s
                """, (username, info["pin"], info["pin"]))
            return
        except Exception as e:
            log.warning(f"DB save_users error: {e}")
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def get_current_user(request: Request) -> str:
    token = request.cookies.get("sa_token") or request.headers.get("X-Token", "")
    user = SESSIONS.get(token)
    if not user:
        raise HTTPException(401, "Chưa đăng nhập")
    return user


@app.post("/api/auth/register")
async def register(body: dict = Body(...)):
    username = body.get("username", "").strip().lower()
    pin = str(body.get("pin", "")).strip()
    if not username or len(username) < 2:
        raise HTTPException(400, "Username tối thiểu 2 ký tự")
    if not pin.isdigit() or len(pin) != 4:
        raise HTTPException(400, "PIN phải là 4 chữ số")
    users = load_users()
    if username in users:
        raise HTTPException(409, "Username đã tồn tại")
    users[username] = {"pin": pin, "created_at": datetime.datetime.now().isoformat()}
    save_users(users)
    return ok({"ok": True, "message": f"Tạo tài khoản '{username}' thành công"})


@app.post("/api/auth/login")
async def login(body: dict = Body(...)):
    username = body.get("username", "").strip().lower()
    pin = str(body.get("pin", "")).strip()
    users = load_users()
    if username not in users or users[username]["pin"] != pin:
        raise HTTPException(401, "Username hoặc PIN không đúng")
    import secrets
    token = secrets.token_hex(24)
    SESSIONS[token] = username
    resp = ok({"ok": True, "username": username, "token": token})
    resp.set_cookie("sa_token", token, httponly=True, samesite="lax", max_age=86400 * 7)
    return resp


@app.post("/api/auth/logout")
async def logout(request: Request):
    token = request.cookies.get("sa_token", "")
    SESSIONS.pop(token, None)
    resp = ok({"ok": True})
    resp.delete_cookie("sa_token")
    return resp


@app.get("/api/auth/me")
async def me(request: Request):
    try:
        user = get_current_user(request)
        return ok({"ok": True, "username": user})
    except HTTPException:
        return ok({"ok": False, "username": None})


# ─────────────────────────────────────────────
# CALCULATOR — lưu lịch sử theo user
# ─────────────────────────────────────────────
CALC_DIR = "calc_data"
os.makedirs(CALC_DIR, exist_ok=True)


def calc_file(username: str) -> str:
    return os.path.join(CALC_DIR, f"{username}.json")


def load_calc_history(username: str) -> list:
    if USE_DB:
        try:
            rows = db_exec(
                "SELECT id, type, data, created_at FROM calc_history WHERE username=%s ORDER BY created_at DESC LIMIT 50",
                (username,), fetch="all"
            )
            result = []
            for r in (rows or []):
                entry = dict(r["data"])
                entry["id"] = r["id"]
                entry["type"] = r["type"]
                entry["saved_at"] = str(r["created_at"])
                result.append(entry)
            return result
        except Exception as e:
            log.warning(f"DB load_calc_history error: {e}")
    fp = calc_file(username)
    if os.path.exists(fp):
        try:
            with open(fp) as f:
                return json.load(f)
        except:
            pass
    return []


def save_calc_entry(username: str, entry: dict):
    entry["saved_at"] = datetime.datetime.now().isoformat()
    entry_id = f"{int(datetime.datetime.now().timestamp()*1000)}"
    entry["id"] = entry_id
    if USE_DB:
        try:
            # Đảm bảo user tồn tại
            db_exec("INSERT INTO users (username, pin) VALUES (%s, '0000') ON CONFLICT DO NOTHING", (username,))
            data_json = json.dumps(entry, cls=SafeEncoder, ensure_ascii=False)
            db_exec(
                "INSERT INTO calc_history (id, username, type, data) VALUES (%s,%s,%s,%s::jsonb)",
                (entry_id, username, entry.get("type", "unknown"), data_json)
            )
            # Giữ tối đa 50
            db_exec("""
                DELETE FROM calc_history WHERE username=%s AND id NOT IN (
                    SELECT id FROM calc_history WHERE username=%s ORDER BY created_at DESC LIMIT 50
                )
            """, (username, username))
            return entry
        except Exception as e:
            log.warning(f"DB save_calc_entry error: {e}")
    history = load_calc_history(username)
    history.insert(0, entry)
    history = history[:50]
    with open(calc_file(username), "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    return entry


@app.post("/api/calc/avg-price")
async def calc_avg_price(request: Request, body: dict = Body(...)):
    """
    Tính trung bình giá.
    Input: { shares_held, avg_price, shares_buy, buy_price, save: bool }
    """
    user = get_current_user(request)

    sh = float(body.get("shares_held", 0))
    ap = float(body.get("avg_price", 0))
    sb = float(body.get("shares_buy", 0))
    bp = float(body.get("buy_price", 0))
    ticker = body.get("ticker", "").upper()

    if sh <= 0 or ap <= 0 or sb <= 0 or bp <= 0:
        raise HTTPException(400, "Vui lòng nhập đầy đủ số liệu hợp lệ")

    total_shares = sh + sb
    total_cost = sh * ap + sb * bp
    new_avg = total_cost / total_shares
    breakeven_pct = ((new_avg - bp) / bp) * 100  # % cần tăng từ giá mua thêm để hòa vốn

    # Bảng mô phỏng: mua thêm 0.5x, 1x, 1.5x, 2x, 3x lô sb
    sim = []
    for mult in [0.5, 1.0, 1.5, 2.0, 3.0]:
        s_extra = sb * mult
        t_shares = sh + s_extra
        t_cost = sh * ap + s_extra * bp
        sim.append({
            "label": f"+{mult}x lô ({int(s_extra):,} CP)",
            "total_shares": round(t_shares),
            "total_cost": round(t_cost),
            "new_avg": round(t_cost / t_shares, 2),
            "breakeven_pct": round(((t_cost / t_shares - bp) / bp) * 100, 1),
        })

    result = {
        "type": "avg_price",
        "ticker": ticker,
        "shares_held": sh,
        "avg_price": ap,
        "shares_buy": sb,
        "buy_price": bp,
        "total_shares": round(total_shares),
        "total_cost": round(total_cost),
        "new_avg": round(new_avg, 2),
        "breakeven_pct": round(breakeven_pct, 1),
        "simulation": sim,
    }

    if body.get("save", False):
        result = save_calc_entry(user, result)

    return ok(result)


@app.post("/api/calc/dao-hang")
async def calc_dao_hang(request: Request, body: dict = Body(...)):
    """
    Tính đảo hàng.
    Input: { shares, buy_price, current_price, rebuy_price, fee_sell, fee_buy, save: bool }
    """
    user = get_current_user(request)

    shares     = float(body.get("shares", 0))
    buy_price  = float(body.get("buy_price", 0))
    cur_price  = float(body.get("current_price", 0))
    rebuy_price= float(body.get("rebuy_price", 0))
    fee_sell   = float(body.get("fee_sell", 0.15)) / 100   # mặc định 0.15%
    fee_buy    = float(body.get("fee_buy",  0.10)) / 100   # mặc định 0.10%
    ticker     = body.get("ticker", "").upper()

    if shares <= 0 or buy_price <= 0 or cur_price <= 0 or rebuy_price <= 0:
        raise HTTPException(400, "Vui lòng nhập đầy đủ số liệu hợp lệ")

    # Tính lỗ khi bán
    sell_value      = shares * cur_price
    sell_fee        = sell_value * fee_sell
    sell_net        = sell_value - sell_fee          # tiền thực nhận
    original_cost   = shares * buy_price
    realized_loss   = original_cost - sell_net       # lỗ thực tế (dương = lỗ)

    # Mua lại: cần bao nhiêu CP để bù lỗ?
    # Tổng tiền bỏ ra khi mua lại = sell_net (tiền nhận được từ bán)
    rebuy_cost_per  = rebuy_price * (1 + fee_buy)
    shares_rebuy    = sell_net / rebuy_cost_per       # số CP mua lại được
    total_rebuy_cost= shares_rebuy * rebuy_cost_per

    # Break-even sau đảo hàng
    breakeven_new   = total_rebuy_cost / shares_rebuy  # = rebuy_price * (1 + fee_buy)

    # So sánh 2 kịch bản
    # Kịch bản 1: giữ nguyên → cần giá tăng về buy_price để hòa
    keep_breakeven_pct = ((buy_price - cur_price) / cur_price) * 100

    # Kịch bản 2: đảo hàng → breakeven thấp hơn
    dao_breakeven_pct  = ((breakeven_new - rebuy_price) / rebuy_price) * 100

    # Lợi thế đảo hàng: giảm được bao nhiêu % break-even
    advantage_pct = ((buy_price - breakeven_new) / buy_price) * 100

    result = {
        "type": "dao_hang",
        "ticker": ticker,
        "shares": shares,
        "buy_price": buy_price,
        "current_price": cur_price,
        "rebuy_price": rebuy_price,
        "fee_sell_pct": fee_sell * 100,
        "fee_buy_pct": fee_buy * 100,
        # Kết quả bán
        "sell_value": round(sell_value),
        "sell_fee": round(sell_fee),
        "sell_net": round(sell_net),
        "realized_loss": round(realized_loss),
        # Kết quả mua lại
        "shares_rebuy": round(shares_rebuy, 0),
        "total_rebuy_cost": round(total_rebuy_cost),
        "breakeven_new": round(breakeven_new, 2),
        # So sánh
        "keep_breakeven_pct": round(keep_breakeven_pct, 1),
        "dao_breakeven_pct": round(dao_breakeven_pct, 1),
        "advantage_pct": round(advantage_pct, 1),
        "is_worth_it": advantage_pct > 5,  # đảo hàng đáng nếu giảm >5% break-even
    }

    if body.get("save", False):
        result = save_calc_entry(user, result)

    return ok(result)


@app.get("/api/calc/history")
async def get_calc_history(request: Request, type: Optional[str] = Query(None)):
    user = get_current_user(request)
    history = load_calc_history(user)
    if type:
        history = [h for h in history if h.get("type") == type]
    return ok({"history": history, "count": len(history)})


@app.delete("/api/calc/history/{entry_id}")
async def delete_calc_entry(entry_id: str, request: Request):
    user = get_current_user(request)
    history = load_calc_history(user)
    history = [h for h in history if h.get("id") != entry_id]
    with open(calc_file(user), "w") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    return ok({"ok": True})


# ─────────────────────────────────────────────
# DCA PORTFOLIO
# ─────────────────────────────────────────────
DCA_FILE = "dca_portfolio.json"

def load_dca(username: str = "default") -> dict:
    if USE_DB:
        try:
            rows = db_exec(
                "SELECT ticker, shares, price, date FROM dca_transactions WHERE username=%s ORDER BY ticker, created_at",
                (username,), fetch="all"
            )
            result = {}
            for r in (rows or []):
                t = r["ticker"]
                result.setdefault(t, []).append({"shares": r["shares"], "price": r["price"], "date": str(r["date"]) if r["date"] else ""})
            return result
        except Exception as e:
            log.warning(f"DB load_dca error: {e}")
    if os.path.exists(DCA_FILE):
        try:
            with open(DCA_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}

def save_dca(data: dict, username: str = "default"):
    if USE_DB:
        try:
            # Xóa toàn bộ transactions cũ của user này
            db_exec("DELETE FROM dca_transactions WHERE username=%s", (username,))
            # Đảm bảo user "default" tồn tại
            db_exec("INSERT INTO users (username, pin) VALUES (%s, '0000') ON CONFLICT DO NOTHING", (username,))
            for ticker, txs in data.items():
                for tx in txs:
                    date = tx.get("date") or None
                    db_exec(
                        "INSERT INTO dca_transactions (username, ticker, shares, price, date) VALUES (%s,%s,%s,%s,%s)",
                        (username, ticker.upper(), float(tx["shares"]), float(tx["price"]), date)
                    )
            return
        except Exception as e:
            log.warning(f"DB save_dca error: {e}")
    with open(DCA_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.get("/api/dca")
def get_dca(request: Request):
    return ok(load_dca(get_username(request)))

@app.post("/api/dca")
async def save_dca_endpoint(request: Request, body: dict = Body(...)):
    """Lưu toàn bộ DCA portfolio. body = { ticker: [{ shares, price, date }] }"""
    save_dca(body, get_username(request))
    return ok({"ok": True})

@app.delete("/api/dca/{ticker}")
def delete_dca_ticker(ticker: str, request: Request):
    u = get_username(request)
    data = load_dca(u)
    data.pop(ticker.upper(), None)
    save_dca(data, u)
    return ok({"ok": True})

@app.get("/api/portfolio/summary")
async def portfolio_summary(request: Request):
    """Tính P&L realtime cho toàn bộ danh mục DCA."""
    u = get_username(request)
    dca = load_dca(u)
    if not dca:
        return ok({"items": [], "total_cost": 0, "total_value": 0, "total_pnl": 0, "total_pnl_pct": 0})

    loop = asyncio.get_event_loop()
    items = []
    total_cost = total_value = 0.0

    async def get_price(ticker: str) -> float:
        ck = f"price:{ticker}"
        cached = cache_get(ck)
        if cached:
            return cached
        try:
            Quote = get_quote()
            today = str(datetime.date.today())
            m_ago = str(datetime.date.today() - datetime.timedelta(days=10))
            df = await loop.run_in_executor(
                None, lambda t=ticker: Quote(symbol=t, source="KBS").history(start=m_ago, end=today)
            )
            price = float(df["close"].iloc[-1]) if df is not None and not df.empty else 0
            cache_set(ck, price)
            return price
        except Exception:
            return 0.0

    for ticker, txns in dca.items():
        cur_price = await get_price(ticker)
        total_shares = sum(float(t["shares"]) for t in txns)
        avg_price    = sum(float(t["shares"]) * float(t["price"]) for t in txns) / total_shares if total_shares else 0
        cost         = total_shares * avg_price
        value        = total_shares * cur_price
        pnl          = value - cost
        pnl_pct      = (pnl / cost * 100) if cost else 0
        total_cost  += cost
        total_value += value
        items.append({
            "ticker": ticker,
            "shares": round(total_shares, 0),
            "avg_price": round(avg_price, 2),
            "cur_price": round(cur_price, 2),
            "cost":   round(cost, 1),
            "value":  round(value, 1),
            "pnl":    round(pnl, 1),
            "pnl_pct": round(pnl_pct, 2),
        })

    items.sort(key=lambda x: abs(x["value"]), reverse=True)
    total_pnl     = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0
    return ok({
        "items":         items,
        "total_cost":    round(total_cost, 1),
        "total_value":   round(total_value, 1),
        "total_pnl":     round(total_pnl, 1),
        "total_pnl_pct": round(total_pnl_pct, 2),
    })


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# COMBINED SIGNAL + RADAR (Redesign V2, Bước 2-3)
# ─────────────────────────────────────────────

def calc_combined_signal(buffett_score: int, ta_data: dict) -> dict:
    """Gộp điểm cơ bản (60%) + kỹ thuật (40%) thành 1 điểm tổng hợp."""
    fundamental = (buffett_score / 14) * 100

    ta_score = 50  # neutral base
    ichi  = ta_data.get("ichimoku", {})
    rsi   = (ta_data.get("latest") or {}).get("rsi", 50) or 50
    vol   = ta_data.get("volume_signal", {})
    lat   = ta_data.get("latest", {}) or {}
    macd_val = lat.get("macd", 0) or 0
    macd_sig = lat.get("macd_signal", 0) or 0

    if ichi.get("signal") == "bullish":        ta_score += 20
    elif ichi.get("signal") == "bearish":      ta_score -= 20
    if ichi.get("tk_cross") == "golden_cross": ta_score += 10
    elif ichi.get("tk_cross") == "dead_cross": ta_score -= 10
    if rsi < 35:    ta_score += 10
    elif rsi > 65:  ta_score -= 10
    if vol.get("signal") == "bullish":   ta_score += 10
    elif vol.get("signal") == "bearish": ta_score -= 15
    if macd_val > macd_sig: ta_score += 5
    else:                    ta_score -= 5
    ta_score = max(0, min(100, ta_score))

    combined = fundamental * 0.6 + ta_score * 0.4
    if combined >= 70:   action, color = "XEM",    "green"
    elif combined >= 50: action, color = "CHỜ",    "yellow"
    else:                action, color = "TRÁNH",  "red"

    ichi_label = {"bullish": "Trên mây", "bearish": "Dưới mây"}.get(
        ichi.get("signal", ""), "Trong mây")

    return {
        "combined":    round(combined),
        "fundamental": round(fundamental),
        "ta_score":    round(ta_score),
        "action":      action,
        "color":       color,
        "ichi_label":  ichi_label,
    }

def compute_technical_sync(ticker: str, period: int = 90) -> dict:
    """Tính technical indicators đồng bộ (dùng trong radar)."""
    try:
        import pandas as pd
        Quote = get_quote()
        ticker = ticker.upper()
        cache_key = f"technical:{ticker}:{period}"
        cached = cache_get(cache_key)
        if cached:
            return cached
        end_date   = str(datetime.date.today())
        start_date = str(datetime.date.today() - datetime.timedelta(days=period * 2))
        df = Quote(symbol=ticker, source="KBS").history(start=start_date, end=end_date, interval="d")
        if df is None or df.empty:
            return {}
        df = df.reset_index() if df.index.name == "time" else df
        df = df.tail(period + 50).copy()
        close = df["close"].astype(float)

        # Ichimoku
        tenkan = (df["high"].rolling(9).max()  + df["low"].rolling(9).min())  / 2
        kijun  = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
        sa     = ((tenkan + kijun) / 2).shift(26)
        sb     = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)
        cloud_top    = max(safe_float(sa.iloc[-1]) or 0, safe_float(sb.iloc[-1]) or 0)
        cloud_bottom = min(safe_float(sa.iloc[-1]) or 0, safe_float(sb.iloc[-1]) or 0)
        price = float(close.iloc[-1])
        ichi_signal = "bullish" if price > cloud_top else "bearish" if price < cloud_bottom else "neutral"
        tk_cross = None
        if len(tenkan) > 1 and len(kijun) > 1:
            if tenkan.iloc[-2] < kijun.iloc[-2] and tenkan.iloc[-1] > kijun.iloc[-1]:
                tk_cross = "golden_cross"
            elif tenkan.iloc[-2] > kijun.iloc[-2] and tenkan.iloc[-1] < kijun.iloc[-1]:
                tk_cross = "dead_cross"

        # RSI
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, 1e-9)
        rsi   = float((100 - 100 / (1 + rs)).iloc[-1])

        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_val = float((ema12 - ema26).iloc[-1])
        macd_sig = float((ema12 - ema26).ewm(span=9, adjust=False).mean().iloc[-1])

        # Volume signal
        vol_ma20 = float(df["volume"].rolling(20).mean().iloc[-1]) if "volume" in df.columns else 0
        vol_last = float(df["volume"].iloc[-1]) if "volume" in df.columns else 0
        vol_ratio = vol_last / vol_ma20 if vol_ma20 > 0 else 1
        prev_close = float(close.iloc[-2]) if len(close) > 1 else price
        change_pct = (price - prev_close) / prev_close * 100 if prev_close else 0
        vol_signal = "neutral"
        vol_warning = None
        if change_pct >= 6.5 and vol_ratio > 3:
            vol_signal = "bearish"; vol_warning = f"⚠️ TRẦN + VOL đột biến {vol_ratio:.1f}x TB20"
        elif change_pct >= 6.5 and vol_ratio < 1:
            vol_signal = "bullish"; vol_warning = f"✅ Trần + vol thấp ({vol_ratio:.1f}x TB20)"
        elif change_pct <= -6.5 and vol_ratio > 2:
            vol_signal = "bearish"; vol_warning = f"⚠️ SÀN + VOL lớn {vol_ratio:.1f}x TB20"

        result = {
            "latest": {"close": price, "rsi": round(rsi,2), "macd": round(macd_val,2), "macd_signal": round(macd_sig,2)},
            "ichimoku": {"signal": ichi_signal, "tk_cross": tk_cross,
                         "cloud_top": round(cloud_top,2), "cloud_bottom": round(cloud_bottom,2)},
            "volume_signal": {"signal": vol_signal, "warning": vol_warning,
                              "vol_ratio": round(vol_ratio,2), "change_pct": round(change_pct,2)},
        }
        cache_set(cache_key, result)
        return result
    except Exception as e:
        log.warning(f"compute_technical_sync {ticker}: {e}")
        return {}

@app.get("/api/radar")
async def get_radar(request: Request):
    """Trả về watchlist với điểm tổng hợp cơ bản + kỹ thuật."""
    u = get_username(request)
    watchlist = load_watchlist(u)
    if not watchlist:
        return ok({"results": [], "updated_at": datetime.datetime.now().isoformat()})

    loop = asyncio.get_event_loop()
    sem  = asyncio.Semaphore(3)

    async def _process(ticker: str):
        async with sem:
            try:
                scan_r  = await loop.run_in_executor(None, scan_one_ticker, ticker)
                ta_data = await loop.run_in_executor(None, compute_technical_sync, ticker)
                signal  = calc_combined_signal(scan_r.get("score", 0), ta_data)
                return {
                    "ticker":         ticker,
                    "signal":         signal,
                    "buffett_score":  scan_r.get("score", 0),
                    "price":          (ta_data.get("latest") or {}).get("close"),
                    "volume_warning": (ta_data.get("volume_signal") or {}).get("warning"),
                    "key_metrics":    scan_r.get("key_metrics", {}),
                }
            except Exception as e:
                log.warning(f"Radar {ticker}: {e}")
                return {"ticker": ticker, "signal": {"combined":0,"action":"TRÁNH","color":"red","ichi_label":"—"}, "buffett_score":0, "price":None, "volume_warning":None, "key_metrics":{}}

    results = list(await asyncio.gather(*[_process(t) for t in watchlist]))
    results.sort(key=lambda x: x["signal"]["combined"], reverse=True)
    return ok({"results": results, "updated_at": datetime.datetime.now().isoformat()})


# ROUTES — SO SÁNH NGÀNH (Phase 4.3)
# ─────────────────────────────────────────────

SECTOR_MAP: dict = {
    # Công nghệ
    "FPT": ("Công nghệ", ["FPT","CMG","VGI","ELC","ICT"]),
    "CMG": ("Công nghệ", ["FPT","CMG","VGI","ELC","ICT"]),
    "VGI": ("Công nghệ", ["FPT","CMG","VGI","ELC","ICT"]),
    # Ngân hàng
    "VCB": ("Ngân hàng", ["VCB","BID","CTG","TCB","MBB","ACB","VPB","HDB"]),
    "BID": ("Ngân hàng", ["VCB","BID","CTG","TCB","MBB","ACB","VPB","HDB"]),
    "CTG": ("Ngân hàng", ["VCB","BID","CTG","TCB","MBB","ACB","VPB","HDB"]),
    "TCB": ("Ngân hàng", ["VCB","BID","CTG","TCB","MBB","ACB","VPB","HDB"]),
    "MBB": ("Ngân hàng", ["VCB","BID","CTG","TCB","MBB","ACB","VPB","HDB"]),
    "ACB": ("Ngân hàng", ["VCB","BID","CTG","TCB","MBB","ACB","VPB","HDB"]),
    "VPB": ("Ngân hàng", ["VCB","BID","CTG","TCB","MBB","ACB","VPB","HDB"]),
    # Bất động sản
    "VHM": ("Bất động sản", ["VHM","VIC","NVL","PDR","KDH","DXG","BCM"]),
    "VIC": ("Bất động sản", ["VHM","VIC","NVL","PDR","KDH","DXG","BCM"]),
    "NVL": ("Bất động sản", ["VHM","VIC","NVL","PDR","KDH","DXG","BCM"]),
    "PDR": ("Bất động sản", ["VHM","VIC","NVL","PDR","KDH","DXG","BCM"]),
    "KDH": ("Bất động sản", ["VHM","VIC","NVL","PDR","KDH","DXG","BCM"]),
    # Thép
    "HPG": ("Thép", ["HPG","HSG","NKG","TLH"]),
    "HSG": ("Thép", ["HPG","HSG","NKG","TLH"]),
    # Bán lẻ
    "MWG": ("Bán lẻ", ["MWG","PNJ","FRT","DGW"]),
    "PNJ": ("Bán lẻ", ["MWG","PNJ","FRT","DGW"]),
    "FRT": ("Bán lẻ", ["MWG","PNJ","FRT","DGW"]),
    # Dầu khí
    "GAS": ("Dầu khí", ["GAS","PVD","PVS","BSR","PLX"]),
    "PVD": ("Dầu khí", ["GAS","PVD","PVS","BSR","PLX"]),
    "PVS": ("Dầu khí", ["GAS","PVD","PVS","BSR","PLX"]),
    # Thực phẩm
    "VNM": ("Thực phẩm", ["VNM","SAB","MSN","MCH","QNS"]),
    "SAB": ("Thực phẩm", ["VNM","SAB","MSN","MCH","QNS"]),
    "MSN": ("Thực phẩm", ["VNM","SAB","MSN","MCH","QNS"]),
    # Xây dựng
    "HBC": ("Xây dựng vật liệu", ["HBC","CTD","FCN","VCG","BMP","CSV"]),
    "CTD": ("Xây dựng vật liệu", ["HBC","CTD","FCN","VCG","BMP","CSV"]),
    "BMP": ("Xây dựng vật liệu", ["HBC","CTD","FCN","VCG","BMP","CSV"]),
    # Điện
    "REE": ("Điện", ["REE","PC1","GEG","EVF","SBA","HND"]),
    "PC1": ("Điện", ["REE","PC1","GEG","EVF","SBA","HND"]),
}

@app.get("/api/sector/{ticker}")
async def sector_compare(ticker: str):
    """So sánh mã với các mã cùng ngành."""
    ticker = ticker.upper()
    if ticker not in SECTOR_MAP:
        return ok({"sector": "Không xác định", "peers": [], "target": ticker})

    sector_name, peers = SECTOR_MAP[ticker]
    # Lấy tối đa 4 mã khác (không bao gồm chính nó) + chính nó
    compare_list = [ticker] + [p for p in peers if p != ticker][:4]

    loop = asyncio.get_event_loop()

    async def get_peer_data(t: str) -> dict:
        ck = f"scan:{t}"
        cached = cache_get(ck)
        if cached:
            return cached
        try:
            r = await loop.run_in_executor(None, scan_one_ticker, t)
            cache_set(ck, r)
            return r
        except Exception:
            return {"ticker": t, "score": 0, "key_metrics": {}}

    results = await asyncio.gather(*[get_peer_data(t) for t in compare_list])
    peers_out = []
    for r in results:
        km = r.get("key_metrics", {})
        peers_out.append({
            "ticker":       r.get("ticker", ""),
            "score":        r.get("score", 0),
            "roe":          km.get("roe"),
            "pe":           km.get("pe"),
            "gross_margin": km.get("gross_margin"),
            "debt_ratio":   km.get("debt_ratio"),
            "price":        r.get("price", 0),
            "is_target":    r.get("ticker","") == ticker,
        })
    peers_out.sort(key=lambda x: x["score"], reverse=True)
    return ok({"sector": sector_name, "target": ticker, "peers": peers_out})


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# ROUTES — PORTFOLIO MODULE (Redesign V2, Bước 1)
# ─────────────────────────────────────────────

def _port_row(row) -> dict:
    return {
        "id":           row["id"],
        "ticker":       row["ticker"],
        "shares":       row["shares"],
        "avg_price":    row["avg_price"],
        "bought_at":    str(row["bought_at"]) if row["bought_at"] else None,
        "note":         row["note"] or "",
        "target_price": row["target_price"],
        "stop_loss":    row["stop_loss"],
        "created_at":   row["created_at"].isoformat() if row["created_at"] else None,
    }

async def _enrich_position(pos: dict, loop) -> dict:
    """Thêm giá hiện tại + P&L + cảnh báo vào 1 position."""
    t = pos["ticker"]
    cur = cache_get(f"price:{t}") or 0
    if not cur:
        try:
            Quote = get_quote()
            today  = str(datetime.date.today())
            m_ago  = str(datetime.date.today() - datetime.timedelta(days=10))
            df = await loop.run_in_executor(
                None, lambda tk=t: Quote(symbol=tk, source="KBS").history(start=m_ago, end=today)
            )
            if df is not None and not df.empty:
                cur = float(df["close"].iloc[-1])
                cache_set(f"price:{t}", cur)
        except Exception:
            pass

    cost  = pos["shares"] * pos["avg_price"]
    value = pos["shares"] * cur if cur else 0
    pnl   = value - cost
    pnl_pct = (pnl / cost * 100) if cost else 0

    # % danh mục tạm — sẽ tính lại ở summary
    warnings = []
    if cur:
        if pos.get("stop_loss") and cur <= pos["stop_loss"]:
            warnings.append({"level":"critical","msg":f"🔴 Thủng Stop Loss {pos['stop_loss']}k!"})
        if pos.get("target_price") and cur >= pos["target_price"] * 0.97:
            warnings.append({"level":"info","msg":f"🎯 Đạt ~97% mục tiêu {pos['target_price']}k — cân nhắc chốt"})
        if pos.get("stop_loss") and cur <= pos["stop_loss"] * 1.03:
            if not any(w["level"]=="critical" for w in warnings):
                warnings.append({"level":"warning","msg":f"⚠️ Đang tiếp cận Stop Loss {pos['stop_loss']}k"})

    return {**pos, "cur_price": round(cur, 2),
            "cost": round(cost,1), "value": round(value,1),
            "pnl": round(pnl,1), "pnl_pct": round(pnl_pct,2),
            "warnings": warnings}

@app.get("/api/portfolio")
async def get_portfolio(request: Request):
    u = get_username(request)
    loop = asyncio.get_event_loop()
    if USE_DB:
        rows = db_exec("SELECT * FROM portfolio WHERE username=%s ORDER BY created_at DESC", (u,), fetch="all") or []
        positions = [_port_row(r) for r in rows]
    else:
        positions = []
    enriched = await asyncio.gather(*[_enrich_position(p, loop) for p in positions])
    return ok({"positions": list(enriched)})

@app.get("/api/portfolio/summary")
async def get_portfolio_summary(request: Request):
    u = get_username(request)
    loop = asyncio.get_event_loop()
    if USE_DB:
        rows = db_exec("SELECT * FROM portfolio WHERE username=%s ORDER BY created_at DESC", (u,), fetch="all") or []
        positions = [_port_row(r) for r in rows]
    else:
        positions = []
    if not positions:
        return ok({"positions":[], "total_cost":0, "total_value":0, "total_pnl":0, "total_pnl_pct":0})
    enriched = list(await asyncio.gather(*[_enrich_position(p, loop) for p in positions]))
    total_cost  = sum(p["cost"]  for p in enriched)
    total_value = sum(p["value"] for p in enriched)
    total_pnl   = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0
    # Tính % danh mục
    for p in enriched:
        p["weight"] = round(p["value"] / total_value * 100, 1) if total_value else 0
    enriched.sort(key=lambda x: abs(x["value"]), reverse=True)
    return ok({
        "positions":     enriched,
        "total_cost":    round(total_cost,1),
        "total_value":   round(total_value,1),
        "total_pnl":     round(total_pnl,1),
        "total_pnl_pct": round(total_pnl_pct,2),
    })

@app.post("/api/portfolio")
async def add_position(request: Request, body: dict = Body(...)):
    u = get_username(request)
    pid = str(int(datetime.datetime.now().timestamp() * 1000))
    ticker = body.get("ticker","").upper().strip()
    shares = float(body.get("shares", 0))
    avg_price = float(body.get("avg_price", 0))
    if not ticker or shares <= 0 or avg_price <= 0:
        raise HTTPException(400, "Thiếu ticker / shares / avg_price")
    bought_at    = body.get("bought_at") or str(datetime.date.today())
    note         = body.get("note","")
    target_price = float(body.get("target_price")) if body.get("target_price") else None
    stop_loss    = float(body.get("stop_loss"))    if body.get("stop_loss")    else None
    if USE_DB:
        db_exec(
            "INSERT INTO portfolio (id,username,ticker,shares,avg_price,bought_at,note,target_price,stop_loss) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (pid, u, ticker, shares, avg_price, bought_at, note, target_price, stop_loss)
        )
    return ok({"id": pid, "ticker": ticker})

@app.put("/api/portfolio/{pid}")
async def update_position(pid: str, request: Request, body: dict = Body(...)):
    u = get_username(request)
    if USE_DB:
        fields, vals = [], []
        for col in ["shares","avg_price","note","target_price","stop_loss","bought_at"]:
            if col in body:
                fields.append(f"{col}=%s")
                vals.append(body[col])
        if fields:
            fields.append("updated_at=NOW()")
            vals += [pid, u]
            db_exec(f"UPDATE portfolio SET {','.join(fields)} WHERE id=%s AND username=%s", vals)
    return ok({"updated": pid})

@app.delete("/api/portfolio/{pid}")
def delete_position(pid: str, request: Request):
    u = get_username(request)
    if USE_DB:
        db_exec("DELETE FROM portfolio WHERE id=%s AND username=%s", (pid, u))
    return ok({"deleted": pid})


# ROUTES — AI ALERT SYSTEM (Phase 3)
# ─────────────────────────────────────────────

def _alert_row_to_dict(row) -> dict:
    return {
        "id":           row["id"],
        "ticker":       row["ticker"],
        "type":         row["type"],
        "value":        row["value"],
        "active":       row["active"],
        "triggered_at": row["triggered_at"].isoformat() if row["triggered_at"] else None,
        "last_checked": row["last_checked"].isoformat() if row["last_checked"] else None,
        "created_at":   row["created_at"].isoformat() if row["created_at"] else None,
    }

@app.post("/api/alerts")
async def create_alert(request: Request, body: dict = Body(...)):
    u = get_username(request)
    alert_id = str(int(datetime.datetime.now().timestamp() * 1000))
    ticker = body.get("ticker", "").upper().strip()
    atype  = body.get("type", "")
    value  = float(body.get("value", 0))
    VALID_TYPES = {"price_above","price_below","rsi_oversold","rsi_overbought","score_change"}
    if not ticker or atype not in VALID_TYPES:
        raise HTTPException(400, "Thiếu ticker hoặc type không hợp lệ")
    if USE_DB:
        db_exec(
            "INSERT INTO alerts (id,username,ticker,type,value) VALUES (%s,%s,%s,%s,%s)",
            (alert_id, u, ticker, atype, value)
        )
    return ok({"id": alert_id, "ticker": ticker, "type": atype, "value": value})

@app.get("/api/alerts")
def get_alerts(request: Request):
    u = get_username(request)
    if USE_DB:
        rows = db_exec("SELECT * FROM alerts WHERE username=%s ORDER BY created_at DESC", (u,), fetch="all")
        return ok({"alerts": [_alert_row_to_dict(r) for r in (rows or [])]})
    return ok({"alerts": []})

@app.delete("/api/alerts/{alert_id}")
def delete_alert(alert_id: str, request: Request):
    u = get_username(request)
    if USE_DB:
        db_exec("DELETE FROM alerts WHERE id=%s AND username=%s", (alert_id, u))
    return ok({"deleted": alert_id})

@app.get("/api/alerts/triggered")
def get_triggered_alerts(request: Request):
    u = get_username(request)
    if USE_DB:
        rows = db_exec(
            "SELECT * FROM alerts WHERE username=%s AND triggered_at IS NOT NULL AND active=TRUE ORDER BY triggered_at DESC",
            (u,), fetch="all"
        )
        return ok({"triggered": [_alert_row_to_dict(r) for r in (rows or [])]})
    return ok({"triggered": []})

@app.post("/api/alerts/{alert_id}/reset")
def reset_alert(alert_id: str, request: Request):
    u = get_username(request)
    if USE_DB:
        db_exec(
            "UPDATE alerts SET triggered_at=NULL, active=TRUE, last_checked=NULL WHERE id=%s AND username=%s",
            (alert_id, u)
        )
    return ok({"reset": alert_id})

# ── Alert checker — chạy mỗi 15 phút, 9:00–15:30 VN (UTC+7)
async def _check_all_alerts():
    """Kiểm tra tất cả active alerts, trigger nếu điều kiện thoả."""
    if not USE_DB:
        return
    try:
        import pytz
        tz_vn = pytz.timezone("Asia/Ho_Chi_Minh")
        now_vn = datetime.datetime.now(tz_vn)
        # Chỉ chạy trong giờ giao dịch
        if not (9 <= now_vn.hour < 16):
            return
        rows = db_exec(
            "SELECT * FROM alerts WHERE active=TRUE AND triggered_at IS NULL",
            fetch="all"
        ) or []
        if not rows:
            return
        # Gom tickers cần check giá
        tickers = list({r["ticker"] for r in rows})
        prices, rsis = {}, {}
        loop = asyncio.get_event_loop()
        for t in tickers:
            try:
                Quote = get_quote()
                today = str(datetime.date.today())
                m_ago = str(datetime.date.today() - datetime.timedelta(days=30))
                df = await loop.run_in_executor(
                    None, lambda: Quote(symbol=t, source="KBS").history(start=m_ago, end=today)
                )
                if df is not None and not df.empty:
                    prices[t] = float(df["close"].iloc[-1])
                    close = df["close"].astype(float)
                    delta = close.diff()
                    gain = delta.clip(lower=0).rolling(14).mean()
                    loss = (-delta.clip(upper=0)).rolling(14).mean()
                    rs = gain / loss.replace(0, 1e-9)
                    rsis[t] = float((100 - 100 / (1 + rs)).iloc[-1])
            except Exception:
                pass
        for row in rows:
            t = row["ticker"]
            atype = row["type"]
            val = float(row["value"])
            price = prices.get(t)
            rsi   = rsis.get(t)
            triggered = False
            if atype == "price_above"    and price and price >= val: triggered = True
            if atype == "price_below"    and price and price <= val: triggered = True
            if atype == "rsi_oversold"   and rsi   and rsi  <= val: triggered = True
            if atype == "rsi_overbought" and rsi   and rsi  >= val: triggered = True
            db_exec(
                "UPDATE alerts SET last_checked=%s" + (", triggered_at=%s" if triggered else "") + " WHERE id=%s",
                (datetime.datetime.utcnow(), datetime.datetime.utcnow(), row["id"]) if triggered
                else (datetime.datetime.utcnow(), row["id"])
            )
            if triggered:
                log.info(f"🔔 Alert triggered: {row['username']} {t} {atype} val={val} price={price} rsi={rsi}")
    except Exception as e:
        log.error(f"Alert checker error: {e}")

if __name__ == "__main__":
    import uvicorn
    print("\n" + "=" * 55)
    print("  📈 STOCK AGENT AI — FastAPI Edition")
    print("=" * 55)
    print("  🌐 Dashboard : http://localhost:8000")
    print("  📋 API Docs  : http://localhost:8000/docs")
    print("  ⏹  Ctrl+C    : dừng")
    print("=" * 55 + "\n")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
