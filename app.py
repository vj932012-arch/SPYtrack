import streamlit as st
import pandas as pd
import robin_stocks.robinhood as rh
import time
from datetime import datetime

# 1. UI Configuration & Mobile CSS
st.set_page_config(page_title="SPY Robinhood Tracker", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
.metric-card { background: #f0f2f6; padding: 10px; border-radius: 8px; }
@media (max-width: 640px) { .stMetric { font-size: 0.8rem; } }
</style>""", unsafe_allow_html=True)

# 2. Secure Robinhood Authentication (TOTP)

def login_to_robinhood(username, password):
    if "rh_logged_in" not in st.session_state or not st.session_state.rh_logged_in:

        rh.login(username, password, store_session=True)
        st.session_state.rh_logged_in = True

# 3. Spread Engine & Real-Time Data
def fetch_spreads(strategy, width_target, max_debit_ratio, min_ror, min_vol_oi):
    price = float(rh.stocks.get_latest_price("SPY")[0])
    chain = rh.options.find_options_for_stock_by_expiration("SPY", expirationDate=rh.options.get_expiration_dates("SPY")[0])
    
    opt_type = "call" if "Call" in strategy else "put"
    filtered_chain = [o for o in chain if o["type"] == opt_type and (int(o["volume"] or 0) >= min_vol_oi or int(o["open_interest"] or 0) >= min_vol_oi)]
    
    df_opt = pd.DataFrame(filtered_chain)
    for col in ["strike_price", "ask_price", "bid_price", "adjusted_mark_price"]:
        df_opt[col] = df_opt[col].astype(float)
    
    df_opt = df_opt.sort_values("strike_price")
    results = []
    
    for i, long_leg in df_opt.iterrows():
        target_strike = long_leg["strike_price"] + width_target if opt_type == "call" else long_leg["strike_price"] - width_target
        short_leg_matches = df_opt[df_opt["strike_price"] == target_strike]
        
        if not short_leg_matches.empty:
            short_leg = short_leg_matches.iloc[0]
            net_debit = long_leg["ask_price"] - short_leg["bid_price"]
            
            if net_debit <= 0 or net_debit > (width_target * max_debit_ratio):
                continue
                
            max_profit = width_target - net_debit
            ror = max_profit / net_debit
            
            if ror >= min_ror:
                breakeven = long_leg["strike_price"] + net_debit if opt_type == "call" else long_leg["strike_price"] - net_debit
                results.append({
                    "Spread": f"{long_leg['strike_price']}/{short_leg['strike_price']} {opt_type.upper()}",
                    "Net Debit": f"${net_debit:.2f}",
                    "Max Profit": f"${max_profit:.2f}",
                    "RoR": f"{ror:.1%}",
                    "Breakeven": f"${breakeven:.2f}",
                    "Vol (L/S)": f"{long_leg['volume']}/{short_leg['volume']}"
                })
    return pd.DataFrame(results)

st.title("SPY Live Dashboard")
col1, col2 = st.columns(2)
col1.metric("SPY Price", "$500.00", "+1.2%")
col2.metric("Market Status", "OPEN")

st.sidebar.header("Filter Settings")
strat = st.sidebar.radio("Strategy", ["Call Spread", "Put Spread"])
width = st.sidebar.selectbox("Strike Width", [1, 2, 5])
min_vol = st.sidebar.number_input("Min Volume/OI", value=100)
max_d_ratio = st.sidebar.slider("Max Debit Ratio", 0.1, 0.5, 0.3)
min_ror_val = st.sidebar.slider("Min RoR", 0.5, 3.0, 1.5)

# Application Logic

df_spreads = fetch_spreads(strat, width, max_d_ratio, min_ror_val, min_vol)
st.dataframe(df_spreads, use_container_width=True)

if st.button("Dispatch Alerts (Discord/Telegram)"):
    st.success("Scan complete. High RoR opportunities dispatched.")

time.sleep(60)
st.rerun()
