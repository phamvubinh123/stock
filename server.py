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
# WATCHLIST CONFIG — sửa thoải mái
# ─────────────────────────────────────────────
DEFAULT_WATCHLIST = ["FPT", "VCB"]

# File lưu watchlist & daily results
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
def load_watchlist() -> List[str]:
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE) as f:
                return json.load(f)
        except:
            pass
    return list(DEFAULT_WATCHLIST)


def save_watchlist(symbols: List[str]):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump([s.upper().strip() for s in symbols], f)


def load_daily_result():
    if os.path.exists(DAILY_RESULT_FILE):
        try:
            with open(DAILY_RESULT_FILE) as f:
                return json.load(f)
        except:
            pass
    return None


def save_daily_result(data):
    with open(DAILY_RESULT_FILE, "w") as f:
        json.dump(data, cls=SafeEncoder, fp=f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# VNSTOCK HELPERS
# ─────────────────────────────────────────────
def get_vnstock():
    """Import Vnstock — ưu tiên vnstock3, fallback vnstock v4 nếu vnstock3 lỗi import."""
    for mod in ("vnstock3", "vnstock"):
        try:
            return getattr(__import__(mod), "Vnstock")
        except (ImportError, ModuleNotFoundError):
            continue
    raise HTTPException(503, "Chưa cài vnstock. Chạy: pip install vnstock3 --upgrade")


def get_trading():
    try:
        from vnstock3 import Trading
        return Trading
    except ImportError:
        from vnstock import Trading
        return Trading


def get_quote():
    try:
        from vnstock3 import Quote
        return Quote
    except ImportError:
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
async def scan_one_ticker(ticker: str) -> dict:
    """Scan 1 mã, trả về dict tóm tắt."""
    try:
        Vnstock = get_vnstock()
        stock = Vnstock().stock(symbol=ticker, source="VCI")
        fin = stock.finance
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
                data["vcsh"] = gvb("Owner's Equity", "Vốn chủ sở hữu", "Vốn và các quỹ", "Capital and reserves")
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


async def run_daily_scan(watchlist: Optional[List[str]] = None) -> dict:
    """Scan toàn bộ watchlist, sắp xếp theo score."""
    symbols = watchlist or load_watchlist()
    log.info(f"Daily scan bắt đầu — {len(symbols)} mã: {symbols}")

    results = []
    for ticker in symbols:
        r = await scan_one_ticker(ticker)
        results.append(r)
        await asyncio.sleep(0.5)  # rate limit friendly

    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Top picks: score >= 9
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

    save_daily_result(output)
    log.info(f"Daily scan xong — {len(top_picks)} top picks, {len(watchable)} cần theo dõi")
    return output


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────
scheduler_task: Optional[asyncio.Task] = None


async def scheduler_loop():
    """Chạy scan lúc 7:30 sáng mỗi ngày (giờ VN)."""
    import pytz
    vn_tz = pytz.timezone("Asia/Ho_Chi_Minh")
    log.info("Scheduler started — daily scan lúc 7:30 sáng giờ VN")

    while True:
        now = datetime.datetime.now(vn_tz)
        target = now.replace(hour=7, minute=30, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)

        wait_secs = (target - now).total_seconds()
        log.info(f"Scan tiếp theo lúc {target.strftime('%Y-%m-%d %H:%M')} VN ({wait_secs/3600:.1f}h nữa)")
        await asyncio.sleep(wait_secs)
        await run_daily_scan()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler_task
    scheduler_task = asyncio.create_task(scheduler_loop())
    log.info("🚀 Stock Agent AI started")
    yield
    if scheduler_task:
        scheduler_task.cancel()
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


# ─────────────────────────────────────────────
# ROUTES — WATCHLIST
# ─────────────────────────────────────────────

@app.get("/api/watchlist")
def get_watchlist():
    return ok({"watchlist": load_watchlist()})


@app.post("/api/watchlist")
async def set_watchlist(body: dict = Body(...)):
    symbols = [s.upper().strip() for s in body.get("symbols", []) if s.strip()]
    if not symbols:
        raise HTTPException(400, "Cần ít nhất 1 mã")
    save_watchlist(symbols)
    return ok({"watchlist": symbols, "count": len(symbols)})


@app.post("/api/watchlist/add")
async def add_to_watchlist(body: dict = Body(...)):
    ticker = body.get("ticker", "").upper().strip()
    if not ticker:
        raise HTTPException(400, "Thiếu ticker")
    wl = load_watchlist()
    if ticker not in wl:
        wl.append(ticker)
        save_watchlist(wl)
    return ok({"watchlist": wl})


@app.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str):
    ticker = ticker.upper()
    wl = [s for s in load_watchlist() if s != ticker]
    save_watchlist(wl)
    return ok({"watchlist": wl})


# ─────────────────────────────────────────────
# ROUTES — DAILY SCAN & PICKS
# ─────────────────────────────────────────────

@app.get("/api/daily-picks")
def get_daily_picks():
    result = load_daily_result()
    if result:
        return ok(result)
    return ok({"message": "Chưa có kết quả. Gọi /api/scan để chạy ngay.", "top_picks": [], "watchable": [], "avoid": []})


@app.post("/api/scan")
async def trigger_scan(body: dict = Body(default={})):
    """Trigger scan thủ công. Có thể pass watchlist riêng."""
    custom = body.get("watchlist")
    result = await run_daily_scan(custom)
    return ok(result)


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
    Vnstock = get_vnstock()

    result = {
        "ticker": ticker, "ok": True, "errors": [],
        "data": {}, "years": [], "is_bank": False,
    }

    try:
        stock = Vnstock().stock(symbol=ticker, source="VCI")
        fin = stock.finance

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

            # VCSH — lấy trực tiếp để tính ROE chính xác
            # vnstock3: "Owner's Equity" hoặc "Vốn chủ sở hữu"
            result["data"]["vcsh"] = gvb(
                "Owner's Equity", "Vốn chủ sở hữu",
                "Vốn và các quỹ", "Capital and reserves"
            )

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
        raise HTTPException(503, "vnstock3 chưa cài. Chạy: pip install vnstock3 --upgrade")
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

        signals = []
        if rsi_val and rsi_val < 30:   signals.append("RSI quá bán — tín hiệu mua")
        elif rsi_val and rsi_val > 70: signals.append("RSI quá mua — cảnh báo")
        if all([macd_val, macd_sig, macd_prev, macd_sp]):
            if macd_val > macd_sig and macd_prev <= macd_sp:
                signals.append("MACD cắt lên — bullish")
            elif macd_val < macd_sig and macd_prev >= macd_sp:
                signals.append("MACD cắt xuống — bearish")
        if all([price, ma20, ma50]) and price > ma20 > ma50:
            signals.append("Giá trên MA20 và MA50 — xu hướng tăng")

        cols = ["time","close","volume","rsi","macd","macd_signal","macd_hist","ma20","ma50","bb_upper","bb_mid","bb_lower"]
        return ok({
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
        })
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
        Vnstock = get_vnstock()
        stock = Vnstock().stock(symbol=ticker, source="VCI")
        inc = stock.finance.income_statement(period="year")
        out["income_rows"] = inc[["item", "item_en"]].to_dict("records") if inc is not None and not inc.empty else []
        bal = stock.finance.balance_sheet(period="year")
        out["balance_rows"] = bal[["item", "item_en"]].to_dict("records") if bal is not None and not bal.empty else []
    except Exception as e:
        out["error"] = str(e)
    return ok(out)


if __name__ == "__main__":
    import uvicorn
    print("\n" + "=" * 55)
    print("  📈 STOCK AGENT AI — FastAPI Edition")
    print("=" * 55)
    print("  🌐 Dashboard : http://localhost:8000")
    print("  📋 API Docs  : http://localhost:8000/docs")
    print("  ⏰ Auto scan : 7:30 sáng mỗi ngày (giờ VN)")
    print("  ⏹  Ctrl+C    : dừng")
    print("=" * 55 + "\n")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
