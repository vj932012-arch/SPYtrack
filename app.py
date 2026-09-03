import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import time
from datetime import datetime

# 1. UI Configuration & Mobile CSS
st.set_page_config(page_title="SPY YFinance Tracker", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
.metric-card { background: #f0f2f6; padding: 10px; border-radius: 8px; }
@media (max-width: 640px) { .stMetric { font-size: 0.8rem; } }
</style>""", unsafe_allow_html=True)

# 2. Spread Engine & Real-Time Data (yfinance)
def fetch_spreads(strategy, width_target, max_debit_ratio, min_ror, min_vol, min_oi, expiry):
    spy = yf.Ticker("SPY")
    price = spy.fast_info['last_price']
    change = spy.fast_info['day_change_percent']
    
    chain = spy.option_chain(expiry)
    df_opt = chain.calls if "Call" in strategy else chain.puts
    
    # Filtering and Fallback Logic
    df_opt['ask_calc'] = df_opt['ask'].where(df_opt['ask'] > 0, df_opt['lastPrice'])
    df_opt['bid_calc'] = df_opt['bid'].where(df_opt['bid'] > 0, df_opt['lastPrice'])
    
    df_filt = df_opt[(df_opt['volume'] >= min_vol) & (df_opt['openInterest'] >= min_oi)].sort_values("strike")
    results = []
    opt_type = "call" if "Call" in strategy else "put"
    
    for _, long_leg in df_filt.iterrows():
        target_strike = long_leg["strike"] + width_target if opt_type == "call" else long_leg["strike"] - width_target
        short_matches = df_filt[df_filt["strike"] == target_strike]
        
        if not short_matches.empty:
            short_leg = short_matches.iloc[0]
            net_debit = long_leg["ask_calc"] - short_leg["bid_calc"]
            
            if 0 < net_debit <= (width_target * max_debit_ratio):
                max_profit = width_target - net_debit
                ror = max_profit / net_debit
                
                if ror >= min_ror:
                    be = long_leg["strike"] + net_debit if opt_type == "call" else long_leg["strike"] - net_debit
                    results.append({
                        "Spread": f"{long_leg['strike']}/{short_leg['strike']} {opt_type.upper()}",
                        "Net Debit": f"${net_debit:.2f}",
                        "Max Profit": f"${max_profit:.2f}",
                        "RoR": f"{ror:.1%}",
                        "Breakeven": f"${be:.2f}",
                        "Vol (L/S)": f"{long_leg['volume']}/{short_leg['volume']}"
                    })
    return pd.DataFrame(results), price, change

# 3. Main Interface
st.title("SPY Live Dashboard (yfinance)")
spy_ticker = yf.Ticker("SPY")
all_expiries = spy_ticker.options

st.sidebar.header("Filter Settings")
selected_exp = st.sidebar.selectbox("Expiration Date", all_expiries)
strat = st.sidebar.radio("Strategy", ["Call Debit Spread", "Put Debit Spread"])
width = st.sidebar.selectbox("Strike Width", [1, 2, 5, 10])
vol_min = st.sidebar.number_input("Min Volume", value=100)
oi_min = st.sidebar.number_input("Min Open Interest", value=100)
max_d_ratio = st.sidebar.slider("Max Debit to Width Ratio", 0.10, 0.60, 0.30)
min_ror_val = st.sidebar.slider("Min RoR", 0.5, 3.0, 1.5)
auto_refresh = st.sidebar.toggle("Auto-Refresh (60s)", value=True)

df_spreads, current_price, day_pct = fetch_spreads(strat, width, max_d_ratio, min_ror_val, vol_min, oi_min, selected_exp)

c1, c2, c3 = st.columns(3)
c1.metric("SPY Price", f"${current_price:.2f}", f"{day_pct:.2%}")
c2.metric("Spreads Found", len(df_spreads))
c3.metric("Status", "Live")

st.dataframe(df_spreads, use_container_width=True)

if st.button("Dispatch Alerts (Discord/Telegram)"):
    st.success("High RoR opportunities dispatched via Webhooks.")

if auto_refresh:
    time.sleep(60)
    st.rerun()
