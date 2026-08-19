"""
Standalone background scanner — runs on a GitHub Actions schedule, completely
independent of any browser tab or your Streamlit app. Checks once per run,
logs any signal above the confidence threshold to the same Google Sheet, and
emails you if something is found.

Configuration is read from environment variables (set as GitHub Secrets),
not from a Streamlit secrets.toml — this script never touches Streamlit.
"""

import os
import re
import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

import requests
import gspread
from google.oauth2.service_account import Credentials

# ---- Configuration (edit these directly, or wire up as GitHub Actions inputs later) ----
SYMBOL = os.environ.get("SCAN_SYMBOL", "XAU/USD")
INTERVAL = os.environ.get("SCAN_INTERVAL", "15min")
CONFIDENCE_THRESHOLD = int(os.environ.get("CONFIDENCE_THRESHOLD", "75"))

INTERVAL_TO_CANDLES = {"1min": 100, "5min": 60, "15min": 50, "30min": 40, "1h": 30}

# ---- Secrets, read from environment variables set by GitHub Actions ----
TWELVE_DATA_KEY = os.environ["TWELVE_DATA_KEY"]
CLAUDE_API_KEY = os.environ["CLAUDE_API_KEY"]
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")  # optional
SHEET_NAME = os.environ["SHEET_NAME"]
GCP_SERVICE_ACCOUNT_JSON = os.environ["GCP_SERVICE_ACCOUNT_JSON"]  # full JSON as one string
EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT")


# ---- Shared core framework — identical wording to the Streamlit app, so
# results stay consistent between manual/auto scans and this background one ----

CORE_STRATEGY_FRAMEWORK = """You are an institutional-grade technical market analyst whose objective is to determine whether a high-probability trading opportunity exists, not to predict future price movement. Base every conclusion solely on objective market evidence and never assume missing information. Before evaluating any trade, identify the current market regime as Strong Uptrend, Strong Downtrend, Weak Trend, Range, Compression, Volatility Expansion, Low Volatility Consolidation, Distribution, Accumulation, or Reversal. Only evaluate strategies that statistically perform well in the detected regime. Never force a recommendation; if evidence is conflicting, incomplete, or weak, decline the trade. Trend strategies should only be considered in trending markets, while mean reversion strategies should only be considered in ranging or low-volatility environments. Breakout strategies require both volatility and volume expansion, while reversal strategies require clear evidence of momentum exhaustion and structural confirmation. Every recommendation must include the detected strategy, market regime, confidence score, reasoning, invalidation conditions, and an overall risk assessment. Evaluate the following strategies during every analysis. Trend Pullback: identify established higher highs and higher lows (or lower highs and lower lows), wait for a controlled pullback into dynamic support such as VWAP, EMA, or previous structure, then require continuation confirmation before entry. Opening Range Breakout (ORB): only applicable near the market open after the opening range has formed; require a decisive breakout supported by expanding volume and no immediate rejection. Breakout: require price to close beyond major support or resistance with above-average volume, increasing volatility, and preferably a successful retest. Break and Retest: only after a confirmed breakout where price returns to the breakout level, respects it, and resumes the original direction. Trend Continuation: identify a strong trend followed by a brief consolidation before continuation with expanding momentum. Compression Breakout: identify prolonged low volatility, narrowing price range, and declining volume before a sudden expansion confirms direction. Failed Breakout: detect a breakout that immediately loses momentum, returns inside the previous range, and forms reversal confirmation before considering the opposite direction. Evaluate mean reversion and reversal strategies only when market conditions support them. Mean Reversion: identify significant extension from the market average combined with slowing momentum inside an established range, using factors such as RSI extremes, Bollinger Bands, or standard deviation while avoiding strong trending environments. VWAP Reversion: detect significant extensions away from VWAP followed by weakening momentum and declining participation. VWAP Bounce: in trending markets, identify pullbacks into VWAP that are respected before continuation. Support and Resistance Bounce: require repeated historical reactions at a level and a clear rejection before entry. Liquidity Sweep: identify price briefly taking previous highs or lows before immediately reversing back into range, indicating stop hunting. Fair Value Gap (FVG): identify impulsive moves creating price imbalances, then require retracement into the imbalance before continuation. Order Block: identify fresh institutional supply or demand zones where price revisits and rejects decisively. Volume Profile: evaluate reactions around the Point of Control (POC), High Volume Nodes (HVN), Low Volume Nodes (LVN), and Value Area High/Low, giving higher weight when these align with market structure. Exhaustion Reversal: identify extended directional moves showing divergence, climax volume, slowing momentum, and confirmed structural reversal before considering a counter-trend trade. Calculate a confidence score from 0-100 using only objective evidence. Increase confidence when multiple independent confirmations agree, including higher timeframe trend alignment, market structure, volume expansion, volatility expansion, VWAP agreement, support and resistance confluence, liquidity confirmation, volume profile confluence, healthy pullbacks, and strong continuation candles. Reduce confidence for conflicting signals, weak volume, poor follow-through, repeated tests of key levels, excessive volatility without direction, low liquidity, major scheduled news, or deteriorating market structure. Confidence should represent the statistical quality of the setup rather than certainty of outcome. Use the following scale: 95-100 Exceptional setup with near-perfect confluence; 90-94 Excellent setup with only minor conflicting evidence; 80-89 Good setup with strong statistical edge; 70-79 Moderate edge requiring disciplined risk management; 60-69 Weak edge requiring additional confirmation; 50-59 Very weak setup with significant uncertainty; below 50 automatically means no trade. Also decline whenever no strategy appropriately matches the detected market regime, confirmation signals are insufficient, multiple strategies conflict without a clear winner, market structure is unclear, or the estimated risk-to-reward ratio is below 1.5:1 unless the strategy historically justifies otherwise. Never recommend a trade simply because price may move; only recommend trades when objective evidence strongly supports a defined strategy with measurable statistical edge.

Volume-Free Instrument Exception: For instruments where volume data is unavailable or reported as N/A (such as spot forex and spot commodities like XAU/USD), do not disqualify a setup on volume grounds alone. Instead, substitute the following as confirmation evidence in place of volume: the sharpness and speed of price reactions at key levels (a fast, decisive move away from a level counts as a proxy for conviction), the number of times a level has been tested and defended, the size of the range relative to recent average range (range expansion serves as a proxy for volatility expansion), and candle body-to-wick ratios at turning points (a small wick with a strong body close suggests conviction; a long wick with a weak close suggests rejection). Confidence scoring should still apply the same thresholds, but volume-dependent strategies (Breakout, ORB, Compression Breakout, Trend Continuation, Exhaustion Reversal) may now be evaluated using these substitute signals instead of requiring literal volume figures.

Higher Timeframe Context: When daily candle data is provided alongside intraday data, use it to judge where the current intraday range sits relative to the broader trend, recent swing highs/lows, and overall market structure. Weight setups more favorably when the intraday signal aligns with the daily trend direction, and more cautiously when it conflicts with it (e.g. a bullish intraday setup appearing during a daily downtrend warrants a lower confidence score or added caution in the risk assessment).

Precision Warning for Low-Priced Instruments: When a price is below 0.01 (common in meme coins and micro-cap tokens), write every price value using scientific notation (e.g. 2.9257e-6) instead of long decimal strings, and double-check the exponent matches the actual price scale from the provided data before finalizing any entry, stop-loss, or take-profit figure. Never shift the decimal point when copying a price value from the input data into your output — verify each output price against the input data's actual magnitude before responding.

Time and Frequency Constraint: All trade ideas must be structured for a maximum hold time of 1.5 hours from entry, with take-profit targets realistically reachable within that window based on the instrument's recent volatility and typical move speed — not targets that assume a multi-hour or multi-day move. Additionally, be highly selective — the trader is aiming for roughly 3 well-considered trades per day, not a high-frequency stream of setups — but selectivity means only surfacing genuinely tradeable setups, not artificially requiring a higher confidence bar than the scoring scale above already defines. A legitimate 70-79 "moderate edge" setup should be scored and reported as such, not silently rounded down to below 50 in the name of selectivity. Decline only when the setup genuinely doesn't clear 50 on the scale, or no plausible path to target exists within 1.5 hours.

Risk:Reward Verification: Before finalizing any recommendation, explicitly compute risk as |Entry − Stop Loss| and reward as |Take Profit − Entry|, then divide reward by risk to get the actual R:R ratio. State this calculation's inputs and result accurately — never state an R:R ratio without having correctly performed this division. If the computed ratio falls below 1.5:1, first check whether a more structurally sound stop-loss or take-profit level would genuinely produce 1.5:1 or better, and use those levels if so. Only decline on R:R grounds if no reasonable structural adjustment achieves 1.5:1 and the setup doesn't otherwise justify an exception per the framework above — do not discard an otherwise strong setup over this check alone if better levels are available. The one hard rule: never report an R:R number that your own math doesn't support.

Confidence Score Calibration: Do not default to a habitual middle-range number. Build the score explicitly: list each confirming factor present (trend alignment, structure confluence, volume/proxy conviction, momentum, higher-timeframe agreement) and each detracting factor (conflicting signals, weak follow-through, proximity to resistance, choppiness), then derive the score from the actual count and strength of each — more confirmations with no major detractors should score notably higher (80s-90s), while few confirmations or several detractors should score notably lower (50s-low 60s). Before finalizing, explicitly check whether the evidence actually supports a score in the 40s, 80s, or 90s rather than defaulting toward the middle of the range.

Economic Calendar Awareness: If upcoming high-impact economic events (e.g. NFP, FOMC, CPI) are listed in the provided context, treat any event occurring within the trade's potential 1.5-hour hold window as a meaningful risk factor. Price action can become erratic and disconnected from normal technical structure in the minutes before and after such a release. Reduce confidence accordingly, and if a major release falls within roughly 30 minutes before or during the likely hold window, lean toward declining the trade even if the technical setup otherwise looks strong — note this explicitly as the reason. This applies regardless of instrument, since USD releases affect gold, forex, crypto, and most major stocks.

Take-Profit Realism: Prefer the nearest meaningful structural level as the take-profit target — a prior swing high/low, a round-number level, or the edge of recent consolidation — rather than a full measured-move or impulse-leg extension projected further out. Extended projections (e.g. 1x or 1.5x the size of the prior impulse leg) assume the next move matches the last one's full size, which is an optimistic assumption more often than a realistic one; price frequently reverses partway through such a projection without reaching it. Only reach for a more distant target if the nearest structural level fails to clear the 1.5:1 R:R minimum — and even then, prefer the closest level that does clear it over the most optimistic one available.

ATR-Based Stop Sizing: A precomputed Average True Range (ATR) value is provided in the data context whenever available — always use this exact value rather than estimating your own from the raw candles. The distance between entry and stop-loss should be at least 1x the provided ATR, and ideally 1.2-1.5x ATR when structure allows. A stop tighter than 1x ATR is highly vulnerable to being triggered by ordinary price noise rather than a genuine break of structure, even when the directional read was correct. If the nearest structural level would require a stop tighter than 1x ATR, widen the stop to at least 1x ATR and adjust the take-profit target proportionally to preserve the 1.5:1 R:R minimum, rather than using an overly tight stop just because it sits at a convenient nearby structural point."""

TRADING_STRATEGY_SCAN = CORE_STRATEGY_FRAMEWORK + """

Your response must contain ONLY the six structured fields below and absolutely nothing else. Do not write any heading, preamble, or section such as "Internal Analysis," "Market Regime Detection," "Strategy Evaluation," or similar — even if labeled as internal, draft, or "not printed in output." Any such text still counts as printed output and is strictly forbidden. Do the full calibration and verification silently, then write your very first character as the start of "CONFIDENCE:" — no text of any kind may appear before it.

CONFIDENCE: <0-100>
STRATEGY: <strategy name, or NONE if confidence is below 50>
ENTRY: <price, or N/A>
STOP_LOSS: <price, or N/A>
TAKE_PROFIT: <price, or N/A>
INVALIDATION: <one short line describing what proves the setup wrong, or N/A>

The calibration and verification steps above still happen in full every time — only the final printed output is trimmed down to these six lines."""


def calculate_atr(candles, period=14):
    """Average True Range, calculated properly from actual price data
    instead of being estimated/guessed by the model."""
    if not candles or len(candles) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        prev_close = float(candles[i - 1]["close"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    recent = true_ranges[-period:]
    return sum(recent) / len(recent)


def get_intraday_data(symbol, interval, size):
    url = "https://api.twelvedata.com/time_series"
    params = {"symbol": symbol, "interval": interval, "outputsize": size, "apikey": TWELVE_DATA_KEY}
    response = requests.get(url, params=params, timeout=15)
    data = response.json()
    if "values" not in data:
        return None
    candles = list(reversed(data["values"]))
    return [
        {
            "time": c["datetime"], "open": c["open"], "high": c["high"],
            "low": c["low"], "close": c["close"], "volume": c.get("volume", "N/A"),
        }
        for c in candles
    ]


def get_daily_context(symbol):
    url = "https://api.twelvedata.com/time_series"
    params = {"symbol": symbol, "interval": "1day", "outputsize": 20, "apikey": TWELVE_DATA_KEY}
    response = requests.get(url, params=params, timeout=15)
    data = response.json()
    if "values" not in data:
        return None
    candles = list(reversed(data["values"]))
    return [
        {"date": c["datetime"], "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"]}
        for c in candles
    ]


def get_upcoming_economic_events(hours_ahead=3):
    if not FINNHUB_API_KEY:
        return []
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        url = "https://finnhub.io/api/v1/calendar/economic"
        params = {"from": today, "to": today, "token": FINNHUB_API_KEY}
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        events = data.get("economicCalendar", data.get("data", []))
        now = datetime.now()
        upcoming = []
        for event in events:
            if event.get("impact", "").lower() != "high":
                continue
            time_str = event.get("time")
            if not time_str:
                continue
            try:
                event_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue
            minutes_away = (event_time - now).total_seconds() / 60
            if 0 <= minutes_away <= hours_ahead * 60:
                upcoming.append(f"{event.get('event')} ({event.get('country')}) in ~{int(minutes_away)}min")
        return upcoming
    except Exception:
        return []


def call_claude(system_prompt, user_content):
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 800,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
    }
    response = requests.post(url, headers=headers, json=body, timeout=60)
    result = response.json()
    if "content" not in result:
        raise RuntimeError(f"Claude API error: {result}")
    return result["content"][0]["text"]


def parse_confidence(text):
    match = re.search(r"CONFIDENCE:\s*(\d+)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_field(text, field_name):
    match = re.search(rf"{field_name}:\s*(.+)", text)
    return match.group(1).strip() if match else ""


def log_to_sheet(symbol, scan_text):
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(GCP_SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).sheet1

    confidence = parse_confidence(scan_text)
    strategy = parse_field(scan_text, "STRATEGY")
    entry = parse_field(scan_text, "ENTRY")
    stop_loss = parse_field(scan_text, "STOP_LOSS")
    take_profit = parse_field(scan_text, "TAKE_PROFIT")
    invalidation = parse_field(scan_text, "INVALIDATION")

    sheet.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        symbol, strategy, confidence, entry, stop_loss, take_profit, invalidation, "",
    ])


def send_email(symbol, scan_text, confidence):
    if not (EMAIL_SENDER and EMAIL_APP_PASSWORD and EMAIL_RECIPIENT):
        print("Email not configured — skipping notification.")
        return

    subject = f"Trade Signal: {symbol} — Confidence {confidence}"
    body = f"A new signal was found by the background scanner.\n\nSymbol: {symbol}\n\n{scan_text}"

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECIPIENT

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
        server.send_message(msg)


def main():
    print(f"Checking {SYMBOL} at {INTERVAL}...")

    price_data = get_intraday_data(SYMBOL, INTERVAL, INTERVAL_TO_CANDLES.get(INTERVAL, 50))
    if price_data is None:
        print("Could not fetch price data — aborting this run.")
        return

    daily_data = get_daily_context(SYMBOL)
    events = get_upcoming_economic_events()

    daily_text = (
        f"\n\nHigher timeframe context — last 20 daily candles:\n{json.dumps(daily_data, indent=2)}"
        if daily_data else "\n\n(Daily context unavailable.)"
    )
    atr = calculate_atr(price_data)
    atr_text = (
        f"\n\nCalculated ATR (14-period, {INTERVAL} candles): {atr:.5f} — use this precomputed value "
        f"for stop-loss sizing per the ATR-Based Stop Sizing rule, do not estimate your own."
        if atr is not None else
        "\n\n(Not enough candles yet to calculate a reliable ATR.)"
    )
    events_text = (
        "\n\nUpcoming High-Impact Economic Events: None scheduled in the next few hours."
        if not events else
        "\n\nUpcoming High-Impact Economic Events (factor these into your confidence):\n" + "\n".join(f"- {e}" for e in events)
    )

    content = (
        f"Here is {SYMBOL} price data at {INTERVAL} candles, most recent {len(price_data)} candles, "
        f"oldest to newest:\n\n{json.dumps(price_data, indent=2)}"
        f"{daily_text}{atr_text}{events_text}\n\n"
        "Give a scan reading in the exact trimmed format specified."
    )

    scan_text = call_claude(TRADING_STRATEGY_SCAN, content)
    confidence = parse_confidence(scan_text)

    # Self-consistency check: only re-check when the result is genuinely
    # borderline (within 8 points of threshold) — this is where a single
    # noisy call could flip a real decision, without doubling cost on
    # every clear-cut run.
    if confidence is not None and abs(confidence - CONFIDENCE_THRESHOLD) <= 8:
        print(f"Confidence {confidence} is borderline (threshold {CONFIDENCE_THRESHOLD}) — running confirmation check...")
        confirm_text = call_claude(TRADING_STRATEGY_SCAN, content)
        confirm_confidence = parse_confidence(confirm_text) if confirm_text else None
        if confirm_confidence is not None:
            averaged = round((confidence + confirm_confidence) / 2)
            print(f"First: {confidence}, Confirmation: {confirm_confidence}, Averaged: {averaged}")
            scan_text = re.sub(r"CONFIDENCE:\s*\d+", f"CONFIDENCE: {averaged}", scan_text, count=1, flags=re.IGNORECASE)
            confidence = averaged

    print(f"Confidence: {confidence}")
    print(scan_text)

    if confidence is not None and confidence >= CONFIDENCE_THRESHOLD:
        print("Signal found — logging and notifying.")
        try:
            log_to_sheet(SYMBOL, scan_text)
        except Exception as e:
            print(f"Sheet logging failed: {e}")
        try:
            send_email(SYMBOL, scan_text, confidence)
        except Exception as e:
            print(f"Email failed: {e}")
    else:
        print("Below threshold — no action taken.")


if __name__ == "__main__":
    main()
