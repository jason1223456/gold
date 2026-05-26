#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue May 12 20:49:02 2026

@author: chenguanting
"""

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Stable Gold AI Trader
功能：
- XAU/USD Smart Money 掃描
- A+ / A / A- / B+ 分級，最低 7 分才通知
- A+ 連發 3 次 + 震動；A 震動；A- / B+ 靜音
- Telegram 指令：/open /close /status /stats /report
- Supabase 記錄平倉交易與勝率統計
- 每 2 小時透過 OpenAI 產生市場報告並推送 Telegram
- TwelveData 免費版穩定低頻設定

安裝：
pip install requests pandas openai ta

重要：正式部署請改用環境變數，不要把 Key 寫死在程式裡。
"""

import os
import time
import json
import requests
import pandas as pd

from openai import OpenAI
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator
from ta.volatility import AverageTrueRange
import base64


def decode_base64(encoded_text):

    return base64.b64decode(
        encoded_text
    ).decode("utf-8")


# =====================================================
# CONFIG
# =====================================================

# =====================================================
# BASE64 KEYS
# =====================================================

# TwelveData
TWELVEDATA_API_KEY_BASE64 = (
    "OWM1OWMyODcyYjI4NGE4ZDg3M2VlOTMxMzlmMTFkNDM="
)

# OpenAI
OPENAI_API_KEY_BASE64 = (
    "c2stcHJvai1UalRfVmFXWjBxZmxoeWJuTTdEQmE4RWpiV2xfaEFwWDJ2LVc3VWRZVWQtU0FJcm14ZFVZUGxHMjA2a05mbzZLSnVWQnVFTkphQ1QzQmxia0ZKZnBPSGdneHg5cjZzeGxJS2NQZDFnZHBSQjZSWWV5em8tNzh0UTdRa0RpazBoSTZibVpFeFpKSTRBdHBXS1NXS3p0TDN2X0RZQUE="
)

# Telegram Bot Token
TELEGRAM_BOT_TOKEN_BASE64 = (
    "ODY2MTAwNDYzOTpBQUdJWVA3UmV3WjhnVlNtOTNEWEVtNkRaLWdia1AzOHRYaw=="
)

# Telegram Chat ID
TELEGRAM_CHAT_ID_BASE64 = (
    "NjkwMTcxMzIxNg=="
)

# Supabase URL
SUPABASE_URL_BASE64 = (
    "aHR0cHM6Ly9vaWF3ZW9reXFjZWRmYnl4dXZtei5zdXBhYmFzZS5jbw=="
)

# Supabase Key
SUPABASE_KEY_BASE64 = (
    "c2JfcHVibGlzaGFibGVfUXBsNzYtOWFDTzFJLWFVYlJMUl9pZ193cDJDUGl6TA=="
)
TWELVEDATA_API_KEY = decode_base64(
    TWELVEDATA_API_KEY_BASE64
)

OPENAI_API_KEY = decode_base64(
    OPENAI_API_KEY_BASE64
)

TELEGRAM_BOT_TOKEN = decode_base64(
    TELEGRAM_BOT_TOKEN_BASE64
)

TELEGRAM_CHAT_ID = decode_base64(
    TELEGRAM_CHAT_ID_BASE64
)

SUPABASE_URL = decode_base64(
    SUPABASE_URL_BASE64
)

SUPABASE_KEY = decode_base64(
    SUPABASE_KEY_BASE64
)

SYMBOL = "XAU/USD"
STATE_FILE = "trade_state.json"

# 免費版穩定設定
SCAN_INTERVAL = 300          # 5分鐘掃描一次訊號
MONITOR_INTERVAL = 60        # 1分鐘監控一次持倉
M5_STRUCTURE_INTERVAL = 300  # 5分鐘檢查一次 M5 結構
REPORT_INTERVAL = 7200       # 2小時產生一次 OpenAI 市場報告

DATA_CACHE = {}

client = OpenAI(api_key=OPENAI_API_KEY)

# =====================================================
# TELEGRAM
# =====================================================

def send_telegram(text, important=False, repeat=1):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # A+：強提醒 + 連發三次
    if "A+" in text:
        text = f"""
🚨🚨🚨 A+級黃金訊號 🚨🚨🚨

{text}
"""
        important = True
        repeat = 3

    # A：強提醒，但不要誤判 A-
    elif "Grade：A" in text and "Grade：A-" not in text and "A+" not in text:
        text = f"""
🔥 高品質黃金訊號 🔥

{text}
"""
        important = True

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        # False = 有聲音/震動；True = 靜音
        "disable_notification": not important
    }

    for _ in range(repeat):
        try:
            res = requests.post(url, json=payload, timeout=15)
            print("Telegram:", res.status_code)
            time.sleep(1)
        except Exception as e:
            print("Telegram Error:", e)


def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout": 0}

    if offset:
        params["offset"] = offset

    try:
        res = requests.get(url, params=params, timeout=10)
        return res.json()
    except Exception as e:
        print("Update Error:", e)
        return {"ok": False, "result": []}

# =====================================================
# STATE
# =====================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "last_signal": None,
            "last_signal_id": None,
            "active_trade": None,
            "telegram_offset": None,
            "notified": {},
            "last_m5_check": 0,
            "last_report_time": 0
        }

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

    # 兼容舊 state
    state.setdefault("last_signal", None)
    state.setdefault("last_signal_id", None)
    state.setdefault("active_trade", None)
    state.setdefault("telegram_offset", None)
    state.setdefault("notified", {})
    state.setdefault("last_m5_check", 0)
    state.setdefault("last_report_time", 0)

    return state


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# =====================================================
# SUPABASE
# =====================================================

def save_trade_to_supabase(trade, exit_price, pnl_points, result):
    url = f"{SUPABASE_URL}/rest/v1/trades"

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    data = {
        "side": trade["side"],
        "entry_price": trade["entry"],
        "exit_price": exit_price,
        "lot": trade["lot"],
        "pnl_points": round(pnl_points, 2),
        "result": result,
        "sl": trade["sl"],
        "tp1": trade["tp1"],
        "tp2": trade["tp2"],
        "signal_score": trade.get("score", 0),
        "setup_type": trade.get("grade", "manual")
    }

    try:
        res = requests.post(url, headers=headers, json=data, timeout=15)
        print("Supabase:", res.status_code)
        print(res.text)
    except Exception as e:
        print("Supabase Error:", e)


def get_trade_stats():
    url = f"{SUPABASE_URL}/rest/v1/trades"

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }

    try:
        res = requests.get(
            url,
            headers=headers,
            params={"select": "*"},
            timeout=15
        )

        trades = res.json()

        if not trades:
            return "目前沒有交易紀錄"

        df = pd.DataFrame(trades)

        total = len(df)
        wins = len(df[df["result"] == "WIN"])
        losses = len(df[df["result"] == "LOSS"])
        win_rate = wins / total * 100

        total_points = df["pnl_points"].sum()
        avg_points = df["pnl_points"].mean()
        best = df["pnl_points"].max()
        worst = df["pnl_points"].min()

        grade_text = ""
        if "setup_type" in df.columns:
            grade_lines = []
            for grade, group in df.groupby("setup_type"):
                g_total = len(group)
                g_wins = len(group[group["result"] == "WIN"])
                g_win_rate = g_wins / g_total * 100 if g_total else 0
                g_points = group["pnl_points"].sum()
                grade_lines.append(
                    f"{grade}：{g_total}筆｜勝率 {round(g_win_rate, 2)}%｜點數 {round(g_points, 2)}"
                )
            grade_text = "\n".join(grade_lines)

        return f"""
📊 交易統計

總交易：{total}
勝：{wins}
敗：{losses}
勝率：{round(win_rate, 2)}%

總點數：{round(total_points, 2)}
平均每單：{round(avg_points, 2)}

最佳單：{round(best, 2)}
最差單：{round(worst, 2)}

--- 分級統計 ---
{grade_text}
"""

    except Exception as e:
        return f"Stats Error: {e}"

# =====================================================
# DATA
# =====================================================

def get_data(interval):
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={SYMBOL}"
        f"&interval={interval}"
        f"&outputsize=500"
        f"&apikey={TWELVEDATA_API_KEY}"
    )

    try:
        data = requests.get(url, timeout=20).json()
    except Exception as e:
        print(f"{interval} request error:", e)
        return None

    if "values" not in data:
        print(f"{interval} 抓取失敗")
        print(data)
        return None

    df = pd.DataFrame(data["values"]).iloc[::-1].reset_index(drop=True)

    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)

    df["RSI"] = RSIIndicator(close=df["close"], window=14).rsi()
    df["EMA20"] = EMAIndicator(close=df["close"], window=20).ema_indicator()
    df["EMA50"] = EMAIndicator(close=df["close"], window=50).ema_indicator()
    df["ATR"] = AverageTrueRange(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=14
    ).average_true_range()

    return df


def get_data_cached(interval, cache_seconds=60):
    now = time.time()

    if interval in DATA_CACHE:
        cached_time, cached_df = DATA_CACHE[interval]
        if now - cached_time < cache_seconds:
            return cached_df

    df = get_data(interval)

    if df is not None:
        DATA_CACHE[interval] = (now, df)

    return df


def get_current_price():
    df = get_data_cached("1min", 300)

    if df is None:
        return None

    return float(df.iloc[-1]["close"])

# =====================================================
# SMART MONEY
# =====================================================

def find_swings(df, lookback=3):
    swing_highs = []
    swing_lows = []

    highs = df["high"].values
    lows = df["low"].values

    for i in range(lookback, len(df) - lookback):
        if highs[i] > max(highs[i-lookback:i]) and highs[i] > max(highs[i+1:i+lookback+1]):
            swing_highs.append((i, highs[i]))

        if lows[i] < min(lows[i-lookback:i]) and lows[i] < min(lows[i+1:i+lookback+1]):
            swing_lows.append((i, lows[i]))

    return swing_highs, swing_lows


def market_structure(df):
    highs, lows = find_swings(df)

    if len(highs) < 2 or len(lows) < 2:
        return "SIDEWAYS"

    last_high = highs[-1][1]
    prev_high = highs[-2][1]
    last_low = lows[-1][1]
    prev_low = lows[-2][1]

    if last_high > prev_high and last_low > prev_low:
        return "BULL"

    if last_high < prev_high and last_low < prev_low:
        return "BEAR"

    return "SIDEWAYS"


def detect_bos(df):
    highs, lows = find_swings(df)

    if len(highs) < 1 or len(lows) < 1:
        return None

    current = df.iloc[-1]["close"]
    last_high = highs[-1][1]
    last_low = lows[-1][1]

    if current > last_high:
        return "BOS_UP"

    if current < last_low:
        return "BOS_DOWN"

    return None


def detect_choch(df):
    structure = market_structure(df)
    bos = detect_bos(df)

    if structure == "BEAR" and bos == "BOS_UP":
        return "BULL_CHOCH"

    if structure == "BULL" and bos == "BOS_DOWN":
        return "BEAR_CHOCH"

    return None


def trend(df):
    last = df.iloc[-1]

    close = last["close"]
    ema20 = last["EMA20"]
    ema50 = last["EMA50"]
    rsi = last["RSI"]
    structure = market_structure(df)

    if close > ema20 > ema50 and rsi > 55 and structure == "BULL":
        return "BULL"

    if close < ema20 < ema50 and rsi < 45 and structure == "BEAR":
        return "BEAR"

    return "SIDEWAYS"


def liquidity_sweep(df):
    highs, lows = find_swings(df)

    if len(highs) < 1 or len(lows) < 1:
        return None

    last = df.iloc[-1]
    last_swing_high = highs[-1][1]
    last_swing_low = lows[-1][1]

    if last["high"] > last_swing_high and last["close"] < last_swing_high:
        return "SWEEP_HIGH"

    if last["low"] < last_swing_low and last["close"] > last_swing_low:
        return "SWEEP_LOW"

    return None


def detect_order_block(df):
    recent = df.tail(20).reset_index(drop=True)

    bullish_ob = None
    bearish_ob = None

    for i in range(len(recent) - 1):
        candle = recent.iloc[i]
        next_candle = recent.iloc[i + 1]

        if candle["close"] < candle["open"] and next_candle["close"] > candle["high"]:
            bullish_ob = candle["low"]

        if candle["close"] > candle["open"] and next_candle["close"] < candle["low"]:
            bearish_ob = candle["high"]

    return bullish_ob, bearish_ob


def detect_fvg(df):
    fvg_zones = []

    for i in range(2, len(df)):
        candle1 = df.iloc[i - 2]
        candle3 = df.iloc[i]

        if candle1["high"] < candle3["low"]:
            fvg_zones.append({
                "type": "BULLISH",
                "low": candle1["high"],
                "high": candle3["low"]
            })

        if candle1["low"] > candle3["high"]:
            fvg_zones.append({
                "type": "BEARISH",
                "low": candle3["high"],
                "high": candle1["low"]
            })

    return fvg_zones


def high_volatility(df):
    atr = df.iloc[-1]["ATR"]
    avg_atr = df["ATR"].tail(50).mean()
    return atr > avg_atr * 1.8


def valid_session():
    now = pd.Timestamp.utcnow()
    hour = now.hour
    return 7 <= hour <= 21

# =====================================================
# GRADE + SIGNAL
# =====================================================

def get_grade(score):
    if score >= 12:
        return "A+"
    elif score >= 10:
        return "A"
    elif score >= 8:
        return "A-"
    elif score >= 7:
        return "B+"
    else:
        return None


# 這裡放完整 Stable Gold AI Trader 主程式
# 由於你的原始程式超長，下面提供的是你目前需要直接覆蓋的重要完整區塊。

# =====================================================
# ATR / RSI FUNCTIONS
# =====================================================


def calculate_atr(df, period=14):

    atr = AverageTrueRange(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=period
    ).average_true_range()

    return atr.iloc[-1]



def calculate_rsi(df, period=14):

    rsi = RSIIndicator(
        close=df["close"],
        window=period
    ).rsi()

    return rsi.iloc[-1]


# =====================================================
# CONTINUATION SETTINGS
# =====================================================

LAST_CONTINUATION_SIGNAL = {
    "BUY": 0,
    "SELL": 0
}

CONTINUATION_COOLDOWN = 2700


# =====================================================
# SMART MONEY SIGNAL
# =====================================================


def smart_money_signal(
    h1,
    m30,
    m15,
    m5,
    m1
):

    global LAST_CONTINUATION_SIGNAL

    import time

    CONTINUATION_MAX_DISTANCE = 12
    CONTINUATION_MIN_ATR = 5
    TP_BUFFER = 3

    now = time.time()

    trend_h1 = trend(h1)

    structure_m30 = market_structure(m30)

    bos_m15 = detect_bos(m15)

    choch_m5 = detect_choch(m5)

    sweep = liquidity_sweep(m5)

    bullish_ob, bearish_ob = detect_order_block(m15)

    fvg = detect_fvg(m15)

    price = m1.iloc[-1]["close"]

    ema20 = m15["close"].ewm(span=20).mean().iloc[-1]

    atr = calculate_atr(m15, 14)

    rsi = calculate_rsi(m5, 14)

    distance_from_ema = abs(price - ema20)

    over_extended = (
        distance_from_ema
        > CONTINUATION_MAX_DISTANCE
    )

    # =====================================================
    # 避免追高追低
    # =====================================================

    recent_m1 = m1.tail(6)

    red_count = sum(
        recent_m1["close"] < recent_m1["open"]
    )

    green_count = sum(
        recent_m1["close"] > recent_m1["open"]
    )

    too_many_red = red_count >= 5
    too_many_green = green_count >= 5

    # =====================================================
    # 大位移過濾
    # =====================================================

    m30_last = m30.iloc[-1]

    body_size = abs(
        m30_last["close"]
        - m30_last["open"]
    )

    atr_m30 = calculate_atr(m30, 14)

    strong_displacement = (
        body_size > atr_m30 * 1.5
    )

    # =====================================================
    # BUY SCORE
    # =====================================================

    score_buy = 0
    reasons_buy = []

    if trend_h1 == "BULL":
        score_buy += 2
        reasons_buy.append("H1偏多")

    if structure_m30 == "BULL":
        score_buy += 2
        reasons_buy.append("M30 HH/HL")

    if bos_m15 == "BOS_UP":
        score_buy += 2
        reasons_buy.append("M15 BOS UP")

    if choch_m5 == "BULL_CHOCH":
        score_buy += 3
        reasons_buy.append("M5 CHOCH翻多")

    if sweep == "SWEEP_LOW":
        score_buy += 3
        reasons_buy.append("掃低流動性")

    if bullish_ob and price <= bullish_ob + 5:
        score_buy += 2
        reasons_buy.append("Bullish OB")

    bullish_fvg_found = False

    for zone in fvg:
        if (
            zone["type"] == "BULLISH"
            and zone["low"] <= price <= zone["high"]
        ):
            bullish_fvg_found = True
            break

    if bullish_fvg_found:
        score_buy += 2
        reasons_buy.append("Bullish FVG")

    # =====================================================
    # SELL SCORE
    # =====================================================

    score_sell = 0
    reasons_sell = []

    if trend_h1 == "BEAR":
        score_sell += 2
        reasons_sell.append("H1偏空")

    if structure_m30 == "BEAR":
        score_sell += 2
        reasons_sell.append("M30 LH/LL")

    if bos_m15 == "BOS_DOWN":
        score_sell += 2
        reasons_sell.append("M15 BOS DOWN")

    if choch_m5 == "BEAR_CHOCH":
        score_sell += 3
        reasons_sell.append("M5 CHOCH翻空")

    if sweep == "SWEEP_HIGH":
        score_sell += 3
        reasons_sell.append("掃高流動性")

    if bearish_ob and price >= bearish_ob - 5:
        score_sell += 2
        reasons_sell.append("Bearish OB")

    bearish_fvg_found = False

    for zone in fvg:
        if (
            zone["type"] == "BEARISH"
            and zone["low"] <= price <= zone["high"]
        ):
            bearish_fvg_found = True
            break

    if bearish_fvg_found:
        score_sell += 2
        reasons_sell.append("Bearish FVG")

    # =====================================================
    # CONTINUATION
    # =====================================================

    continuation_sell = (
        trend_h1 == "BEAR"
        and structure_m30 == "BEAR"
        and bos_m15 == "BOS_DOWN"
        and choch_m5 == "BEAR_CHOCH"
        and atr >= CONTINUATION_MIN_ATR
        and 35 < rsi < 55
        and not over_extended
        and not too_many_red
    )

    continuation_buy = (
        trend_h1 == "BULL"
        and structure_m30 == "BULL"
        and bos_m15 == "BOS_UP"
        and choch_m5 == "BULL_CHOCH"
        and atr >= CONTINUATION_MIN_ATR
        and 45 < rsi < 65
        and not over_extended
        and not too_many_green
    )

    # =====================================================
    # REVERSAL FILTER
    # =====================================================

    valid_buy_reversal = (
        (
            sweep == "SWEEP_LOW"
            or choch_m5 == "BULL_CHOCH"
        )
        and not strong_displacement
    )

    valid_sell_reversal = (
        (
            sweep == "SWEEP_HIGH"
            or choch_m5 == "BEAR_CHOCH"
        )
        and not strong_displacement
    )

    # =====================================================
    # COOLDOWN
    # =====================================================

    if continuation_sell:

        elapsed = (
            now
            - LAST_CONTINUATION_SIGNAL["SELL"]
        )

        if elapsed < CONTINUATION_COOLDOWN:
            continuation_sell = False

    if continuation_buy:

        elapsed = (
            now
            - LAST_CONTINUATION_SIGNAL["BUY"]
        )

        if elapsed < CONTINUATION_COOLDOWN:
            continuation_buy = False

    # =====================================================
    # GRADE
    # =====================================================

    buy_grade = get_grade(score_buy)
    sell_grade = get_grade(score_sell)

    if continuation_buy:
        buy_grade = "A-"

    if continuation_sell:
        sell_grade = "A-"

    # =====================================================
    # BUY SIGNAL
    # =====================================================

    if buy_grade:

        signal_mode = "REVERSAL"

        if continuation_buy:
            signal_mode = "CONTINUATION"

        else:
            if not valid_buy_reversal:
                buy_grade = None

        if buy_grade:

            if continuation_buy:
                LAST_CONTINUATION_SIGNAL["BUY"] = now

            return {
                "type": "BUY",
                "mode": signal_mode,
                "grade": buy_grade,
                "score": score_buy,
                "entry": round(price, 2),
                "sl": round(price - 8, 2),
                "tp1": round(price + 15, 2),
                "tp2": round(price + 30, 2),
                "tp_buffer": round(price + 15 - TP_BUFFER, 2),
                "reasons": reasons_buy
            }

    # =====================================================
    # SELL SIGNAL
    # =====================================================

    if sell_grade:

        signal_mode = "REVERSAL"

        if continuation_sell:
            signal_mode = "CONTINUATION"

        else:
            if not valid_sell_reversal:
                sell_grade = None

        if sell_grade:

            if continuation_sell:
                LAST_CONTINUATION_SIGNAL["SELL"] = now

            return {
                "type": "SELL",
                "mode": signal_mode,
                "grade": sell_grade,
                "score": score_sell,
                "entry": round(price, 2),
                "sl": round(price + 8, 2),
                "tp1": round(price - 15, 2),
                "tp2": round(price - 30, 2),
                "tp_buffer": round(price - 15 + TP_BUFFER, 2),
                "reasons": reasons_sell
            }

    return {
        "type": "NO_SIGNAL"
    }

# =====================================================
# GPT
# =====================================================

def analyze_trade_with_ai(signal):
    prompt = f"""
你是專業黃金 Smart Money 交易分析師。

Direction: {signal['type']}
Grade: {signal['grade']}
Score: {signal['score']}
Entry: {signal['entry']}
SL: {signal['sl']}
TP1: {signal['tp1']}
TP2: {signal['tp2']}
Reasons: {signal['reasons']}

請簡短分析：
1. 值不值得做
2. 是否追價
3. 風險提醒
"""

    response = client.chat.completions.create(
        model="gpt-5",
        messages=[
            {"role": "system", "content": "你是黃金交易分析師"},
            {"role": "user", "content": prompt}
        ]
    )

    return response.choices[0].message.content


def generate_2h_report():
    m1 = get_data_cached("1min", 60)
    m5 = get_data_cached("5min", 600)
    m15 = get_data_cached("15min", 900)
    m30 = get_data_cached("30min", 1800)
    h1 = get_data_cached("1h", 3600)

    if any(df is None for df in [m1, m5, m15, m30, h1]):
        return "⚠️ 兩小時報告：資料不足，暫時無法產生。"

    current_price = m1.iloc[-1]["close"]
    signal = smart_money_signal(h1, m30, m15, m5, m1)

    context = {
        "price": round(current_price, 2),
        "h1_trend": trend(h1),
        "m30_structure": market_structure(m30),
        "m15_bos": detect_bos(m15),
        "m5_choch": detect_choch(m5),
        "m5_sweep": liquidity_sweep(m5),
        "signal": signal,
        "m1_rsi": round(m1.iloc[-1]["RSI"], 2),
        "m5_rsi": round(m5.iloc[-1]["RSI"], 2),
        "m15_atr": round(m15.iloc[-1]["ATR"], 2),
    }

    prompt = f"""
你是專業 XAUUSD 黃金 Smart Money 交易分析師。

請根據以下資料產生 2 小時市場報告：
{context}

請用繁體中文，格式如下：
1. 目前市場狀態
2. 多空方向
3. 是否適合交易
4. 關鍵支撐/壓力或風險區
5. 接下來 2 小時應該觀察什麼
6. 若目前沒有好機會，請明確說不要硬做

請簡短、實用、像 Telegram 通知。
"""

    try:
        response = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": "你是黃金交易風控分析師，只提供輔助判斷，不保證獲利。"},
                {"role": "user", "content": prompt}
            ]
        )

        return f"""
🕒 XAUUSD 兩小時 AI 市場報告

{response.choices[0].message.content}
"""

    except Exception as e:
        return f"⚠️ 兩小時報告產生失敗：{e}"

# =====================================================
# SCAN
# =====================================================

def scan_market():
    print("掃描市場中...")

    m1 = get_data_cached("1min", 60)
    m5 = get_data_cached("5min", 600)
    m15 = get_data_cached("15min", 900)
    m30 = get_data_cached("30min", 1800)
    h1 = get_data_cached("1h", 3600)

    if any(df is None for df in [m1, m5, m15, m30, h1]):
        return None

    return smart_money_signal(h1, m30, m15, m5, m1)

# =====================================================
# TRADE / COMMANDS
# =====================================================

def calculate_pnl_points(trade, price):
    if trade["side"] == "BUY":
        return price - trade["entry"]
    return trade["entry"] - price


def handle_telegram_commands(state):

    updates = get_updates(
        state.get("telegram_offset")
    )

    if not updates.get("ok"):
        return state

    for item in updates.get("result", []):

        state["telegram_offset"] = (
            item["update_id"] + 1
        )

        text = (
            item.get("message", {})
            .get("text", "")
            .strip()
        )

        if not text:
            continue

        parts = text.split()

        command = parts[0].lower()

        # =====================================
        # OPEN
        # =====================================

        if command == "/open":

            if len(parts) < 4:

                send_telegram(
                    "格式：/open BUY 4685 0.05"
                )

                continue

            side = parts[1].upper()

            entry = float(parts[2])

            lot = float(parts[3])

            if side == "BUY":

                sl = entry - 8
                tp1 = entry + 15
                tp2 = entry + 30

            else:

                sl = entry + 8
                tp1 = entry - 15
                tp2 = entry - 30

            last_signal = state.get(
                "last_signal"
            )

            grade = "manual"

            score = 0

            if (
                last_signal
                and last_signal.get("type") == side
            ):

                grade = last_signal.get(
                    "grade",
                    "manual"
                )

                score = last_signal.get(
                    "score",
                    0
                )

            state["active_trade"] = {

                "side": side,

                "entry": entry,

                "lot": lot,

                "sl": sl,

                "tp1": tp1,

                "tp2": tp2,

                "grade": grade,

                "score": score,

                "tp1_done": False,

                "tp2_done": False,

                "created_at":
                str(pd.Timestamp.utcnow())
            }

            state["notified"] = {}

            save_state(state)

            send_telegram(f"""
✅ 已記錄持倉

方向：{side}

Entry：{entry}

Lot：{lot}

SL：{sl}

TP1：{tp1}

TP2：{tp2}

系統開始監控。
""")

        # =====================================
        # CLOSE
        # =====================================

        elif command == "/close":

            trade = state.get(
                "active_trade"
            )

            if not trade:

                send_telegram(
                    "目前沒有持倉"
                )

                continue

            if len(parts) >= 2:

                exit_price = float(parts[1])

            else:

                price = get_current_price()

                if price is None:

                    send_telegram(
                        "抓不到目前價格"
                    )

                    continue

                exit_price = price

            pnl_points = calculate_pnl_points(
                trade,
                exit_price
            )

            result = (
                "WIN"
                if pnl_points > 0
                else "LOSS"
            )

            save_trade_to_supabase(
                trade,
                exit_price,
                pnl_points,
                result
            )

            send_telegram(f"""
✅ 已平倉並記錄

方向：{trade['side']}

Entry：{trade['entry']}

Exit：{exit_price}

Lot：{trade['lot']}

PnL：{round(pnl_points, 2)}

結果：{result}
""")

            state["active_trade"] = None

            state["notified"] = {}

            save_state(state)

        # =====================================
        # STATUS
        # =====================================

        elif command == "/status":

            trade = state.get(
                "active_trade"
            )

            if not trade:

                send_telegram(
                    "目前沒有持倉"
                )

                continue

            price = get_current_price()

            pnl = calculate_pnl_points(
                trade,
                price
            )

            send_telegram(f"""
📊 持倉狀態

方向：{trade['side']}

Entry：{trade['entry']}

目前價格：{price}

浮動點數：{round(pnl, 2)}

SL：{trade['sl']}

TP1：{trade['tp1']}

TP2：{trade['tp2']}
""")

        # =====================================
        # STATS
        # =====================================

        elif command == "/stats":

            send_telegram(
                get_trade_stats()
            )

        # =====================================
        # REPORT
        # =====================================

        elif command == "/report":

            send_telegram(
                generate_2h_report(),
                important=True
            )

        # =====================================
        # DEBUG
        # =====================================

        elif command == "/debug":

            m1 = get_data_cached(
                "1min",
                60
            )

            m5 = get_data_cached(
                "5min",
                600
            )

            m15 = get_data_cached(
                "15min",
                900
            )

            m30 = get_data_cached(
                "30min",
                1800
            )

            h1 = get_data_cached(
                "1h",
                3600
            )

            if any(
                df is None
                for df in [m1, m5, m15, m30, h1]
            ):

                send_telegram(
                    "❌ 資料不足"
                )

                continue

            h1_trend = trend(h1)

            m30_structure = market_structure(
                m30
            )

            m15_bos = detect_bos(m15)

            m5_choch = detect_choch(m5)

            m5_sweep = liquidity_sweep(m5)

            current_price = m1.iloc[-1]["close"]

            current_rsi = round(
                m5.iloc[-1]["RSI"],
                2
            )

            signal = smart_money_signal(
                h1,
                m30,
                m15,
                m5,
                m1
            )

            send_telegram(f"""
📊 DEBUG REPORT

Price：
{current_price}

H1 Trend：
{h1_trend}

M30 Structure：
{m30_structure}

M15 BOS：
{m15_bos}

M5 CHOCH：
{m5_choch}

M5 Sweep：
{m5_sweep}

M5 RSI：
{current_rsi}

Signal：
{signal}
""")

    save_state(state)

    return state
# =====================================================
# MONITOR
# =====================================================

# =====================================================
# TP1 MANAGEMENT SYSTEM
# =====================================================


def monitor_trade(state):

    trade = state.get("active_trade")

    if not trade:
        return state

    price = get_current_price()

    if price is None:
        return state

    side = trade["side"]
    sl = trade["sl"]
    tp1 = trade["tp1"]
    tp2 = trade["tp2"]

    notified = state.get("notified", {})

    pnl_points = calculate_pnl_points(
        trade,
        price
    )

    print(
        "Monitor:",
        side,
        price,
        "PNL:",
        pnl_points
    )

    TP_BUFFER = 3

    # =====================================
    # BUY / SELL CONDITIONS
    # =====================================

    if side == "BUY":

        distance_to_sl = price - sl

        hit_sl = price <= sl

        near_sl = (
            0 < distance_to_sl <= 3
        )

        hit_tp1 = (
            price >= tp1 - TP_BUFFER
        )

        hit_tp2 = (
            price >= tp2
        )

    else:

        distance_to_sl = sl - price

        hit_sl = price >= sl

        near_sl = (
            0 < distance_to_sl <= 3
        )

        hit_tp1 = (
            price <= tp1 + TP_BUFFER
        )

        hit_tp2 = (
            price <= tp2
        )

    # =====================================
    # NEAR SL WARNING
    # =====================================

    if (
        near_sl
        and not notified.get("near_sl")
    ):

        send_telegram(f"""
⚠️ 接近止損

方向：{side}
目前價格：{price}
SL：{sl}

建議：
❌ 不要攤平
❌ 不要加碼
✅ 確認是否減倉
""", important=True)

        notified["near_sl"] = True

    # =====================================
    # HIT SL
    # =====================================

    if (
        hit_sl
        and not notified.get("hit_sl")
    ):

        send_telegram(f"""
🛑 已觸及止損區

方向：{side}
目前價格：{price}
SL：{sl}

建議：
⚠️ 此單失效
⚠️ 避免繼續硬扛
""", important=True)

        notified["hit_sl"] = True

    # =====================================
    # TP1 MANAGEMENT
    # =====================================

    if (
        hit_tp1
        and not trade.get("tp1_done")
    ):

        # =========================
        # 自動移保本
        # =========================

        trade["sl"] = trade["entry"]

        send_telegram(f"""
💰 接近 TP1

方向：{side}
目前價格：{price}
TP1：{tp1}

建議：
✅ 平倉 50%
✅ 已自動移動 SL 至保本
✅ 剩餘觀察 TP2
""", important=True)

        send_telegram(f"""
🛡️ 保本 SL 已啟動

新 SL：
{trade['sl']}
""", important=True)

        # =========================
        # continuation 檢查
        # =========================

        m5 = get_data_cached(
            "5min",
            600
        )

        if m5 is not None:

            choch = detect_choch(m5)

            bos = detect_bos(m5)

            # SELL continuation

            if (
                side == "SELL"
                and choch == "BEAR_CHOCH"
                and bos == "BOS_DOWN"
            ):

                send_telegram(f"""
🔥 SELL continuation 延續

目前價格：{price}

✅ 結構仍偏空
✅ 剩餘倉位可續抱 TP2
✅ SL 已保本
""", important=True)

            # BUY continuation

            if (
                side == "BUY"
                and choch == "BULL_CHOCH"
                and bos == "BOS_UP"
            ):

                send_telegram(f"""
🔥 BUY continuation 延續

目前價格：{price}

✅ 結構仍偏多
✅ 剩餘倉位可續抱 TP2
✅ SL 已保本
""", important=True)

        trade["tp1_done"] = True

    # =====================================
    # TP2
    # =====================================

    if (
        hit_tp2
        and not trade.get("tp2_done")
    ):

        send_telegram(f"""
🏁 已到達 TP2

方向：{side}
目前價格：{price}
TP2：{tp2}

建議：
✅ 可全部出場
✅ 或保留極小倉位
""", important=True)

        trade["tp2_done"] = True

    # =====================================
    # M5 STRUCTURE CHECK
    # =====================================

    now = time.time()

    last_m5_check = state.get(
        "last_m5_check",
        0
    )

    if (
        now - last_m5_check
        >= M5_STRUCTURE_INTERVAL
    ):

        m5 = get_data_cached(
            "5min",
            600
        )

        if m5 is not None:

            choch = detect_choch(m5)

            # BUY 結構轉弱

            if (
                side == "BUY"
                and choch == "BEAR_CHOCH"
                and not notified.get("choch")
            ):

                send_telegram(f"""
⚠️ BUY 結構轉弱

M5 出現 BEAR CHOCH

建議：
✅ 減倉
✅ 保本
✅ 降低風險
""", important=True)

                notified["choch"] = True

            # SELL 結構轉強

            if (
                side == "SELL"
                and choch == "BULL_CHOCH"
                and not notified.get("choch")
            ):

                send_telegram(f"""
⚠️ SELL 結構轉強

M5 出現 BULL CHOCH

建議：
✅ 減倉
✅ 保本
✅ 降低風險
""", important=True)

                notified["choch"] = True

        state["last_m5_check"] = now

    # =====================================
    # SAVE
    # =====================================

    state["active_trade"] = trade

    state["notified"] = notified

    save_state(state)

    return state
# =====================================================
# MAIN
# =====================================================

def main():
    send_telegram("✅ Gold AI Trader 已啟動（穩定版 + 2H AI報告）")

    state = load_state()
    last_scan_time = 0

    GRADE_RANK = {
        "B+": 1,
        "A-": 2,
        "A": 3,
        "A+": 4
    }

    while True:
        try:
            state = handle_telegram_commands(state)

            now = time.time()

            if now - last_scan_time >= SCAN_INTERVAL:
                signal = scan_market()
                print("Signal:", signal)

                if signal and signal["type"] in ["BUY", "SELL"]:

                    active_trade = state.get("active_trade")

                    if active_trade:
                        current_grade = active_trade.get("grade", "B+")
                        new_grade = signal.get("grade", "B+")

                        current_rank = GRADE_RANK.get(current_grade, 1)
                        new_rank = GRADE_RANK.get(new_grade, 1)

                        if new_rank <= current_rank:
                            print(
                                f"已有 {current_grade} 持倉，"
                                f"跳過 {new_grade} 訊號"
                            )

                            last_scan_time = now
                            continue

                    signal_id = (
                        f"{signal['type']}_"
                        f"{signal['grade']}_"
                        f"{signal['entry']}_"
                        f"{signal['score']}"
                    )

                    if state.get("last_signal_id") != signal_id:
                        ai_text = analyze_trade_with_ai(signal)

                        msg = f"""
🔥 {signal['grade']}級訊號｜XAUUSD

方向：{signal['type']}
模式：{signal['mode']}
Grade：{signal['grade']}
Score：{signal['score']}

Entry：{signal['entry']}
SL：{signal['sl']}
TP1：{signal['tp1']}
TP2：{signal['tp2']}

原因：
{', '.join(signal['reasons'])}

===== GPT-5 分析 =====
{ai_text}

若你已開單，請回覆：
/open {signal['type']} 開倉價 手數
"""

                        important = signal["grade"] in ["A+", "A"]
                        send_telegram(msg, important=important)

                        state["last_signal"] = signal
                        state["last_signal_id"] = signal_id
                        save_state(state)

                last_scan_time = now

            last_report_time = state.get("last_report_time", 0)

            if now - last_report_time >= REPORT_INTERVAL:
                report = generate_2h_report()
                send_telegram(report, important=False)

                state["last_report_time"] = now
                save_state(state)

            state = monitor_trade(state)

            time.sleep(MONITOR_INTERVAL)

        except KeyboardInterrupt:
            send_telegram("🛑 Gold AI Trader 已停止")
            break

        except Exception as e:
            print("Error:", e)
            send_telegram(f"⚠️ 系統錯誤：{e}", important=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
