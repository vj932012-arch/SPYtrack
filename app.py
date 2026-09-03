# SPY 0 DTE Options Tracker with Interactive Plotly Graph

import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime
import pytz
import plotly.graph_objects as go
import time

# 1. UI Configuration & Mobile CSS
st.set_page_config(page_title="SPY 0 DTE Tracker", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
.dte-badge { background: #ff4b4b; color: white; padding: 4px 8px; border-radius: 4px; font-weight: bold; }
.metric-card { background: #f0f2f6; padding: 10px; border-radius: 8px; }
@media (max-width: 640px) { .stMetric { font-size: 0.8rem; } }
</style>""", unsafe_allow_html=True)

# 2. 0 DTE Signal Engine (5m interval, Momentum & Time-of-Day)
def get_0dte_signal():
    tz = pytz.timezone("US/Eastern")
    now = datetime.now(tz)
    current_time = now.strftime("%H:%M")
    
    spy = yf.Ticker("SPY")
    hist = spy.history(period="1d", interval="5m")
    if len(hist) < 21:
        return "WAIT", "Gathering 5m historical data...", None, None, None, None
    
    ema9 = hist['Close'].ewm(span=9, adjust=False).mean()
    ema21 = hist['Close'].ewm(span=21, adjust=False).mean()
    delta = hist['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = (100 - (100 / (1 + rs))).iloc[-1]
    
    current_price = hist['Close'].iloc[-1]
    curr_ema9 = ema9.iloc[-1]
    curr_ema21 = ema21.iloc[-1]
    
    # Time-of-Day Rules
    if current_time > "15:15":
        return "WAIT", f"Post-3:15 PM ET expiration cutoff (High risk)", hist, ema9, ema21, current_price
    if "11:30" <= current_time <= "13:30":
        return "WAIT", f"Midday chop window (Theta acceleration)", hist, ema9, ema21, current_price
    
    # Directional Signals
    if current_price > curr_ema9 > curr_ema21 and rsi < 70:
        return "CALL DEBIT SUGGESTED", f"Bullish momentum: SPY (${current_price:.2f}) > 9 EMA (${curr_ema9:.2f}) > 21 EMA (${curr_ema21:.2f}) | RSI: {rsi:.1f}", hist, ema9, ema21, current_price
    elif current_price < curr_ema9 < curr_ema21 and rsi > 30:
        return "PUT DEBIT SUGGESTED", f"Bearish breakdown: SPY (${current_price:.2f}) < 9 EMA (${curr_ema9:.2f}) < 21 EMA (${curr_ema21:.2f}) | RSI: {rsi:.1f}", hist, ema9, ema21, current_price
    
    return "WAIT", f"Consolidation / No confirmed momentum | RSI: {rsi:.1f}", hist, ema9, ema21, current_price

# 3. 0 DTE Spread Engine
def fetch_0dte_spreads(strat, width, max_debit_ratio, min_ror):
    spy = yf.Ticker("SPY")
    if not spy.options:
        return None
    expiry = spy.options[0] # Lock strictly to 0 DTE

    price = float(spy.fast_info.get('last_price', 0.0))
    chain = spy.option_chain(expiry)
    df = chain.calls if "Call" in strat else chain.puts
    
    if df.empty:
        return None
    
    # Target ATM / 1-tick ITM long strike
    df['dist'] = (df['strike'] - price).abs()
    long_leg = df.sort_values('dist').iloc[0]
    
    target_short = long_leg['strike'] + width if "Call" in strat else long_leg['strike'] - width
    short_matches = df[df['strike'] == target_short]
    
    if not short_matches.empty:
        short_leg = short_matches.iloc[0]
        ask_calc = float(long_leg['ask'] if long_leg['ask'] > 0 else long_leg['lastPrice'])
        bid_calc = float(short_leg['bid'] if short_leg['bid'] > 0 else short_leg['lastPrice'])
        debit = ask_calc - bid_calc
        
        if 0 < debit <= (width * max_debit_ratio):
            profit = width - debit
            ror = profit / debit
            if ror >= min_ror:
                be = long_leg['strike'] + debit if "Call" in strat else long_leg['strike'] - debit
                return {
                    "Spread": f"{long_leg['strike']:.1f}/{short_leg['strike']:.1f}",
                    "Long_Strike": float(long_leg['strike']),
                    "Short_Strike": float(short_leg['strike']),
                    "Entry": debit,
                    "Max_Profit": profit,
                    "RoR": ror,
                    "BE": be
                }
    return None

# --- Main Dashboard ---
st.title("SPY 0 DTE Real-Time Momentum Tracker")
st.markdown('Current Mode: <span class="dte-badge">0 DTE ACTIVE</span>', unsafe_allow_html=True)

# Sidebar Parameters
st.sidebar.header("0 DTE Filter Controls")
strat = st.sidebar.radio("Strategy", ["Call Debit Spread", "Put Debit Spread"])
width = st.sidebar.selectbox("Strike Width ($)", [1.0, 2.0, 3.0], index=0)
max_dr = st.sidebar.slider("Max Debit Ratio (% of Width)", 0.10, 0.60, 0.38)
min_ror = st.sidebar.slider("Min Return on Risk (RoR)", 1.0, 3.0, 1.6)
auto_refresh = st.sidebar.toggle("Live Auto-Refresh (60s)", value=True)

# Signal Evaluation
sig, reason, hist_df, ema9, ema21, current_price = get_0dte_signal()

if "CALL" in sig:
    st.success(f"**Signal**: {sig} — {reason}")
elif "PUT" in sig:
    st.warning(f"**Signal**: {sig} — {reason}")
else:
    st.info(f"**Signal**: {sig} — {reason}")

rec = fetch_0dte_spreads(strat, width, max_dr, min_ror)

if rec:
    st.subheader("🎯 Optimal 0 DTE Spread Recommendation")
    cols = st.columns(5)
    cols[0].metric("Recommended Spread", rec['Spread'])
    cols[1].metric("Entry Cost (Max Risk)", f"${rec['Entry']:.2f}")
    cols[2].metric("Max Profit Target", f"${rec['Max_Profit']:.2f}")
    cols[3].metric("Return on Risk (RoR)", f"{rec['RoR']:.1%}")
    cols[4].metric("Breakeven Price", f"${rec['BE']:.2f}")

# 4. Interactive Visual Price Chart with Critical Parameters
st.subheader("📊 SPY Live Price Action & Critical Parameter Overlays")

if hist_df is not None and not hist_df.empty:
    fig = go.Figure()
    
    # 5-minute Price Action
    fig.add_trace(go.Scatter(
        x=hist_df.index, y=hist_df['Close'],
        mode='lines', name='SPY Price (5m Close)',
        line=dict(color='#1f77b4', width=2.5)
    ))
    
    # 9 EMA (Fast Signal Line)
    fig.add_trace(go.Scatter(
        x=hist_df.index, y=ema9,
        mode='lines', name='9 EMA (Fast Trend)',
        line=dict(color='#ff7f0e', width=1.5, dash='dot')
    ))
    
    # 21 EMA (Base Trend Line)
    fig.add_trace(go.Scatter(
        x=hist_df.index, y=ema21,
        mode='lines', name='21 EMA (Base Trend)',
        line=dict(color='#9467bd', width=1.5, dash='dash')
    ))
    
    # Overlay Critical 0 DTE Parameters
    if rec:
        long_stk = rec['Long_Strike']
        short_stk = rec['Short_Strike']
        be_val = rec['BE']
        
        # Long Strike Level (Entry Barrier)
        fig.add_hline(
            y=long_stk, line_dash="dash", line_color="#2ca02c", line_width=2,
            annotation_text=f"Long Strike (Floor): ${long_stk:.2f}",
            annotation_position="top right", annotation_font_color="#2ca02c"
        )
        
        # Short Strike Level (Max Profit Cap)
        fig.add_hline(
            y=short_stk, line_dash="dash", line_color="#d62728", line_width=2,
            annotation_text=f"Short Strike (Cap): ${short_stk:.2f}",
            annotation_position="top right", annotation_font_color="#d62728"
        )
        
        # Breakeven Level
        fig.add_hline(
            y=be_val, line_dash="dot", line_color="#ffbb78", line_width=2,
            annotation_text=f"Breakeven Level: ${be_val:.2f}",
            annotation_position="bottom right", annotation_font_color="#d62728"
        )
        
        # Shaded Target Profit Zone
        fig.add_hrect(
            y0=min(long_stk, short_stk), y1=max(long_stk, short_stk),
            fillcolor="#2ca02c", opacity=0.12, line_width=0,
            annotation_text="🎯 Max Profit Zone", annotation_position="top left"
        )
    
    # Chart Layout (X/Y Axes, Formatting, Mobile Padding)
    fig.update_layout(
        xaxis=dict(
            title="Time of Day (US/Eastern)",
            showgrid=True,
            gridcolor="#f0f2f6"
        ),
        yaxis=dict(
            title="SPY Share Price ($ USD)",
            showgrid=True,
            gridcolor="#f0f2f6",
            tickformat="$.2f"
        ),
        template="plotly_white",
        height=480,
        margin=dict(l=15, r=15, t=30, b=30),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        hovermode="x unified"
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.warning("Awaiting live 5-minute market data from Yahoo Finance.")

if auto_refresh:
    time.sleep(60)
    st.rerun()

