"""
Streamlit web app: clean UI for the stock trade analysis tool.

To run locally (optional, for testing): streamlit run app.py
To deploy: push this to GitHub, then deploy on share.streamlit.io
API keys go in Streamlit's Secrets manager, NOT in this file.
"""

import streamlit as st
import requests
import json

# ---- API keys pulled from Streamlit Secrets (set these in the Streamlit Cloud dashboard, not here) ----
TWELVE_DATA_KEY = st.secrets["TWELVE_DATA_KEY"]
CLAUDE_API_KEY = st.secrets["CLAUDE_API_KEY"]

TRADING_STRATEGY = """You are an institutional-grade technical market analyst whose objective is to determine whether a high-probability trading opportunity exists, not to predict future price movement. Base every conclusion solely on objective market evidence and never assume missing information. Before evaluating any trade, identify the current market regime as Strong Uptrend, Strong Downtrend, Weak Trend, Range, Compression, Volatility Expansion, Low Volatility Consolidation, Distribution, Accumulation, or Reversal. Only evaluate strategies that statistically perform well in the detected regime. Never force a recommendation; if evidence is conflicting, incomplete, or weak, return DONT_RECOMMEND. Trend strategies should only be considered in trending markets, while mean reversion strategies should only be considered in ranging or low-volatility environments. Breakout strategies require both volatility and volume expansion, while reversal strategies require clear evidence of momentum exhaustion and structural confirmation. Every recommendation must include the detected strategy, market regime, confidence score, reasoning, invalidation conditions, and an overall risk assessment. Evaluate the following strategies during every analysis. Trend Pullback: identify established higher highs and higher lows (or lower highs and lower lows), wait for a controlled pullback into dynamic support such as VWAP, EMA, or previous structure, then require continuation confirmation before entry. Opening Range Breakout (ORB): only applicable near the market open after the opening range has formed; require a decisive breakout supported by expanding volume and no immediate rejection. Breakout: require price to close beyond major support or resistance with above-average volume, increasing volatility, and preferably a successful retest. Break and Retest: only after a confirmed breakout where price returns to the breakout level, respects it, and resumes the original direction. Trend Continuation: identify a strong trend followed by a brief consolidation before continuation with expanding momentum. Compression Breakout: identify prolonged low volatility, narrowing price range, and declining volume before a sudden expansion confirms direction. Failed Breakout: detect a breakout that immediately loses momentum, returns inside the previous range, and forms reversal confirmation before considering the opposite direction. Evaluate mean reversion and reversal strategies only when market conditions support them. Mean Reversion: identify significant extension from the market average combined with slowing momentum inside an established range, using factors such as RSI extremes, Bollinger Bands, or standard deviation while avoiding strong trending environments. VWAP Reversion: detect significant extensions away from VWAP followed by weakening momentum and declining participation. VWAP Bounce: in trending markets, identify pullbacks into VWAP that are respected before continuation. Support and Resistance Bounce: require repeated historical reactions at a level and a clear rejection before entry. Liquidity Sweep: identify price briefly taking previous highs or lows before immediately reversing back into range, indicating stop hunting. Fair Value Gap (FVG): identify impulsive moves creating price imbalances, then require retracement into the imbalance before continuation. Order Block: identify fresh institutional supply or demand zones where price revisits and rejects decisively. Volume Profile: evaluate reactions around the Point of Control (POC), High Volume Nodes (HVN), Low Volume Nodes (LVN), and Value Area High/Low, giving higher weight when these align with market structure. Exhaustion Reversal: identify extended directional moves showing divergence, climax volume, slowing momentum, and confirmed structural reversal before considering a counter-trend trade. Calculate a confidence score from 0-100 using only objective evidence. Increase confidence when multiple independent confirmations agree, including higher timeframe trend alignment, market structure, volume expansion, volatility expansion, VWAP agreement, support and resistance confluence, liquidity confirmation, volume profile confluence, healthy pullbacks, and strong continuation candles. Reduce confidence for conflicting signals, weak volume, poor follow-through, repeated tests of key levels, excessive volatility without direction, low liquidity, major scheduled news, or deteriorating market structure. Confidence should represent the statistical quality of the setup rather than certainty of outcome. Use the following scale: 95-100 Exceptional setup with near-perfect confluence; 90-94 Excellent setup with only minor conflicting evidence; 80-89 Good setup with strong statistical edge; 70-79 Moderate edge requiring disciplined risk management; 60-69 Weak edge requiring additional confirmation; 50-59 Very weak setup with significant uncertainty; below 50 automatically returns DONT_RECOMMEND. Also return DONT_RECOMMEND whenever no strategy appropriately matches the detected market regime, confirmation signals are insufficient, multiple strategies conflict without a clear winner, market structure is unclear, or the estimated risk-to-reward ratio is below 2:1 unless the strategy historically justifies otherwise. Never recommend a trade simply because price may move; only recommend trades when objective evidence strongly supports a defined strategy with measurable statistical edge.

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

Keep your entire response under 600 words. Do not narrate your reasoning process step-by-step or show your analysis of each strategy candidate — go straight to the final structured output listed above. Only include brief supporting reasoning inline within each field (e.g. one short clause for why that stop-loss level), not separate paragraphs."""


def get_intraday_data(symbol, interval):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": 100,
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
    """Pull recent daily candles for higher-timeframe context."""
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


def call_claude(messages):
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1500,
        "system": TRADING_STRATEGY,
        "messages": messages,
    }
    response = requests.post(url, headers=headers, json=body)
    result = response.json()

    if "content" not in result:
        return None, result

    return result["content"][0]["text"], None


# ---------------- UI starts here ----------------

st.set_page_config(page_title="MissionToMars", page_icon="📈", layout="centered")
st.title("📈 MissionToMars")
st.caption("Short-term technical analysis powered by Claude")

# Keep conversation history across reruns within a session
if "conversation" not in st.session_state:
    st.session_state.conversation = []
if "scanned" not in st.session_state:
    st.session_state.scanned = False

with st.form("scan_form"):
    col1, col2 = st.columns(2)
    with col1:
        symbol = st.text_input("Stock symbol", value="AAPL")
    with col2:
        interval = st.selectbox(
            "Interval", ["5min", "15min", "30min", "1h"], index=1
        )
    run_scan = st.form_submit_button("Run Scan")

if run_scan:
    with st.spinner(f"Fetching {interval} data for {symbol}..."):
        price_data, error = get_intraday_data(symbol, interval)

    if price_data is None:
        st.error(f"Could not fetch price data: {error}")
    else:
        price_data_text = json.dumps(price_data, indent=2)

        with st.spinner("Fetching daily context..."):
            daily_data = get_daily_context(symbol)

        daily_context_text = (
            f"\n\nHigher timeframe context — last 20 daily candles:\n{json.dumps(daily_data, indent=2)}"
            if daily_data else "\n\n(Daily context unavailable for this symbol.)"
        )

        st.session_state.conversation = [
            {
                "role": "user",
                "content": (
                    f"Here is {symbol} price data at {interval} candles, "
                    f"most recent {len(price_data)} candles, oldest to newest:\n\n"
                    f"{price_data_text}"
                    f"{daily_context_text}\n\n"
                    "Analyze this for a short-term trade using the exact "
                    "output format specified. Use the daily context to judge "
                    "where the intraday range sits relative to the broader trend."
                ),
            }
        ]
        with st.spinner("Analyzing..."):
            analysis, error = call_claude(st.session_state.conversation)

        if analysis is None:
            st.error(f"Claude API error: {error}")
        else:
            st.session_state.conversation.append(
                {"role": "assistant", "content": analysis}
            )
            st.session_state.scanned = True

# Show the conversation so far (scan result + any follow-ups)
if st.session_state.scanned:
    st.divider()
    for msg in st.session_state.conversation:
        if msg["role"] == "assistant":
            with st.chat_message("assistant"):
                st.markdown(msg["content"])
        elif msg["role"] == "user" and st.session_state.conversation.index(msg) != 0:
            # Skip showing the first message (the raw data dump), show only
            # actual follow-up questions the user typed
            with st.chat_message("user"):
                st.markdown(msg["content"])

    question = st.chat_input("Ask a follow-up question about this analysis")
    if question:
        st.session_state.conversation.append({"role": "user", "content": question})
        with st.spinner("Thinking..."):
            answer, error = call_claude(st.session_state.conversation)
        if answer:
            st.session_state.conversation.append(
                {"role": "assistant", "content": answer}
            )
            st.rerun()
        else:
            st.error(f"Claude API error: {error}")
