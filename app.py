#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon May 11 17:54:50 2026

@author: chenguanting
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

# =====================================================
# CONFIG
# =====================================================
TWELVEDATA_API_KEY ="4a687376a74041138be71968f5acb5cb"
OPENAI_API_KEY ="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"
TELEGRAM_BOT_TOKEN = "8661004639:AAGIYP7RewZ8gVSm93DXEm6DZ-gbkP38tXk"
TELEGRAM_CHAT_ID = "6901713216"

SUPABASE_URL = "https://oiaweokyqcedfbyxuvmz.supabase.co"

SUPABASE_KEY = "sb_publishable_Qpl76-9aCO1I-aUbRLR_ig_wp2CPizL"

SYMBOL = "XAU/USD"
STATE_FILE = "trade_state.json"

SCAN_INTERVAL = 180
MONITOR_INTERVAL = 30
M5_STRUCTURE_INTERVAL = 120

DATA_CACHE = {}

client = OpenAI(api_key=OPENAI_API_KEY)

# =====================================================
# TELEGRAM
# =====================================================

def send_telegram(
    text,
    important=False,
    repeat=1
):

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    # =========================================
    # A+ 超級提醒
    # =========================================

    if "A+" in text:

        text = f"""
🚨🚨🚨 A+級黃金訊號 🚨🚨🚨

{text}
"""

        important = True

        repeat = 3

    # =========================================
    # A級提醒
    # =========================================

    elif "A級" in text or "Grade：A" in text:

        text = f"""
🔥 高品質黃金訊號 🔥

{text}
"""

        important = True

    # =========================================
    # 發送
    # =========================================

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,

        # True = 靜音
        # False = 強制通知
        "disable_notification": not important
    }

    for i in range(repeat):

        try:

            res = requests.post(
                url,
                json=payload,
                timeout=15
            )

            print(
                "Telegram:",
                res.status_code
            )

            # 防止太快被限制
            time.sleep(1)

        except Exception as e:

            print(
                "Telegram Error:",
                e
            )



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
            "last_m5_check": 0
        }

    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)



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
        res = requests.post(
            url,
            headers=headers,
            json=data,
            timeout=15
        )

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

    data = requests.get(url, timeout=20).json()

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

    df = get_data_cached("1min", 30)

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
# GRADE
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


# =====================================================
# SIGNAL
# =====================================================


def smart_money_signal(h1, m30, m15, m5, m1):

    if not valid_session():
        return {"type": "NO_TRADE"}

    if high_volatility(m1):
        return {"type": "NO_TRADE"}

    trend_h1 = trend(h1)
    structure_m30 = market_structure(m30)
    bos_m15 = detect_bos(m15)
    choch_m5 = detect_choch(m5)
    sweep = liquidity_sweep(m5)

    bullish_ob, bearish_ob = detect_order_block(m15)

    fvg = detect_fvg(m15)

    price = m1.iloc[-1]["close"]

    score_buy = 0
    score_sell = 0

    reasons_buy = []
    reasons_sell = []

    # BUY

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

    for zone in fvg:

        if zone["type"] == "BULLISH" and zone["low"] <= price <= zone["high"]:
            score_buy += 2
            reasons_buy.append("Bullish FVG")

    # SELL

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

    for zone in fvg:

        if zone["type"] == "BEARISH" and zone["low"] <= price <= zone["high"]:
            score_sell += 2
            reasons_sell.append("Bearish FVG")

    buy_grade = get_grade(score_buy)

    if buy_grade:

        return {
            "type": "BUY",
            "grade": buy_grade,
            "score": score_buy,
            "entry": round(price, 2),
            "sl": round(price - 8, 2),
            "tp1": round(price + 15, 2),
            "tp2": round(price + 30, 2),
            "reasons": reasons_buy
        }

    sell_grade = get_grade(score_sell)

    if sell_grade:

        return {
            "type": "SELL",
            "grade": sell_grade,
            "score": score_sell,
            "entry": round(price, 2),
            "sl": round(price + 8, 2),
            "tp1": round(price - 15, 2),
            "tp2": round(price - 30, 2),
            "reasons": reasons_sell
        }

    return {"type": "NO_SIGNAL"}


# =====================================================
# GPT
# =====================================================


def analyze_trade_with_ai(signal):

    prompt = f"""
你是專業黃金 Smart Money 交易分析師。

Direction:
{signal['type']}

Grade:
{signal['grade']}

Score:
{signal['score']}

Entry:
{signal['entry']}

SL:
{signal['sl']}

TP1:
{signal['tp1']}

TP2:
{signal['tp2']}

Reasons:
{signal['reasons']}

請簡短分析：
1. 值不值得做
2. 是否追價
3. 風險提醒
"""

    response = client.chat.completions.create(
        model="gpt-5",
        messages=[
            {
                "role": "system",
                "content": "你是黃金交易分析師"
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    return response.choices[0].message.content


# =====================================================
# SCAN
# =====================================================


def scan_market():

    print("掃描市場中...")

    m1 = get_data_cached("1min", 30)
    m5 = get_data_cached("5min", 60)
    m15 = get_data_cached("15min", 180)
    m30 = get_data_cached("30min", 300)
    h1 = get_data_cached("1h", 600)

    if any(df is None for df in [m1, m5, m15, m30, h1]):
        return None

    return smart_money_signal(h1, m30, m15, m5, m1)


# =====================================================
# PNL
# =====================================================


def calculate_pnl_points(trade, price):

    if trade["side"] == "BUY":
        return price - trade["entry"]

    return trade["entry"] - price


# =====================================================
# TELEGRAM COMMANDS
# =====================================================


def handle_telegram_commands(state):

    updates = get_updates(state.get("telegram_offset"))

    if not updates.get("ok"):
        return state

    for item in updates.get("result", []):

        state["telegram_offset"] = item["update_id"] + 1

        text = item.get("message", {}).get("text", "").strip()

        if not text:
            continue

        parts = text.split()
        command = parts[0].lower()

        # OPEN

        if command == "/open":

            if len(parts) < 4:
                send_telegram("格式：/open BUY 4685 0.05")
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

            state["active_trade"] = {
                "side": side,
                "entry": entry,
                "lot": lot,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "grade": "manual"
            }

            save_state(state)

            send_telegram("✅ 已記錄持倉")

        # CLOSE

        elif command == "/close":

            trade = state.get("active_trade")

            if not trade:
                send_telegram("目前沒有持倉")
                continue

            if len(parts) >= 2:
                exit_price = float(parts[1])
            else:
                exit_price = get_current_price()

            pnl_points = calculate_pnl_points(trade, exit_price)

            result = "WIN" if pnl_points > 0 else "LOSS"

            save_trade_to_supabase(
                trade,
                exit_price,
                pnl_points,
                result
            )

            send_telegram(f"""
✅ 已平倉

方向：{trade['side']}
Entry：{trade['entry']}
Exit：{exit_price}

PnL：{round(pnl_points, 2)}
結果：{result}
""")

            state["active_trade"] = None

            save_state(state)

        # STATUS

        elif command == "/status":

            trade = state.get("active_trade")

            if not trade:
                send_telegram("目前沒有持倉")
                continue

            price = get_current_price()

            pnl = calculate_pnl_points(trade, price)

            send_telegram(f"""
📊 持倉狀態

方向：{trade['side']}
Entry：{trade['entry']}
目前價格：{price}
浮動點數：{round(pnl, 2)}
""")

        # STATS

        elif command == "/stats":

            send_telegram(get_trade_stats())

    save_state(state)

    return state


# =====================================================
# MAIN
# =====================================================


def main():

    send_telegram("✅ Gold AI Trader 已啟動")

    state = load_state()

    last_scan_time = 0

    while True:

        try:

            state = handle_telegram_commands(state)

            now = time.time()

            if now - last_scan_time >= SCAN_INTERVAL:

                signal = scan_market()

                print("Signal:", signal)

                if signal and signal["type"] in ["BUY", "SELL"]:

                    ai_text = analyze_trade_with_ai(signal)

                    msg = f"""
🔥 {signal['grade']}級訊號｜XAUUSD

方向：{signal['type']}
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
"""

                    send_telegram(msg)

                last_scan_time = now

            time.sleep(MONITOR_INTERVAL)

        except KeyboardInterrupt:
            break

        except Exception as e:
            print("Error:", e)
            time.sleep(10)


if __name__ == "__main__":
    main()
