"""
Streamlit web app: manual trade analysis + automated background-style scanning.

To run locally (optional, for testing): streamlit run app.py
To deploy: push this to GitHub, then deploy on share.streamlit.io
API keys go in Streamlit's Secrets manager, NOT in this file.
"""

import streamlit as st
import requests
import json
import time
import re
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import html

# ---- API keys pulled from Streamlit Secrets ----
TWELVE_DATA_KEY = st.secrets["TWELVE_DATA_KEY"]
CLAUDE_API_KEY = st.secrets["CLAUDE_API_KEY"]

DEFAULT_SCAN_INTERVAL_MINUTES = 5
DEFAULT_CONFIDENCE_THRESHOLD = 75

INTERVAL_TO_MINUTES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1h": 60}
INTERVAL_TO_CANDLES = {"1min": 100, "5min": 60, "15min": 50, "30min": 40, "1h": 30}
INTERVAL_TO_TV_CODE = {"1min": "1", "5min": "5", "15min": "15", "30min": "30", "1h": "60"}


# ---------------- Shared core framework (identical text used by BOTH prompts) ----------------
# Built once here so the manual scan and auto-scan can never drift apart in
# wording — only the final output-format instructions differ between them.

CORE_STRATEGY_FRAMEWORK = """You are an institutional-grade technical market analyst whose objective is to determine whether a high-probability trading opportunity exists, not to predict future price movement. Base every conclusion solely on objective market evidence and never assume missing information. Before evaluating any trade, identify the current market regime as Strong Uptrend, Strong Downtrend, Weak Trend, Range, Compression, Volatility Expansion, Low Volatility Consolidation, Distribution, Accumulation, or Reversal. Only evaluate strategies that statistically perform well in the detected regime. Never force a recommendation; if evidence is conflicting, incomplete, or weak, decline the trade. Trend strategies should only be considered in trending markets, while mean reversion strategies should only be considered in ranging or low-volatility environments. Breakout strategies require both volatility and volume expansion, while reversal strategies require clear evidence of momentum exhaustion and structural confirmation. Every recommendation must include the detected strategy, market regime, confidence score, reasoning, invalidation conditions, and an overall risk assessment. Evaluate the following strategies during every analysis. Trend Pullback: identify established higher highs and higher lows (or lower highs and lower lows), wait for a controlled pullback into dynamic support such as VWAP, EMA, or previous structure, then require continuation confirmation before entry. Opening Range Breakout (ORB): only applicable near the market open after the opening range has formed; require a decisive breakout supported by expanding volume and no immediate rejection. Breakout: require price to close beyond major support or resistance with above-average volume, increasing volatility, and preferably a successful retest. Break and Retest: only after a confirmed breakout where price returns to the breakout level, respects it, and resumes the original direction. Trend Continuation: identify a strong trend followed by a brief consolidation before continuation with expanding momentum. Compression Breakout: identify prolonged low volatility, narrowing price range, and declining volume before a sudden expansion confirms direction. Failed Breakout: detect a breakout that immediately loses momentum, returns inside the previous range, and forms reversal confirmation before considering the opposite direction. Evaluate mean reversion and reversal strategies only when market conditions support them. Mean Reversion: identify significant extension from the market average combined with slowing momentum inside an established range, using factors such as RSI extremes, Bollinger Bands, or standard deviation while avoiding strong trending environments. VWAP Reversion: detect significant extensions away from VWAP followed by weakening momentum and declining participation. VWAP Bounce: in trending markets, identify pullbacks into VWAP that are respected before continuation. Support and Resistance Bounce: require repeated historical reactions at a level and a clear rejection before entry. Liquidity Sweep: identify price briefly taking previous highs or lows before immediately reversing back into range, indicating stop hunting. Fair Value Gap (FVG): identify impulsive moves creating price imbalances, then require retracement into the imbalance before continuation. Order Block: identify fresh institutional supply or demand zones where price revisits and rejects decisively. Volume Profile: evaluate reactions around the Point of Control (POC), High Volume Nodes (HVN), Low Volume Nodes (LVN), and Value Area High/Low, giving higher weight when these align with market structure. Exhaustion Reversal: identify extended directional moves showing divergence, climax volume, slowing momentum, and confirmed structural reversal before considering a counter-trend trade. Calculate a confidence score from 0-100 using only objective evidence. Increase confidence when multiple independent confirmations agree, including higher timeframe trend alignment, market structure, volume expansion, volatility expansion, VWAP agreement, support and resistance confluence, liquidity confirmation, volume profile confluence, healthy pullbacks, and strong continuation candles. Reduce confidence for conflicting signals, weak volume, poor follow-through, repeated tests of key levels, excessive volatility without direction, low liquidity, major scheduled news, or deteriorating market structure. Confidence should represent the statistical quality of the setup rather than certainty of outcome. Use the following scale: 95-100 Exceptional setup with near-perfect confluence; 90-94 Excellent setup with only minor conflicting evidence; 80-89 Good setup with strong statistical edge; 70-79 Moderate edge requiring disciplined risk management; 60-69 Weak edge requiring additional confirmation; 50-59 Very weak setup with significant uncertainty; below 50 automatically means no trade. Also decline whenever no strategy appropriately matches the detected market regime, confirmation signals are insufficient, multiple strategies conflict without a clear winner, market structure is unclear, or the estimated risk-to-reward ratio is below 1.5:1 unless the strategy historically justifies otherwise. Never recommend a trade simply because price may move; only recommend trades when objective evidence strongly supports a defined strategy with measurable statistical edge.

Volume-Free Instrument Exception: For instruments where volume data is unavailable or reported as N/A (such as spot forex and spot commodities like XAU/USD), do not disqualify a setup on volume grounds alone. Instead, substitute the following as confirmation evidence in place of volume: the sharpness and speed of price reactions at key levels (a fast, decisive move away from a level counts as a proxy for conviction), the number of times a level has been tested and defended, the size of the range relative to recent average range (range expansion serves as a proxy for volatility expansion), and candle body-to-wick ratios at turning points (a small wick with a strong body close suggests conviction; a long wick with a weak close suggests rejection). Confidence scoring should still apply the same thresholds, but volume-dependent strategies (Breakout, ORB, Compression Breakout, Trend Continuation, Exhaustion Reversal) may now be evaluated using these substitute signals instead of requiring literal volume figures.

Higher Timeframe Context: When daily candle data is provided alongside intraday data, use it to judge where the current intraday range sits relative to the broader trend, recent swing highs/lows, and overall market structure. Weight setups more favorably when the intraday signal aligns with the daily trend direction, and more cautiously when it conflicts with it (e.g. a bullish intraday setup appearing during a daily downtrend warrants a lower confidence score or added caution in the risk assessment).

Precision Warning for Low-Priced Instruments: When a price is below 0.01 (common in meme coins and micro-cap tokens), write every price value using scientific notation (e.g. 2.9257e-6) instead of long decimal strings, and double-check the exponent matches the actual price scale from the provided data before finalizing any entry, stop-loss, or take-profit figure. Never shift the decimal point when copying a price value from the input data into your output — verify each output price against the input data's actual magnitude before responding.

Time and Frequency Constraint: All trade ideas must be structured for a maximum hold time of 1.5 hours from entry, with take-profit targets realistically reachable within that window based on the instrument's recent volatility and typical move speed — not targets that assume a multi-hour or multi-day move. Additionally, be highly selective — the trader is aiming for roughly 3 well-considered trades per day, not a high-frequency stream of setups — but selectivity means only surfacing genuinely tradeable setups, not artificially requiring a higher confidence bar than the scoring scale above already defines. A legitimate 70-79 "moderate edge" setup should be scored and reported as such, not silently rounded down to below 50 in the name of selectivity. Decline only when the setup genuinely doesn't clear 50 on the scale, or no plausible path to target exists within 1.5 hours.

Risk:Reward Verification: Before finalizing any recommendation, explicitly compute risk as |Entry − Stop Loss| and reward as |Take Profit − Entry|, then divide reward by risk to get the actual R:R ratio. State this calculation's inputs and result accurately — never state an R:R ratio without having correctly performed this division. If the computed ratio falls below 1.5:1, first check whether a more structurally sound stop-loss or take-profit level would genuinely produce 1.5:1 or better, and use those levels if so. Only decline on R:R grounds if no reasonable structural adjustment achieves 1.5:1 and the setup doesn't otherwise justify an exception per the framework above — do not discard an otherwise strong setup over this check alone if better levels are available. The one hard rule: never report an R:R number that your own math doesn't support.

Confidence Score Calibration: Do not default to a habitual middle-range number. Build the score explicitly: list each confirming factor present (trend alignment, structure confluence, volume/proxy conviction, momentum, higher-timeframe agreement) and each detracting factor (conflicting signals, weak follow-through, proximity to resistance, choppiness), then derive the score from the actual count and strength of each — more confirmations with no major detractors should score notably higher (80s-90s), while few confirmations or several detractors should score notably lower (50s-low 60s). Before finalizing, explicitly check whether the evidence actually supports a score in the 40s, 80s, or 90s rather than defaulting toward the middle of the range.

Economic Calendar Awareness: If upcoming high-impact economic events (e.g. NFP, FOMC, CPI) are listed in the provided context, treat any event occurring within the trade's potential 1.5-hour hold window as a meaningful risk factor. Price action can become erratic and disconnected from normal technical structure in the minutes before and after such a release. Reduce confidence accordingly, and if a major release falls within roughly 30 minutes before or during the likely hold window, lean toward declining the trade even if the technical setup otherwise looks strong — note this explicitly as the reason. This applies regardless of instrument, since USD releases affect gold, forex, crypto, and most major stocks.

Take-Profit Realism: Prefer the nearest meaningful structural level as the take-profit target — a prior swing high/low, a round-number level, or the edge of recent consolidation — rather than a full measured-move or impulse-leg extension projected further out. Extended projections (e.g. 1x or 1.5x the size of the prior impulse leg) assume the next move matches the last one's full size, which is an optimistic assumption more often than a realistic one; price frequently reverses partway through such a projection without reaching it. Only reach for a more distant target if the nearest structural level fails to clear the 1.5:1 R:R minimum — and even then, prefer the closest level that does clear it over the most optimistic one available."""

# ---------------- Full prompt (manual scans): shared core + full written format ----------------

TRADING_STRATEGY_FULL = CORE_STRATEGY_FRAMEWORK + """

For every analysis, structure your output exactly as follows:
- Detected Strategy
- Market Regime
- Confidence Score (0-100)
- Entry Price
- Stop Loss Price (with reasoning — e.g. below structure, below ATR-based buffer)
- Take Profit Price (with reasoning, and resulting risk:reward ratio)
- Invalidation Conditions (what would prove this setup wrong)
- Risk Assessment (1-2 sentences)

If confidence is below 50, or no strategy fits, output DONT_RECOMMEND with a brief reason instead of the above fields. If the reason for declining is a failed R:R check specifically, do not first print the full structured fields and then append DONT_RECOMMEND — switch entirely to the DONT_RECOMMEND format and explain the R:R shortfall as the reason, without listing the abandoned price levels as if they were a live recommendation.

After the initial scan, the user may ask follow-up questions about the analysis. Answer those using the same price data and strategy framework, staying consistent with your original assessment unless the user points out something you missed.

Keep your entire response under 600 words. Do not narrate your reasoning process step-by-step or show your analysis of each strategy candidate — go straight to the final structured output listed above. Only include brief supporting reasoning inline within each field (e.g. one short clause for why that stop-loss level), not separate paragraphs."""

# ---------------- Scan prompt (auto-scanning): same shared core + trimmed format ----------------

TRADING_STRATEGY_SCAN = CORE_STRATEGY_FRAMEWORK + """

Your response must contain ONLY the six structured fields below and absolutely nothing else. Do not write any heading, preamble, or section such as "Internal Analysis," "Market Regime Detection," "Strategy Evaluation," or similar — even if labeled as internal, draft, or "not printed in output." Any such text still counts as printed output and is strictly forbidden. Do the full calibration and verification silently, then write your very first character as the start of "CONFIDENCE:" — no text of any kind may appear before it.

CONFIDENCE: <0-100>
STRATEGY: <strategy name, or NONE if confidence is below 50>
ENTRY: <price, or N/A>
STOP_LOSS: <price, or N/A>
TAKE_PROFIT: <price, or N/A>
INVALIDATION: <one short line describing what proves the setup wrong, or N/A>

The calibration and verification steps above still happen in full every time — only the final printed output is trimmed down to these six lines."""


def get_intraday_data(symbol, interval, size=50):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": size,
        "apikey": TWELVE_DATA_KEY,
    }
    response = requests.get(url, params=params)
    data = response.json()

    if "values" not in data:
        return None, data

    candles = list(reversed(data["values"]))
    summary = []
    for candle in candles:
        summary.append({
            "time": candle["datetime"],
            "open": candle["open"],
            "high": candle["high"],
            "low": candle["low"],
            "close": candle["close"],
            "volume": candle.get("volume", "N/A"),
        })
    return summary, None


def get_daily_context(symbol):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": "1day",
        "outputsize": 20,
        "apikey": TWELVE_DATA_KEY,
    }
    response = requests.get(url, params=params)
    data = response.json()

    if "values" not in data:
        return None

    candles = list(reversed(data["values"]))
    summary = []
    for candle in candles:
        summary.append({
            "date": candle["datetime"],
            "open": candle["open"],
            "high": candle["high"],
            "low": candle["low"],
            "close": candle["close"],
        })
    return summary


def call_claude(system_prompt, messages, max_tokens=1500):
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": messages,
    }
    response = requests.post(url, headers=headers, json=body)
    result = response.json()

    if "content" not in result:
        return None, result

    return result["content"][0]["text"], None


def build_data_message(symbol, interval, price_data, daily_data, instruction):
    price_data_text = json.dumps(price_data, indent=2)
    daily_context_text = (
        f"\n\nHigher timeframe context — last 20 daily candles:\n{json.dumps(daily_data, indent=2)}"
        if daily_data else "\n\n(Daily context unavailable for this symbol.)"
    )
    economic_events = get_upcoming_economic_events_cached()
    economic_context_text = format_economic_events_context(economic_events)
    return (
        f"Here is {symbol} price data at {interval} candles, "
        f"most recent {len(price_data)} candles, oldest to newest:\n\n"
        f"{price_data_text}"
        f"{daily_context_text}"
        f"{economic_context_text}\n\n"
        f"{instruction}"
    )


def parse_price_field(scan_text, field_name):
    """Extract a numeric price value for a given field label (e.g. ENTRY, TAKE_PROFIT)."""
    if not scan_text:
        return None
    match = re.search(rf"{field_name}:\s*([\d.]+(?:e-?\d+)?)", scan_text, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def check_signal_staleness(scan_text, current_price):
    """Compare a signal's entry price against the current live price and flag
    if price has already moved past it, since the AI can't know the price at
    the exact moment the user reads the card — only the app can check that."""
    if current_price is None:
        return None
    entry = parse_price_field(scan_text, "ENTRY")
    take_profit = parse_price_field(scan_text, "TAKE_PROFIT")
    if entry is None or take_profit is None:
        return None

    is_long = take_profit > entry  # direction inferred from where the target sits
    if is_long and current_price > entry:
        return f"⚠ Price has already moved past this entry (now {current_price:.5f} vs entry {entry:.5f})"
    if not is_long and current_price < entry:
        return f"⚠ Price has already moved past this entry (now {current_price:.5f} vs entry {entry:.5f})"
    return None


def get_upcoming_economic_events(hours_ahead=3):
    """Pull high-impact economic events (NFP, FOMC, CPI, etc.) in the next
    few hours from Finnhub's free economic calendar. Returns a list of dicts,
    or an empty list if the key isn't set up or the call fails — this should
    never block a scan from running."""
    if "FINNHUB_API_KEY" not in st.secrets:
        return []
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        url = "https://finnhub.io/api/v1/calendar/economic"
        params = {"from": today, "to": today, "token": st.secrets["FINNHUB_API_KEY"]}
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        events = data.get("economicCalendar", data.get("data", []))

        now = datetime.now()
        upcoming = []
        for event in events:
            if event.get("impact", "").lower() not in ("high",):
                continue
            event_time_str = event.get("time")
            if not event_time_str:
                continue
            try:
                event_time = datetime.strptime(event_time_str, "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue
            minutes_away = (event_time - now).total_seconds() / 60
            if 0 <= minutes_away <= hours_ahead * 60:
                upcoming.append({
                    "event": event.get("event", "Unknown event"),
                    "country": event.get("country", ""),
                    "time": event_time.strftime("%H:%M"),
                    "minutes_away": int(minutes_away),
                })
        return upcoming
    except Exception:
        return []  # never let a calendar failure block scanning


def format_economic_events_context(events):
    """Turn the events list into a short text block to include in the
    prompt, or a note that none are imminent."""
    if not events:
        return "\n\nUpcoming High-Impact Economic Events: None scheduled in the next few hours."
    lines = [f"- {e['event']} ({e['country']}) at {e['time']}, in ~{e['minutes_away']} min" for e in events]
    return "\n\nUpcoming High-Impact Economic Events (factor these into your risk assessment and confidence — avoid recommending entries shortly before a major release):\n" + "\n".join(lines)


def get_upcoming_economic_events_cached():
    """Only check the calendar every ~10 minutes rather than on every scan —
    events don't change minute to minute, so this saves API calls."""
    now = time.time()
    if (
        "econ_events_cache" not in st.session_state
        or now - st.session_state.get("econ_events_cache_time", 0) > 600
    ):
        st.session_state.econ_events_cache = get_upcoming_economic_events()
        st.session_state.econ_events_cache_time = now
    return st.session_state.econ_events_cache


def github_api_headers():
    return {
        "Authorization": f"Bearer {st.secrets['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }


def get_background_scanner_status():
    """Returns 'active', 'disabled_manually', or None if the check fails."""
    try:
        owner = st.secrets["GITHUB_OWNER"]
        repo = st.secrets["GITHUB_REPO"]
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/scan.yml"
        response = requests.get(url, headers=github_api_headers(), timeout=10)
        if response.status_code == 200:
            return response.json().get("state")
        return None
    except Exception:
        return None


def set_background_scanner_enabled(enable: bool):
    """Enables or disables the scheduled GitHub Actions workflow. Returns
    True on success, False (with an error stored) on failure."""
    try:
        owner = st.secrets["GITHUB_OWNER"]
        repo = st.secrets["GITHUB_REPO"]
        action = "enable" if enable else "disable"
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/scan.yml/{action}"
        response = requests.put(url, headers=github_api_headers(), timeout=10)
        if response.status_code == 204:
            return True
        st.session_state.background_scanner_error = f"GitHub API returned {response.status_code}: {response.text}"
        return False
    except Exception as e:
        st.session_state.background_scanner_error = str(e)
        return False


def get_sheet():
    """Connect to the Google Sheet used for logging signals. Stores any
    error in session_state for display, rather than failing completely
    silently, so logging issues can actually be diagnosed."""
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=scopes
        )
        client = gspread.authorize(creds)
        sheet = client.open(st.secrets["SHEET_NAME"]).sheet1
        st.session_state.last_sheet_error = None
        return sheet
    except Exception as e:
        st.session_state.last_sheet_error = f"{type(e).__name__}: {e}"
        return None


def log_signal_to_sheet(symbol, scan_text):
    """Append a new signal row to the Google Sheet. Logging failures never
    interrupt scanning, but the error is saved for display."""
    sheet = get_sheet()
    if sheet is None:
        return
    try:
        confidence = parse_confidence(scan_text)
        entry = parse_price_field(scan_text, "ENTRY")
        stop_loss = parse_price_field(scan_text, "STOP_LOSS")
        take_profit = parse_price_field(scan_text, "TAKE_PROFIT")
        strategy_match = re.search(r"STRATEGY:\s*(.+)", scan_text)
        strategy = strategy_match.group(1).strip() if strategy_match else ""
        invalidation_match = re.search(r"INVALIDATION:\s*(.+)", scan_text, re.DOTALL)
        invalidation = invalidation_match.group(1).strip() if invalidation_match else ""

        sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol,
            strategy,
            confidence,
            entry,
            stop_loss,
            take_profit,
            invalidation,
            "",  # Outcome — filled in later by you once the trade plays out
        ])
        st.session_state.last_sheet_error = None
    except Exception as e:
        st.session_state.last_sheet_error = f"{type(e).__name__}: {e}"


def parse_confidence(scan_text):
    if not scan_text:
        return None
    # Primary format: "CONFIDENCE: 72"
    match = re.search(r"CONFIDENCE:\s*(\d+)", scan_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    # Fallback: catches drift like "Confidence Score: 72/100" or "Confidence: 72"
    match = re.search(r"Confidence\s*(?:Score)?:?\s*(\d+)", scan_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def guess_tradingview_symbol(symbol):
    """Best-effort guess at a TradingView-formatted symbol (EXCHANGE:TICKER).
    Not guaranteed correct — the chart symbol field lets the user override it."""
    s = symbol.strip().upper()
    if "/" in s:
        base, quote = s.split("/", 1)
        # Common metals/commodities on forex-style feeds
        if base in ("XAU", "XAG"):
            return f"OANDA:{base}{quote}"
        # Otherwise assume crypto pair
        return f"BINANCE:{base}{quote}T" if quote == "USD" else f"BINANCE:{base}{quote}"
    # Plain ticker — assume a stock, default to NASDAQ (user can override if wrong exchange)
    return f"NASDAQ:{s}"


def render_countdown(remaining_seconds, total_seconds, height=60):
    """Client-side ticking countdown — updates every second in the browser
    without needing a Streamlit rerun, so the page doesn't visibly reload
    just to move a progress bar."""
    widget_html = f"""
    <div style="font-family: 'IBM Plex Mono', monospace; color:#E8EAF0; padding:4px 0;">
      <div id="countdown-text" style="margin-bottom:6px; font-size:0.85rem;"></div>
      <div style="background-color:#2A3050; border-radius:4px; height:8px; overflow:hidden;">
        <div id="countdown-bar" style="background-color:#C9A24B; height:100%; width:0%; transition:width 1s linear;"></div>
      </div>
    </div>
    <script>
    let remaining = {remaining_seconds};
    const total = {total_seconds};
    const textEl = document.getElementById('countdown-text');
    const barEl = document.getElementById('countdown-bar');
    function update() {{
      const pct = Math.max(0, Math.min(100, ((total - remaining) / total) * 100));
      barEl.style.width = pct + '%';
      textEl.textContent = 'Next check in ~' + Math.max(0, Math.round(remaining)) + 's';
    }}
    update();
    const timer = setInterval(function() {{
      remaining -= 1;
      if (remaining <= 0) {{
        clearInterval(timer);
      }}
      update();
    }}, 1000);
    </script>
    """
    st.components.v1.html(widget_html, height=height)


def render_tradingview_chart(tv_symbol, height=500, interval="15"):
    """Embed a live TradingView chart widget for the given symbol.
    interval uses TradingView's own codes: 1, 5, 15, 30, 60, D (daily)."""
    widget_html = f"""
    <div class="tradingview-widget-container" style="height:100%; width:100%;">
      <div id="tradingview_chart" style="height:100%; width:100%;"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
      new TradingView.widget({{
        "width": "100%",
        "height": {height},
        "symbol": "{tv_symbol}",
        "interval": "{interval}",
        "timezone": "Etc/UTC",
        "theme": "dark",
        "style": "1",
        "locale": "en",
        "toolbar_bg": "#131829",
        "enable_publishing": false,
        "hide_top_toolbar": false,
        "save_image": false,
        "container_id": "tradingview_chart"
      }});
      </script>
    </div>
    """
    st.components.v1.html(widget_html, height=height)


# ---------------- UI starts here ----------------

st.set_page_config(page_title="MissionToMars", page_icon="▪", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
}

.stApp {
    background-color: #0B0E1A;
    color: #E4E6ED;
}

h1, h2, h3 {
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em;
}

h1 {
    font-weight: 700 !important;
    color: #F5F6FA !important;
    font-size: 1.7rem !important;
}

.stCaption, [data-testid="stCaptionContainer"] {
    color: #7A8199 !important;
    font-family: 'IBM Plex Sans', sans-serif !important;
    letter-spacing: 0.01em;
}

/* Buttons */
.stButton > button {
    font-family: 'IBM Plex Sans', sans-serif;
    font-weight: 500;
    letter-spacing: 0.01em;
    border-radius: 3px;
    border: 1px solid #2A3050;
    background-color: #131829;
    color: #E4E6ED;
    transition: border-color 0.15s ease;
}
.stButton > button:hover {
    border-color: #C9A24B;
    color: #C9A24B;
}

/* Tabs */
.stTabs [data-baseweb="tab"] {
    font-family: 'IBM Plex Sans', sans-serif;
    font-weight: 500;
    letter-spacing: 0.01em;
    color: #7A8199;
}
.stTabs [aria-selected="true"] {
    color: #C9A24B !important;
}

/* Text inputs and selects */
.stTextInput input, .stSelectbox div[data-baseweb="select"] {
    font-family: 'IBM Plex Mono', monospace;
    background-color: #131829 !important;
    border-color: #2A3050 !important;
    color: #E4E6ED !important;
}

/* Slider */
.stSlider [data-baseweb="slider"] {
    color: #C9A24B;
}

/* Expander (fallback, in case still used) */
.streamlit-expanderHeader {
    font-family: 'IBM Plex Mono', monospace;
    background-color: #131829 !important;
    border-radius: 3px;
}

hr, [data-testid="stDivider"] {
    border-color: #2A3050 !important;
}

@keyframes pulse-dot {
    0% { opacity: 1; box-shadow: 0 0 0 0 rgba(95, 191, 119, 0.5); }
    70% { opacity: 0.6; box-shadow: 0 0 0 6px rgba(95, 191, 119, 0); }
    100% { opacity: 1; box-shadow: 0 0 0 0 rgba(95, 191, 119, 0); }
}
.live-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background-color: #5FBF77;
    margin-right: 8px;
    animation: pulse-dot 1.8s infinite;
    vertical-align: middle;
}

.stProgress > div > div {
    background-color: #C9A24B !important;
}
</style>
""", unsafe_allow_html=True)

st.markdown(
    "<div style='display:flex; align-items:baseline; gap:10px; "
    "border-bottom:1px solid #2A3050; padding-bottom:10px; margin-bottom:4px;'>"
    "<h1 style='margin:0;'>MissionToMars</h1>"
    "<span style='font-family:IBM Plex Mono, monospace; color:#7A8199; "
    "font-size:0.75rem; letter-spacing:0.02em;'>MARKET ANALYSIS TERMINAL</span>"
    "</div>",
    unsafe_allow_html=True,
)

_upcoming_events = get_upcoming_economic_events_cached()
if _upcoming_events:
    _event_lines = "; ".join(
        f"{e['event']} ({e['country']}) in ~{e['minutes_away']}min" for e in _upcoming_events
    )
    st.warning(f"⚠ High-impact economic event(s) approaching: {_event_lines}")

# ---- Session state setup ----
if "conversation" not in st.session_state:
    st.session_state.conversation = []
if "scanned" not in st.session_state:
    st.session_state.scanned = False
if "auto_scanning" not in st.session_state:
    st.session_state.auto_scanning = False
if "last_auto_scan" not in st.session_state:
    st.session_state.last_auto_scan = 0
if "signals" not in st.session_state:
    st.session_state.signals = []  # list of dicts: {time, symbol, text}
if "auto_symbol" not in st.session_state:
    st.session_state.auto_symbol = "XAU/USD"
if "confidence_threshold" not in st.session_state:
    st.session_state.confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD
if "last_price" not in st.session_state:
    st.session_state.last_price = None
if "prev_price" not in st.session_state:
    st.session_state.prev_price = None
if "last_check_debug" not in st.session_state:
    st.session_state.last_check_debug = None  # holds raw text + any error from most recent check
if "last_sheet_error" not in st.session_state:
    st.session_state.last_sheet_error = None
if "chart_symbol_override" not in st.session_state:
    st.session_state.chart_symbol_override = ""
if "show_chart" not in st.session_state:
    st.session_state.show_chart = True
if "auto_interval" not in st.session_state:
    st.session_state.auto_interval = "15min"
if "scan_interval_minutes" not in st.session_state:
    st.session_state.scan_interval_minutes = DEFAULT_SCAN_INTERVAL_MINUTES
if "background_scanner_error" not in st.session_state:
    st.session_state.background_scanner_error = None

# Auto-stop the background GitHub Actions scanner once per session when the
# app is opened, so it never keeps running unattended just because it was
# left on from a previous session.
if "auto_stopped_background_scanner" not in st.session_state:
    st.session_state.auto_stopped_background_scanner = True
    if get_background_scanner_status() == "active":
        set_background_scanner_enabled(False)

tab_manual, tab_auto, tab_background = st.tabs(["Manual Scan", "Auto Scanning", "Background Scanner"])

# ---------------- MANUAL SCAN TAB ----------------
with tab_manual:
    with st.form("scan_form"):
        col1, col2 = st.columns(2)
        with col1:
            symbol = st.text_input("Stock symbol", value="XAU/USD")
        with col2:
            interval = st.selectbox("Interval", ["5min", "15min", "30min", "1h"], index=1)
        run_scan = st.form_submit_button("Run Scan")

    if run_scan:
        with st.spinner(f"Fetching {interval} data for {symbol}..."):
            price_data, error = get_intraday_data(
                symbol, interval, size=INTERVAL_TO_CANDLES.get(interval, 50)
            )

        if price_data is None:
            st.error(f"Could not fetch price data: {error}")
        else:
            with st.spinner("Fetching daily context..."):
                daily_data = get_daily_context(symbol)

            instruction = (
                "Analyze this for a short-term trade using the exact output "
                "format specified. Use the daily context to judge where the "
                "intraday range sits relative to the broader trend."
            )
            content = build_data_message(symbol, interval, price_data, daily_data, instruction)

            st.session_state.conversation = [{"role": "user", "content": content}]

            with st.spinner("Analyzing..."):
                analysis, error = call_claude(TRADING_STRATEGY_FULL, st.session_state.conversation)

            if analysis is None:
                st.error(f"Claude API error: {error}")
            else:
                st.session_state.conversation.append({"role": "assistant", "content": analysis})
                st.session_state.scanned = True

    if st.session_state.scanned:
        st.divider()
        for i, msg in enumerate(st.session_state.conversation):
            if msg["role"] == "assistant":
                with st.chat_message("assistant"):
                    st.markdown(msg["content"])
            elif msg["role"] == "user" and i != 0:
                with st.chat_message("user"):
                    st.markdown(msg["content"])

        question = st.chat_input("Ask a follow-up question about this analysis")
        if question:
            st.session_state.conversation.append({"role": "user", "content": question})
            with st.spinner("Thinking..."):
                answer, error = call_claude(TRADING_STRATEGY_FULL, st.session_state.conversation)
            if answer:
                st.session_state.conversation.append({"role": "assistant", "content": answer})
                st.rerun()
            else:
                st.error(f"Claude API error: {error}")

# ---------------- AUTO SCANNING TAB ----------------
with tab_auto:
    st.write("Configure your scan below.")

    col_symbol, col_threshold = st.columns(2)
    with col_symbol:
        auto_symbol = st.text_input("Symbol to watch", value=st.session_state.auto_symbol, key="auto_symbol_input")
        st.session_state.auto_symbol = auto_symbol
    with col_threshold:
        threshold = st.slider(
            "Minimum confidence to report",
            min_value=50,
            max_value=95,
            value=st.session_state.confidence_threshold,
            step=5,
            disabled=st.session_state.auto_scanning,
            help="Locked while scanning is active — stop scanning to change it.",
        )
        st.session_state.confidence_threshold = threshold

    col_interval, col_timer = st.columns(2)
    with col_interval:
        interval_options = ["1min", "5min", "15min", "30min", "1h"]
        auto_interval = st.select_slider(
            "Candle interval",
            options=interval_options,
            value=st.session_state.auto_interval,
            disabled=st.session_state.auto_scanning,
            help="Locked while scanning is active — stop scanning to change it.",
        )
        st.session_state.auto_interval = auto_interval
    with col_timer:
        scan_timer = st.slider(
            "Check every (minutes)",
            min_value=1,
            max_value=60,
            value=st.session_state.scan_interval_minutes,
            step=1,
            disabled=st.session_state.auto_scanning,
            help="Locked while scanning is active — stop scanning to change it.",
        )
        st.session_state.scan_interval_minutes = scan_timer

    # Helpful nudge if checking more often than candles actually close —
    # those extra checks would just re-read the same unclosed candle.
    interval_minutes = INTERVAL_TO_MINUTES.get(st.session_state.auto_interval, 15)
    if st.session_state.scan_interval_minutes < interval_minutes:
        st.caption(
            f"⚠️ Checking every {st.session_state.scan_interval_minutes}min but candles are "
            f"{st.session_state.auto_interval} — some checks will re-read the same candle. "
            f"Matching the two avoids wasted calls."
        )

    st.write(f"Checks every {st.session_state.scan_interval_minutes} minute(s) using {st.session_state.auto_interval} candles.")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Start Scanning", disabled=st.session_state.auto_scanning):
            st.session_state.auto_scanning = True
            st.session_state.last_auto_scan = 0  # force an immediate check
            st.rerun()
    with col2:
        if st.button("Stop Scanning", disabled=not st.session_state.auto_scanning):
            st.session_state.auto_scanning = False
            st.rerun()

    @st.fragment(run_every="3s")
    def auto_scan_fragment():
        """Everything in here refreshes on its own timer, independent of the
        rest of the page — the chart and manual scan tab never reload or
        dim when this fragment updates."""
        status = st.empty()
        metrics_row = st.empty()

        if st.session_state.auto_scanning:
            status.markdown(
                f"<span class='live-dot'></span>"
                f"<span style='font-family:\"IBM Plex Mono\", monospace; color:#5FBF77;'>"
                f"LIVE — watching {st.session_state.auto_symbol}</span>",
                unsafe_allow_html=True,
            )

            now = time.time()
            seconds_since_last = now - st.session_state.last_auto_scan
            scan_interval_seconds = st.session_state.scan_interval_minutes * 60

            if seconds_since_last >= scan_interval_seconds:
                with st.spinner(f"Checking {st.session_state.auto_symbol}..."):
                    price_data, error = get_intraday_data(
                        st.session_state.auto_symbol,
                        st.session_state.auto_interval,
                        size=INTERVAL_TO_CANDLES.get(st.session_state.auto_interval, 50),
                    )
                    if price_data is None:
                        st.session_state.last_check_debug = {
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "error": f"Price fetch failed: {error}",
                            "confidence": None,
                        }
                    else:
                        latest_close = float(price_data[-1]["close"])
                        st.session_state.prev_price = st.session_state.last_price
                        st.session_state.last_price = latest_close

                        daily_data = get_daily_context(st.session_state.auto_symbol)
                        instruction = "Give a scan reading in the exact trimmed format specified."
                        content = build_data_message(
                            st.session_state.auto_symbol, st.session_state.auto_interval, price_data, daily_data, instruction
                        )
                        scan_text, error = call_claude(
                            TRADING_STRATEGY_SCAN, [{"role": "user", "content": content}], max_tokens=800
                        )
                        if scan_text is None:
                            st.session_state.last_check_debug = {
                                "time": datetime.now().strftime("%H:%M:%S"),
                                "error": f"Claude API error: {error}",
                                "confidence": None,
                            }
                        else:
                            confidence = parse_confidence(scan_text)
                            st.session_state.last_check_debug = {
                                "time": datetime.now().strftime("%H:%M:%S"),
                                "error": None if confidence is not None else "Could not parse a confidence value from the response — see raw text below.",
                                "confidence": confidence,
                                "raw": scan_text,
                            }
                            if confidence is not None and confidence >= st.session_state.confidence_threshold:
                                st.session_state.signals.insert(0, {
                                    "id": f"{datetime.now().timestamp()}",
                                    "time": datetime.now().strftime("%H:%M:%S"),
                                    "symbol": st.session_state.auto_symbol,
                                    "text": scan_text,
                                })
                                log_signal_to_sheet(st.session_state.auto_symbol, scan_text)
                st.session_state.last_auto_scan = time.time()

            if st.session_state.last_price is not None:
                delta = None
                if st.session_state.prev_price is not None:
                    delta = st.session_state.last_price - st.session_state.prev_price
                with metrics_row.container():
                    st.metric(
                        label=f"{st.session_state.auto_symbol} — last checked price",
                        value=f"{st.session_state.last_price:.8f}".rstrip("0").rstrip("."),
                        delta=f"{delta:.8f}".rstrip("0").rstrip(".") if delta else None,
                    )

            if st.session_state.last_check_debug:
                dbg = st.session_state.last_check_debug
                if dbg.get("error"):
                    st.warning(f"Last check ({dbg['time']}): {dbg['error']}")
                    if dbg.get("raw"):
                        with st.expander("Raw response from last check"):
                            st.text(dbg["raw"])
                else:
                    st.caption(
                        f"Last check ({dbg['time']}): confidence {dbg['confidence']} "
                        f"— threshold is {st.session_state.confidence_threshold}"
                    )

            if st.session_state.last_sheet_error:
                st.error(f"Sheet logging failed: {st.session_state.last_sheet_error}")

        if st.session_state.signals:
            st.markdown(
                "<h3 style='margin-top:1.5rem;'>Signals Found</h3>", unsafe_allow_html=True
            )
            for sig in st.session_state.signals:
                confidence = parse_confidence(sig["text"])
                if confidence is not None and confidence >= 85:
                    tier_color, tier_label = "#5FBF77", "STRONG"
                elif confidence is not None and confidence >= 75:
                    tier_color, tier_label = "#C9A24B", "MODERATE"
                else:
                    tier_color, tier_label = "#4FD1C5", "SIGNAL"

                body_html = html.escape(sig["text"]).replace("\n", "<br>")
                staleness_warning = check_signal_staleness(sig["text"], st.session_state.last_price)

                close_col, card_col = st.columns([0.05, 0.95])
                with close_col:
                    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)
                    if st.button("✕", key=f"close_{sig.get('id', sig['time'])}", help="Dismiss this signal"):
                        st.session_state.signals = [
                            s for s in st.session_state.signals
                            if s.get("id", s["time"]) != sig.get("id", sig["time"])
                        ]
                with card_col:
                    stale_html = ""
                    if staleness_warning:
                        stale_html = (
                            "<div style='background-color:#3A2A1A; color:#E8B25F; border-radius:3px; "
                            "padding:6px 10px; font-family:\"IBM Plex Mono\", monospace; "
                            "font-size:0.78rem; margin-bottom:10px;'>"
                            + html.escape(staleness_warning) + "</div>"
                        )

                    card_html = (
                        "<div style='background-color:#131829; border-left:4px solid " + tier_color + "; "
                        "border-radius:4px; padding:14px 18px; margin-bottom:12px;'>"
                        "<div style='display:flex; justify-content:space-between; align-items:center; "
                        "font-family:\"IBM Plex Sans\", sans-serif; font-weight:600; "
                        "color:" + tier_color + "; font-size:0.85rem; letter-spacing:0.05em; margin-bottom:8px;'>"
                        "<span>" + html.escape(tier_label) + " — " + html.escape(sig['symbol']) + "</span>"
                        "<span style='color:#7A8199; font-family:\"IBM Plex Mono\", monospace; "
                        "font-weight:400;'>" + html.escape(sig['time']) + "</span>"
                        "</div>"
                        + stale_html +
                        "<div style='font-family:\"IBM Plex Mono\", monospace; font-size:0.85rem; "
                        "color:#E8EAF0; line-height:1.6; white-space:pre-wrap;'>" + body_html + "</div>"
                        "</div>"
                    )
                    st.markdown(card_html, unsafe_allow_html=True)
        else:
            st.caption("No signals above the confidence threshold yet.")

        if st.session_state.auto_scanning:
            scan_interval_seconds = st.session_state.scan_interval_minutes * 60
            elapsed = time.time() - st.session_state.last_auto_scan
            remaining = max(int(scan_interval_seconds - elapsed), 0)
            render_countdown(remaining, scan_interval_seconds)

    auto_scan_fragment()

# ---------------- BACKGROUND SCANNER TAB (GitHub Actions control) ----------------
with tab_background:
    st.write(
        "This runs independently of your browser — checks continue on GitHub's "
        "servers even if this tab is closed. Currently fixed to XAU/USD, 15min, "
        "confidence 75+, every 15 minutes (edit scan.yml on GitHub to change these)."
    )

    if "background_scanner_error" not in st.session_state:
        st.session_state.background_scanner_error = None

    status = get_background_scanner_status()

    if status == "active":
        st.success("● Background scanner is ACTIVE — running on schedule")
    elif status == "disabled_manually":
        st.info("○ Background scanner is STOPPED")
    else:
        st.warning("Could not determine status — check your GitHub token and repo settings in Secrets.")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Start Background Scanner", disabled=(status == "active")):
            if set_background_scanner_enabled(True):
                st.rerun()
    with col2:
        if st.button("Stop Background Scanner", disabled=(status == "disabled_manually")):
            if set_background_scanner_enabled(False):
                st.rerun()

    if st.session_state.background_scanner_error:
        st.error(f"GitHub API error: {st.session_state.background_scanner_error}")

    st.caption(
        "Signals found by the background scanner log to the same Google Sheet "
        "and, if configured, send an email — check those rather than this app "
        "for results, since this tab only controls on/off, not live results."
    )

# ---------------- LIVE CHART (full width, below everything) ----------------
st.divider()
header_col, toggle_col = st.columns([4, 1])
with header_col:
    st.markdown("<h3>Live Chart</h3>", unsafe_allow_html=True)
with toggle_col:
    st.session_state.show_chart = st.toggle(
        "Show", value=st.session_state.show_chart, key="chart_toggle"
    )

if st.session_state.show_chart:
    default_symbol = st.session_state.auto_symbol or "XAU/USD"
    guessed = guess_tradingview_symbol(default_symbol)
    if default_symbol.upper() in ("XAU/USD", "XAUUSD"):
        guessed = "FOREXCOM:XAUUSD"
    override = st.text_input(
        "Chart symbol (TradingView format — adjust if wrong exchange)",
        value=st.session_state.chart_symbol_override or guessed,
        key="chart_symbol_input",
        help="e.g. NASDAQ:AAPL, FOREXCOM:XAUUSD, BINANCE:PEPEUSDT",
    )
    st.session_state.chart_symbol_override = override
    render_tradingview_chart(
        override, height=400, interval=INTERVAL_TO_TV_CODE.get(st.session_state.auto_interval, "15")
    )  # ~1/3 of content width at 1280px screen
else:
    st.caption("Chart hidden — toggle on to load it again.")
