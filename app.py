import datetime
import numpy as np
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pytz
import streamlit as st
import yfinance as yf

# ---------------------------------------------------------
# Page Configuration & Styling
# ---------------------------------------------------------
st.set_page_config(
    page_title="SPY Intraday Spread Tracker", page_icon="📈", layout="wide"
)

st.markdown(
    """
    <style>
    .metric-card {
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 15px;
        background-color: rgba(255, 255, 255, 0.03);
        margin-bottom: 10px;
    }
    .signal-bull {
        color: #00e676;
        font-weight: 700;
        font-size: 1.25rem;
    }
    .signal-bear {
        color: #ff5252;
        font-weight: 700;
        font-size: 1.25rem;
    }
    .signal-neutral {
        color: #b0bec5;
        font-weight: 600;
        font-size: 1.25rem;
    }
    </style>
""",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------
# Signal Generation Engine
# ---------------------------------------------------------
def generate_intraday_signals(
    df: pd.DataFrame,
    fast_ema: int = 9,
    slow_ema: int = 21,
    atr_period: int = 14,
    rvol_window: int = 20,
    adx_period: int = 14,
    adx_threshold: float = 25.0
) -> pd.DataFrame:
    """Computes dynamic multi-factor entry thresholds for intraday directional debit spreads."""
    df = df.copy()

    # 1. EMAs and Normalized Delta
    df["ema_fast"] = df["close"].ewm(span=fast_ema, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=slow_ema, adjust=False).mean()

    # 2. ATR Calculation
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(window=atr_period).mean()

    # Normalize EMA separation by ATR
    df["ema_spread_norm"] = (df["ema_fast"] - df["ema_slow"]) / df["atr"]

    # 3. Normalized Distance from VWAP
    df["vwap_dist_norm"] = (df["close"] - df["vwap"]) / df["atr"]

    # 4. Volume Validation (RVOL)
    df["vol_ma"] = df["volume"].rolling(window=rvol_window).mean()
    df["rvol"] = df["volume"] / df["vol_ma"]

    # 5. ADX and DMI Calculation
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=adx_period)
    
    adx_col = f"ADX_{adx_period}"
    dmp_col = f"DMP_{adx_period}"
    dmn_col = f"DMN_{adx_period}"
    
    if adx_df is not None:
        df = pd.concat([df, adx_df], axis=1)
    else:
        # Fallback if calculation fails on limited data
        df[adx_col], df[dmp_col], df[dmn_col] = 0.0, 0.0, 0.0

    # 6. Session Phase Filtering
    time = df.index.time
    t_start_am = pd.to_datetime("09:50:00").time()
    t_end_am = pd.to_datetime("11:30:00").time()
    t_start_pm = pd.to_datetime("13:45:00").time()
    t_end_pm = pd.to_datetime("15:15:00").time()

    session_active = ((time >= t_start_am) & (time <= t_end_am)) | (
        (time >= t_start_pm) & (time <= t_end_pm)
    )

    # Signal Threshold Logic with ADX & DMI filters
    call_spread_trigger = (
        session_active
        & (df["ema_spread_norm"] > 0.15)
        & (df["vwap_dist_norm"] >= 0.20)
        & (df["vwap_dist_norm"] <= 1.10)
        & (df["rvol"] >= 1.30)
        & (df["close"] > df["open"])
        & (df[adx_col] >= adx_threshold)
        & (df[dmp_col] > df[dmn_col])
    )

    put_spread_trigger = (
        session_active
        & (df["ema_spread_norm"] < -0.15)
        & (df["vwap_dist_norm"] <= -0.20)
        & (df["vwap_dist_norm"] >= -1.10)
        & (df["rvol"] >= 1.30)
        & (df["close"] < df["open"])
        & (df[adx_col] >= adx_threshold)
        & (df[dmn_col] > df[dmp_col])
    )

    df["signal"] = 0
    df.loc[call_spread_trigger, "signal"] = 1
    df.loc[put_spread_trigger, "signal"] = -1

    # Filter out consecutive duplicate signals (take initial impulse only)
    df["entry_signal"] = np.where(
        (df["signal"] != 0) & (df["signal"] != df["signal"].shift(1)),
        df["signal"],
        0,
    )

    return df


# ---------------------------------------------------------
# Market Data Fetcher & VWAP Assembler
# ---------------------------------------------------------
@st.cache_data(ttl=60)
def fetch_spy_intraday_data():
    """Fetches intraday 5-minute bars for SPY and computes cumulative day-anchored VWAP."""
    ticker = yf.Ticker("SPY")
    df = ticker.history(period="5d", interval="5m")

    if df.empty:
        return pd.DataFrame()

    df.columns = [c.lower() for c in df.columns]

    # Localize index to US/Eastern using IANA timezone "America/New_York"
    if df.index.tz is None:
        df.index = (
            df.index.tz_localize("UTC")
            .tz_convert("America/New_York")
        )
    else:
        df.index = df.index.tz_convert("America/New_York")

    # Filter for standard market hours (9:30 AM to 4:00 PM ET)
    df = df.between_time("09:30", "16:00").copy()

    # Anchor VWAP to each session date
    df["date"] = df.index.date
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    df["cum_vp"] = (typical_price * df["volume"]).groupby(df["date"]).cumsum()
    df["cum_vol"] = (df["volume"]).groupby(df["date"]).cumsum()
    df["vwap"] = df["cum_vp"] / df["cum_vol"]
    df.drop(columns=["date", "cum_vp", "cum_vol"], inplace=True)

    return df


def get_spread_recommendation(
    current_price: float, signal: int, spread_width: float = 2.0
):
    """Calculates strike selections for vertical debit spreads."""
    if signal == 1:
        long_strike = np.floor(current_price)
        short_strike = long_strike + spread_width
        return {
            "type": "CALL DEBIT SPREAD (Bullish)",
            "long_leg": f"Buy ${long_strike:.0f} Call",
            "short_leg": f"Sell ${short_strike:.0f} Call",
            "target": f"SPY > ${short_strike:.2f} by EOD",
            "risk_profile": "Defined Risk (Net Debit Paid)",
        }
    elif signal == -1:
        long_strike = np.ceil(current_price)
        short_strike = long_strike - spread_width
        return {
            "type": "PUT DEBIT SPREAD (Bearish)",
            "long_leg": f"Buy ${long_strike:.0f} Put",
            "short_leg": f"Sell ${short_strike:.0f} Put",
            "target": f"SPY < ${short_strike:.2f} by EOD",
            "risk_profile": "Defined Risk (Net Debit Paid)",
        }
    return None


# ---------------------------------------------------------
# Main UI App
# ---------------------------------------------------------
st.title("🎯 SPY Dynamic Intraday Spread Tracker")
st.caption(
    "Multi-factor signal scanner analyzing normalized EMA deltas, VWAP displacement, RVOL expansion, and ADX trend strength."
)

# Sidebar Parameters
st.sidebar.header("⚙️ Strategy Parameters")
fast_ema = st.sidebar.slider("Fast EMA", 5, 20, 9)
slow_ema = st.sidebar.slider("Slow EMA", 15, 50, 21)
atr_len = st.sidebar.slider("ATR Period", 7, 28, 14)
rvol_win = st.sidebar.slider("RVOL Baseline Window", 10, 40, 20)
adx_len = st.sidebar.slider("ADX Period", 7, 28, 14)
adx_thresh = st.sidebar.slider("ADX Threshold", 15.0, 40.0, 25.0, step=1.0)
spread_width = st.sidebar.selectbox("Spread Width ($)", [1.0, 2.0, 3.0, 5.0], index=1)

if st.sidebar.button("🔄 Force Refresh"):
    st.cache_data.clear()
    st.rerun()

# Fetch & Process
raw_df = fetch_spy_intraday_data()

if raw_df.empty:
    st.error(
        "Unable to retrieve intraday market data. Verify connection to data feed."
    )
    st.stop()

processed_df = generate_intraday_signals(
    raw_df,
    fast_ema=fast_ema,
    slow_ema=slow_ema,
    atr_period=atr_len,
    rvol_window=rvol_win,
    adx_period=adx_len,
    adx_threshold=adx_thresh
)

latest = processed_df.iloc[-1]
current_time = latest.name.strftime("%Y-%m-%d %H:%M:%S ET")

# Top KPI Metric Cards (Expanded to include ADX)
col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("SPY Last", f"${latest['close']:.2f}")
col2.metric("Intraday VWAP", f"${latest['vwap']:.2f}")
col3.metric("ATR (14)", f"${latest['atr']:.2f}")
col4.metric(
    "EMA Spread",
    f"{latest['ema_spread_norm']:.2f}σ",
    delta=f"{(latest['ema_fast'] - latest['ema_slow']):.2f}",
)
col5.metric("RVOL", f"{latest['rvol']:.2f}x")

# Dynamic ADX Metric Color
adx_val = latest.get(f"ADX_{adx_len}", 0)
adx_color = "normal" if adx_val >= adx_thresh else "off"
col6.metric("ADX (Strength)", f"{adx_val:.1f}")

st.markdown("---")

# Signal Banner & Active Strategy Card
sig_val = int(latest["signal"])
spread_info = get_spread_recommendation(
    latest["close"], sig_val, spread_width=spread_width
)

banner_col, details_col = st.columns([1.2, 2])

with banner_col:
    st.markdown("### 📡 Real-Time Indicator State")
    if sig_val == 1:
        st.markdown(
            '<div class="signal-bull">🟢 CALL DEBIT SPREAD TRIGGERED</div>',
            unsafe_allow_html=True,
        )
    elif sig_val == -1:
        st.markdown(
            '<div class="signal-bear">🔴 PUT DEBIT SPREAD TRIGGERED</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="signal-neutral">⚪ MONITORING (NO TRIGGER)</div>',
            unsafe_allow_html=True,
        )

    # Session gate verification
    now_et = latest.name.time()
    in_am = (
        pd.to_datetime("09:50:00").time()
        <= now_et
        <= pd.to_datetime("11:30:00").time()
    )
    in_pm = (
        pd.to_datetime("13:45:00").time()
        <= now_et
        <= pd.to_datetime("15:15:00").time()
    )
    session_status = "Active Window ✅" if (in_am or in_pm) else "Outside Filter ⏳"

    st.write(f"**Session State:** {session_status}")
    st.write(f"**As of:** `{current_time}`")

with details_col:
    st.markdown("### 📋 Suggested Structure")
    if spread_info:
        sc1, sc2, sc3 = st.columns(3)
        sc1.info(f"**Long Leg:**\n{spread_info['long_leg']}")
        sc2.info(f"**Short Leg:**\n{spread_info['short_leg']}")
        sc3.success(f"**Target:**\n{spread_info['target']}")
    else:
        st.write(
            "Waiting for confirmation thresholds:\n"
            "- EMA Spread Norm: `> 0.15` (Call) or `< -0.15` (Put)\n"
            "- VWAP Distance: `0.20 to 1.10` (Call) or `-0.20 to -1.10` (Put)\n"
            "- RVOL: `>= 1.30x`\n"
            f"- ADX Trend Strength: `>= {adx_thresh}`"
        )

st.markdown("---")

# Charting
st.subheader("📊 Intraday Price Action & Multi-Factor Indicators")

# Plot only current session
today_date = latest.name.date()
plot_df = processed_df[processed_df.index.date == today_date].copy()

if plot_df.empty:
    plot_df = processed_df.tail(78).copy()  # Fallback to last ~1 trading session

fig = make_subplots(
    rows=3,
    cols=1,
    shared_xaxes=True,
    vertical_spacing=0.04,
    row_heights=[0.5, 0.25, 0.25],
    subplot_titles=("SPY Candlesticks & Factor Overlays", "Relative Volume (RVOL)", "ADX & DMI (Trend Strength)"),
)

# Row 1: Candlesticks + VWAP + EMAs
fig.add_trace(
    go.Candlestick(
        x=plot_df.index,
        open=plot_df["open"],
        high=plot_df["high"],
        low=plot_df["low"],
        close=plot_df["close"],
        name="Price",
    ),
    row=1,
    col=1,
)

fig.add_trace(
    go.Scatter(
        x=plot_df.index,
        y=plot_df["vwap"],
        line=dict(color="#ffa726", width=1.5),
        name="VWAP",
    ),
    row=1,
    col=1,
)

fig.add_trace(
    go.Scatter(
        x=plot_df.index,
        y=plot_df["ema_fast"],
        line=dict(color="#29b6f6", width=1),
        name=f"EMA {fast_ema}",
    ),
    row=1,
    col=1,
)

fig.add_trace(
    go.Scatter(
        x=plot_df.index,
        y=plot_df["ema_slow"],
        line=dict(color="#ab47bc", width=1),
        name=f"EMA {slow_ema}",
    ),
    row=1,
    col=1,
)

# Markers for Entry Impulse Signals
bull_entries = plot_df[plot_df["entry_signal"] == 1]
bear_entries = plot_df[plot_df["entry_signal"] == -1]

if not bull_entries.empty:
    fig.add_trace(
        go.Scatter(
            x=bull_entries.index,
            y=bull_entries["low"] - (bull_entries["atr"] * 0.5),
            mode="markers",
            marker=dict(symbol="triangle-up", size=11, color="#00e676"),
            name="Bull Entry Signal",
        ),
        row=1,
        col=1,
    )

if not bear_entries.empty:
    fig.add_trace(
        go.Scatter(
            x=bear_entries.index,
            y=bear_entries["high"] + (bear_entries["atr"] * 0.5),
            mode="markers",
            marker=dict(symbol="triangle-down", size=11, color="#ff5252"),
            name="Bear Entry Signal",
        ),
        row=1,
        col=1,
    )

# Row 2: RVOL
fig.add_trace(
    go.Bar(
        x=plot_df.index,
        y=plot_df["rvol"],
        name="RVOL",
        marker_color=np.where(plot_df["rvol"] >= 1.3, "#00e676", "#78909c"),
    ),
    row=2,
    col=1,
)

fig.add_hline(y=1.3, line_dash="dot", line_color="#ffca28", row=2, col=1)

# Row 3: ADX & DMI
adx_col = f"ADX_{adx_len}"
dmp_col = f"DMP_{adx_len}"
dmn_col = f"DMN_{adx_len}"

if adx_col in plot_df.columns:
    fig.add_trace(
        go.Scatter(
            x=plot_df.index,
            y=plot_df[adx_col],
            line=dict(color="#FFD700", width=2),
            name="ADX",
        ),
        row=3,
        col=1,
    )
    
    fig.add_trace(
        go.Scatter(
            x=plot_df.index,
            y=plot_df[dmp_col],
            line=dict(color="#00e676", width=1.2),
            name="+DI",
        ),
        row=3,
        col=1,
    )
    
    fig.add_trace(
        go.Scatter(
            x=plot_df.index,
            y=plot_df[dmn_col],
            line=dict(color="#ff5252", width=1.2),
            name="-DI",
        ),
        row=3,
        col=1,
    )

    fig.add_hline(
        y=adx_thresh, 
        line_dash="dot", 
        line_color="#b0bec5", 
        row=3, 
        col=1, 
        annotation_text=f"Threshold ({adx_thresh})", 
        annotation_position="bottom right"
    )

fig.update_layout(
    height=850,
    margin=dict(l=20, r=20, t=30, b=20),
    xaxis_rangeslider_visible=False,
    template="plotly_dark",
    hovermode="x unified"
)

st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------
# Signal History Audit Table
# ---------------------------------------------------------
st.subheader("📜 Today's Signal Impulses")
audit_cols = [
    "close", "vwap", "ema_spread_norm", "vwap_dist_norm", "rvol", "entry_signal"
]
if adx_col in plot_df.columns:
    audit_cols.append(adx_col)

signal_log = plot_df[plot_df["entry_signal"] != 0][audit_cols].copy()

if not signal_log.empty:
    signal_log["Trigger"] = signal_log["entry_signal"].apply(
        lambda x: "🟢 CALL SPREAD" if x == 1 else "🔴 PUT SPREAD"
    )
    signal_log.drop(columns=["entry_signal"], inplace=True)
    
    # Rename ADX column for cleaner display
    if adx_col in signal_log.columns:
        signal_log.rename(columns={adx_col: "ADX"}, inplace=True)
        format_dict = {
            "close": "${:.2f}",
            "vwap": "${:.2f}",
            "ema_spread_norm": "{:.2f}σ",
            "vwap_dist_norm": "{:.2f}σ",
            "rvol": "{:.2f}x",
            "ADX": "{:.1f}"
        }
    else:
        format_dict = {
            "close": "${:.2f}",
            "vwap": "${:.2f}",
            "ema_spread_norm": "{:.2f}σ",
            "vwap_dist_norm": "{:.2f}σ",
            "rvol": "{:.2f}x",
        }

    st.dataframe(
        signal_log.sort_index(ascending=False).style.format(format_dict),
        use_container_width=True,
    )
else:
    st.info(
        "No verified breakout entry impulses generated yet in today's active trading windows."
    )
