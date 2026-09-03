import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime
import pytz
import time

# 1. UI Configuration & 0 DTE Branding
st.set_page_config(page_title="SPY 0 DTE Tracker", layout="wide")
st.markdown("""<style>.dte-badge { background: #ff4b4b; color: white; padding: 4px 8px; border-radius: 4px; font-weight: bold; }</style>""", unsafe_allow_html=True)

# 2. 0 DTE Signal Engine (5m interval, Momentum & Time-of-Day)
def get_0dte_signal():
    tz = pytz.timezone("US/Eastern")
    now = datetime.now(tz)
    current_time = now.strftime("%H:%M")
    
    spy = yf.Ticker("SPY")
    hist = spy.history(period="1d", interval="5m")
    if len(hist) < 21: return "WAIT", "Gathering 5m data..."
    
    ema9 = hist['Close'].ewm(span=9, adjust=False).mean().iloc[-1]
    ema21 = hist['Close'].ewm(span=21, adjust=False).mean().iloc[-1]
    delta = hist['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi = 100 - (100 / (1 + (gain / loss.replace(0, 1e-9)).iloc[-1]))
    price = hist['Close'].iloc[-1]
    
    # Time-of-Day Logic
    if current_time > "15:15": return "WAIT", "Post-3:15 PM high-risk cutoff."
    if "11:30" <= current_time <= "13:30": return "WAIT", "Midday chop/Theta warning."
    
    if price > ema9 > ema21 and rsi < 70: return "CALL DEBIT SUGGESTED", "Bullish momentum (9/21 EMA)."
    if price < ema9 < ema21 and rsi > 30: return "PUT DEBIT SUGGESTED", "Bearish momentum (9/21 EMA)."
    return "WAIT", "No 0 DTE trend confirmed."

# 3. 0 DTE Spread Engine
def fetch_0dte_spreads(strat, width, max_debit_ratio, min_ror):
    spy = yf.Ticker("SPY")

    if not spy.options: return None
    expiry = spy.options[0] # Lock to 0 DTE


    price = float(spy.fast_info.get('last_price', 0.0))
    chain = spy.option_chain(expiry)
    df = chain.calls if "Call" in strat else chain.puts
    
    # Target ATM/1-tick ITM
    df['dist'] = (df['strike'] - price).abs()
    long_leg = df.sort_values('dist').iloc[0]
    target_short = long_leg['strike'] + width if "Call" in strat else long_leg['strike'] - width
    short_matches = df[df['strike'] == target_short]
    
    if not short_matches.empty:
        short_leg = short_matches.iloc[0]
        debit = long_leg['ask'] - short_leg['bid']
        if 0 < debit <= (width * max_debit_ratio):
            profit = width - debit
            ror = profit / debit
            if ror >= min_ror:
                return {"Spread": f"{long_leg['strike']}/{short_leg['strike']}", "Entry": f"${debit:.2f}", "Max Profit": f"${profit:.2f}", "RoR": f"{ror:.1%}", "BE": f"${long_leg['strike']+debit if 'Call' in strat else long_leg['strike']-debit:.2f}"}
    return None

st.title("SPY 0 DTE Momentum Dashboard")
st.markdown('Current Mode: <span class="dte-badge">0 DTE ACTIVE</span>', unsafe_allow_html=True)

# Sidebar 0 DTE Defaults
strat = st.sidebar.radio("Strategy", ["Call Debit Spread", "Put Debit Spread"])
width = st.sidebar.selectbox("Strike Width", [1.0, 2.0, 3.0], index=0)
max_dr = st.sidebar.slider("Max Debit Ratio", 0.10, 0.60, 0.38)
min_ror = st.sidebar.slider("Min RoR", 1.0, 3.0, 1.6)

sig, reason = get_0dte_signal()
st.info(f"**Signal**: {sig} | {reason}")

rec = fetch_0dte_spreads(strat, width, max_dr, min_ror)
if rec:
    st.subheader("🎯 Top 0 DTE Recommendation")
    cols = st.columns(5)
    cols[0].metric("Spread", rec['Spread'])
    cols[1].metric("Entry (Max Risk)", rec['Entry'])
    cols[2].metric("Max Profit", rec['Max Profit'])
    cols[3].metric("RoR", rec['RoR'])
    cols[4].metric("Breakeven", rec['BE'])
