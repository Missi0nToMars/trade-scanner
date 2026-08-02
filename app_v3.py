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

# ---- API keys pulled from Streamlit Secrets ----
TWELVE_DATA_KEY = st.secrets["TWELVE_DATA_KEY"]
CLAUDE_API_KEY = st.secrets["CLAUDE_API_KEY"]

SCAN_INTERVAL_SECONDS = 5 * 60  # 5 minutes
DEFAULT_CONFIDENCE_THRESHOLD = 75

# ---------------- Full strategy prompt (used for manual scans) ----------------

TRADING_STRATEGY_FULL = """You are an institutional-grade technical market analyst whose objective is to determine whether a high-probability trading opportunity exists, not to predict future price movement. Base every conclusion solely on objective market evidence and never assume missing information. Before evaluating any trade, identify the current market regime as Strong Uptrend, Strong Downtrend, Weak Trend, Range, Compression, Volatility Expansion, Low Volatility Consolidation, Distribution, Accumulation, or Reversal. Only evaluate strategies that statistically perform well in the detected regime. Never force a recommendation; if evidence is conflicting, incomplete, or weak, return DONT_RECOMMEND. Trend strategies should only be considered in trending markets, while mean reversion strategies should only be considered in ranging or low-volatility environments. Breakout strategies require both volatility and volume expansion, while reversal strategies require clear evidence of momentum exhaustion and structural confirmation. Every recommendation must include the detected strategy, market regime, confidence score, reasoning, invalidation conditions, and an overall risk assessment. Evaluate the following strategies during every analysis. Trend Pullback: identify established higher highs and higher lows (or lower highs and lower lows), wait for a controlled pullback into dynamic support such as VWAP, EMA, or previous structure, then require continuation confirmation before entry. Opening Range Breakout (ORB): only applicable near the market open after the opening range has formed; require a decisive breakout supported by expanding volume and no immediate rejection. Breakout: require price to close beyond major support or resistance with above-average volume, increasing volatility, and preferably a successful retest. Break and Retest: only after a confirmed breakout where price returns to the breakout level, respects it, and resumes the original direction. Trend Continuation: identify a strong trend followed by a brief consolidation before continuation with expanding momentum. Compression Breakout: identify prolonged low volatility, narrowing price range, and declining volume before a sudden expansion confirms direction. Failed Breakout: detect a breakout that immediately loses momentum, returns inside the previous range, and forms reversal confirmation before considering the opposite direction. Evaluate mean reversion and reversal strategies only when market conditions support them. Mean Reversion: identify significant extension from the market average combined with slowing momentum inside an established range, using factors such as RSI extremes, Bollinger Bands, or standard deviation while avoiding strong trending environments. VWAP Reversion: detect significant extensions away from VWAP followed by weakening momentum and declining participation. VWAP Bounce: in trending markets, identify pullbacks into VWAP that are respected before continuation. Support and Resistance Bounce: require repeated historical reactions at a level and a clear rejection before entry. Liquidity Sweep: identify price briefly taking previous highs or lows before immediately reversing back into range, indicating stop hunting. Fair Value Gap (FVG): identify impulsive moves creating price imbalances, then require retracement into the imbalance before continuation. Order Block: identify fresh institutional supply or demand zones where price revisits and rejects decisively. Volume Profile: evaluate reactions around the Point of Control (POC), High Volume Nodes (HVN), Low Volume Nodes (LVN), and Value Area High/Low, giving higher weight when these align with market structure. Exhaustion Reversal: identify extended directional moves showing divergence, climax volume, slowing momentum, and confirmed structural reversal before considering a counter-trend trade. Calculate a confidence score from 0-100 using only objective evidence. Increase confidence when multiple independent confirmations agree, including higher timeframe trend alignment, market structure, volume expansion, volatility expansion, VWAP agreement, support and resistance confluence, liquidity confirmation, volume profile confluence, healthy pullbacks, and strong continuation candles. Reduce confidence for conflicting signals, weak volume, poor follow-through, repeated tests of key levels, excessive volatility without direction, low liquidity, major scheduled news, or deteriorating market structure. Confidence should represent the statistical quality of the setup rather than certainty of outcome. Use the following scale: 95-100 Exceptional setup with near-perfect confluence; 90-94 Excellent setup with only minor conflicting evidence; 80-89 Good setup with strong statistical edge; 70-79 Moderate edge requiring disciplined risk management; 60-69 Weak edge requiring additional confirmation; 50-59 Very weak setup with significant uncertainty; below 50 automatically returns DONT_RECOMMEND. Also return DONT_RECOMMEND whenever no strategy appropriately matches the detected market regime, confirmation signals are insufficient, multiple strategies conflict without a clear winner, market structure is unclear, or the estimated risk-to-reward ratio is below 2:1 unless the strategy historically justifies otherwise. Never recommend a trade simply because price may move; only recommend trades when objective evidence strongly supports a defined strategy with measurable statistical edge.

For every analysis, structure your output exactly as follows:
- Detected Strategy
- Market Regime
- Confidence Score (0-100)
- Entry Price
- Stop Loss Price (with reasoning — e.g. below structure, below ATR-based buffer)
- Take Profit Price (with reasoning, and resulting risk:reward ratio)
- Invalidation Conditions (what would prove this setup wrong)
- Risk Assessment (1-2 sentences)

If confidence is below 50, or no strategy fits, output DONT_RECOMMEND with a brief reason instead of the above fields.

After the initial scan, the user may ask follow-up questions about the analysis. Answer those using the same price data and strategy framework, staying consistent with your original assessment unless the user points out something you missed.

Volume-Free Instrument Exception: For instruments where volume data is unavailable or reported as N/A (such as spot forex and spot commodities like XAU/USD), do not disqualify a setup on volume grounds alone. Instead, substitute the following as confirmation evidence in place of volume: the sharpness and speed of price reactions at key levels (a fast, decisive move away from a level counts as a proxy for conviction), the number of times a level has been tested and defended, the size of the range relative to recent average range (range expansion serves as a proxy for volatility expansion), and candle body-to-wick ratios at turning points (a small wick with a strong body close suggests conviction; a long wick with a weak close suggests rejection). Confidence scoring should still apply the same thresholds, but volume-dependent strategies (Breakout, ORB, Compression Breakout, Trend Continuation, Exhaustion Reversal) may now be evaluated using these substitute signals instead of requiring literal volume figures.

Higher Timeframe Context: When daily candle data is provided alongside intraday data, use it to judge where the current intraday range sits relative to the broader trend, recent swing highs/lows, and overall market structure. Weight setups more favorably when the intraday signal aligns with the daily trend direction, and more cautiously when it conflicts with it (e.g. a bullish intraday setup appearing during a daily downtrend warrants a lower confidence score or added caution in the risk assessment).

Keep your entire response under 600 words. Do not narrate your reasoning process step-by-step or show your analysis of each strategy candidate — go straight to the final structured output listed above. Only include brief supporting reasoning inline within each field (e.g. one short clause for why that stop-loss level), not separate paragraphs.

Precision Warning for Low-Priced Instruments: When a price is below 0.01 (common in meme coins and micro-cap tokens), write every price value using scientific notation (e.g. 2.9257e-6) instead of long decimal strings, and double-check the exponent matches the actual price scale from the provided data before finalizing any entry, stop-loss, or take-profit figure. Never shift the decimal point when copying a price value from the input data into your output — verify each output price against the input data's actual magnitude before responding.

Time and Frequency Constraint: All trade ideas must be structured for a maximum hold time of 1.5 hours from entry, with take-profit targets realistically reachable within that window based on the instrument's recent volatility and typical move speed — not targets that assume a multi-hour or multi-day move. Additionally, be highly selective — the trader is aiming for roughly 3 well-considered trades per day, not a high-frequency stream of setups. Only recommend a trade when the setup is genuinely strong; if a marginal or mediocre setup exists, or if no plausible path to target exists within 1.5 hours, return DONT_RECOMMEND rather than lowering the bar. Favor quality and patience over quantity."""

# ---------------- Trimmed strategy prompt (used for automated scanning) ----------------

TRADING_STRATEGY_SCAN = """You are an institutional-grade technical market analyst. Apply the same strategy framework and confidence scoring rules as a full analysis (regime detection, strategy matching, volume-free instrument exception, higher timeframe context weighting), but output ONLY the following structured fields, nothing else, no extra commentary, no reasoning paragraphs:

CONFIDENCE: <0-100>
STRATEGY: <strategy name, or NONE if confidence is below 50>
ENTRY: <price, or N/A>
STOP_LOSS: <price, or N/A>
TAKE_PROFIT: <price, or N/A>
INVALIDATION: <one short line describing what proves the setup wrong, or N/A>

Do not include a market regime explanation, risk assessment paragraph, or any narration. Just the six lines above, exactly as labeled.

Precision Warning for Low-Priced Instruments: When a price is below 0.01 (common in meme coins and micro-cap tokens), write every price value using scientific notation (e.g. 2.9257e-6) instead of long decimal strings, and double-check the exponent matches the actual price scale from the provided data before finalizing any entry, stop-loss, or take-profit figure. Never shift the decimal point when copying a price value from the input data into your output — verify each output price against the input data's actual magnitude before responding.

Time and Frequency Constraint: All trade ideas must be structured for a maximum hold time of 1.5 hours from entry, with take-profit targets realistically reachable within that window based on the instrument's recent volatility and typical move speed. Be highly selective — the trader is aiming for roughly 3 well-considered trades per day. Only report CONFIDENCE at 50+ when the setup is genuinely strong and reachable within 1.5 hours; otherwise report CONFIDENCE below 50 with STRATEGY: NONE."""


def get_intraday_data(symbol, interval, size=100):
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
    return (
        f"Here is {symbol} price data at {interval} candles, "
        f"most recent {len(price_data)} candles, oldest to newest:\n\n"
        f"{price_data_text}"
        f"{daily_context_text}\n\n"
        f"{instruction}"
    )


def parse_confidence(scan_text):
    match = re.search(r"CONFIDENCE:\s*(\d+)", scan_text)
    return int(match.group(1)) if match else None


# ---------------- UI starts here ----------------

st.set_page_config(page_title="MissionToMars", page_icon="📈", layout="centered")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Mono:wght@400;600&family=Inter:wght@400;500&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

.stApp {
    background: radial-gradient(circle at 20% 0%, #131829 0%, #0B0E1A 55%);
    color: #E8EAF0;
}

h1, h2, h3 {
    font-family: 'Space Grotesk', sans-serif !important;
    letter-spacing: 0.02em;
}

h1 {
    font-weight: 700 !important;
    color: #F5F6FA !important;
}

.stCaption, [data-testid="stCaptionContainer"] {
    color: #7A8199 !important;
    font-family: 'IBM Plex Mono', monospace !important;
    letter-spacing: 0.03em;
}

/* Buttons */
.stButton > button {
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 600;
    letter-spacing: 0.03em;
    border-radius: 4px;
    border: 1px solid #2A3050;
    background-color: #131829;
    color: #E8EAF0;
    transition: border-color 0.15s ease;
}
.stButton > button:hover {
    border-color: #FFA630;
    color: #FFA630;
}

/* Tabs */
.stTabs [data-baseweb="tab"] {
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: #7A8199;
}
.stTabs [aria-selected="true"] {
    color: #FFA630 !important;
}

/* Text inputs and selects */
.stTextInput input, .stSelectbox div[data-baseweb="select"] {
    font-family: 'IBM Plex Mono', monospace;
    background-color: #131829 !important;
    border-color: #2A3050 !important;
    color: #E8EAF0 !important;
}

/* Slider */
.stSlider [data-baseweb="slider"] {
    color: #FFA630;
}

/* Expander (fallback, in case still used) */
.streamlit-expanderHeader {
    font-family: 'IBM Plex Mono', monospace;
    background-color: #131829 !important;
    border-radius: 4px;
}

hr, [data-testid="stDivider"] {
    border-color: #2A3050 !important;
}
</style>
""", unsafe_allow_html=True)

st.markdown("<h1 style='margin-bottom:0;'>📡 MissionToMars</h1>", unsafe_allow_html=True)
st.markdown(
    "<p style='font-family:IBM Plex Mono, monospace; color:#7A8199; "
    "letter-spacing:0.05em; margin-top:0;'>SHORT-TERM SIGNAL CONSOLE — POWERED BY CLAUDE</p>",
    unsafe_allow_html=True,
)

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
    st.session_state.auto_symbol = "AAPL"
if "confidence_threshold" not in st.session_state:
    st.session_state.confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD

tab_manual, tab_auto = st.tabs(["Manual Scan", "Auto Scanning"])

# ---------------- MANUAL SCAN TAB ----------------
with tab_manual:
    with st.form("scan_form"):
        col1, col2 = st.columns(2)
        with col1:
            symbol = st.text_input("Stock symbol", value="AAPL")
        with col2:
            interval = st.selectbox("Interval", ["5min", "15min", "30min", "1h"], index=1)
        run_scan = st.form_submit_button("Run Scan")

    if run_scan:
        with st.spinner(f"Fetching {interval} data for {symbol}..."):
            price_data, error = get_intraday_data(symbol, interval)

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
    st.write(f"Checks every {SCAN_INTERVAL_SECONDS // 60} minutes.")

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

    status = st.empty()

    if st.session_state.auto_scanning:
        status.success(f"Scanning {st.session_state.auto_symbol} — tab must stay open to keep checking.")

        now = time.time()
        seconds_since_last = now - st.session_state.last_auto_scan

        if seconds_since_last >= SCAN_INTERVAL_SECONDS:
            with status:
                with st.spinner(f"Checking {st.session_state.auto_symbol}..."):
                    price_data, error = get_intraday_data(st.session_state.auto_symbol, "15min")
                    if price_data:
                        daily_data = get_daily_context(st.session_state.auto_symbol)
                        instruction = "Give a scan reading in the exact trimmed format specified."
                        content = build_data_message(
                            st.session_state.auto_symbol, "15min", price_data, daily_data, instruction
                        )
                        scan_text, error = call_claude(
                            TRADING_STRATEGY_SCAN, [{"role": "user", "content": content}], max_tokens=200
                        )
                        if scan_text:
                            confidence = parse_confidence(scan_text)
                            if confidence is not None and confidence >= st.session_state.confidence_threshold:
                                st.session_state.signals.insert(0, {
                                    "time": datetime.now().strftime("%H:%M:%S"),
                                    "symbol": st.session_state.auto_symbol,
                                    "text": scan_text,
                                })
            st.session_state.last_auto_scan = time.time()

    # Render signals BEFORE the rerun trigger below, so new signals actually show up
    if st.session_state.signals:
        st.markdown(
            "<h3 style='margin-top:1.5rem;'>Signals Found</h3>", unsafe_allow_html=True
        )
        for sig in st.session_state.signals:
            confidence = parse_confidence(sig["text"])
            if confidence is not None and confidence >= 85:
                tier_color, tier_label = "#5FBF77", "STRONG"
            elif confidence is not None and confidence >= 75:
                tier_color, tier_label = "#FFA630", "MODERATE"
            else:
                tier_color, tier_label = "#4FD1C5", "SIGNAL"

            body_html = sig["text"].replace("\n", "<br>")
            st.markdown(
                f"""
                <div style='background-color:#131829; border-left:4px solid {tier_color};
                            border-radius:4px; padding:14px 18px; margin-bottom:12px;'>
                    <div style='display:flex; justify-content:space-between; align-items:center;
                                font-family:"Space Grotesk", sans-serif; font-weight:600;
                                color:{tier_color}; font-size:0.85rem; letter-spacing:0.05em;
                                margin-bottom:8px;'>
                        <span>{tier_label} — {sig['symbol']}</span>
                        <span style='color:#7A8199; font-family:"IBM Plex Mono", monospace;
                                     font-weight:400;'>{sig['time']}</span>
                    </div>
                    <div style='font-family:"IBM Plex Mono", monospace; font-size:0.85rem;
                                color:#E8EAF0; line-height:1.6; white-space:pre-wrap;'>{body_html}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.caption("No signals above the confidence threshold yet.")

    # Trigger the next check — this happens LAST so everything above has
    # already rendered on screen before the page restarts
    if st.session_state.auto_scanning:
        remaining = int(SCAN_INTERVAL_SECONDS - (time.time() - st.session_state.last_auto_scan))
        status.info(f"Watching {st.session_state.auto_symbol} — next check in ~{max(remaining, 0)}s")
        time.sleep(3)
        st.rerun()
