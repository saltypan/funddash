import streamlit as st
import pandas as pd
import efinance as ef
from pyxirr import xirr
from datetime import datetime, timedelta
import os
import requests
import json

# ==========================================
# 1. 页面配置与初始化
# ==========================================
st.set_page_config(page_title="个人投资理财看板", layout="wide")
st.title("📈 个人投资看板 & 实时追踪")

if not os.path.exists('transactions.csv'):
    df_init = pd.DataFrame(columns=['date', 'code', 'name', 'type', 'nav', 'amount', 'shares', 'fee'])
    df_init.to_csv('transactions.csv', index=False)

# ==========================================
# 2. 侧边栏：录入新交易表单
# ==========================================
with st.sidebar:
    st.header("➕ 录入新交易")
    with st.form("new_transaction_form", clear_on_submit=True):
        t_date = st.date_input("交易日期", datetime.now())
        t_code = st.text_input("标的代码 (如 600519 或 110007)")
        t_name = st.text_input("标的名称")
        t_type = st.selectbox("交易类型", ["BUY", "SELL", "FEE"])
        t_nav = st.number_input("成交净值/单价", min_value=0.0000, step=0.0010, format="%.4f")
        t_amount = st.number_input("发生金额 (元)", min_value=0.0, step=100.0)
        t_shares = st.number_input("成交份额", min_value=0.0, step=1.0)
        t_fee = st.number_input("手续费 (元)", min_value=0.0, step=1.0)
        
        submitted = st.form_submit_button("提交记录")
        
        if submitted:
            if t_code and t_name and t_amount >= 0:
                new_record = pd.DataFrame([{
                    'date': t_date.strftime('%Y-%m-%d'),
                    'code': str(t_code).strip().zfill(6),
                    'name': t_name.strip(),
                    'type': t_type,
                    'nav': t_nav,
                    'amount': t_amount,
                    'shares': t_shares,
                    'fee': t_fee
                }])
                new_record.to_csv('transactions.csv', mode='a', header=False, index=False)
                st.success("✅ 交易记录已保存！")
                st.cache_data.clear() 
                st.rerun()            
            else:
                st.error("⚠️ 请确保填写了代码、名称，且金额正确。")

# ==========================================
# 3. 数据读取与行情获取 
# ==========================================
@st.cache_data(ttl=60)
def load_transactions():
    df = pd.read_csv('transactions.csv')
    df['date'] = pd.to_datetime(df['date'])
    df['code'] = df['code'].astype(str).str.zfill(6)
    if 'nav' not in df.columns:
        df['nav'] = df.apply(lambda row: row['amount'] / row['shares'] if row.get('shares', 0) > 0 else 0.0, axis=1)
    df['nav'] = df['nav'].fillna(0.0)
    return df

transactions = load_transactions()

@st.cache_data(ttl=300) 
def get_live_prices(codes):
    prices = {}
    for code in codes:
        code_str = str(code).strip().zfill(6)
        if not code_str: continue
        prices[code_str] = {'price': 0.0, 'change_rate': 0.0, 'change_amount': 0.0}
        
        try:
            quote_df = ef.stock.get_realtime_quotes(code_str)
            if quote_df is not None and not quote_df.empty:
                prices[code_str]['price'] = float(quote_df['最新价'].iloc[0])
                prices[code_str]['change_rate'] = float(quote_df['涨跌幅'].iloc[0])
                prices[code_str]['change_amount'] = float(quote_df['涨跌额'].iloc[0])
                continue 
        except: pass
            
        try:
            url = f"http://api.fund.eastmoney.com/f10/lsjz?fundCode={code_str}&pageIndex=1&pageSize=2"
            headers = {'Referer': 'http://fund.eastmoney.com/'}
            res = requests.get(url, headers=headers, timeout=5)
            data = json.loads(res.text)
            if data.get('Data') and data['Data'].get('LSJZList'):
                latest = data['Data']['LSJZList'][0]
                prices[code_str]['price'] = float(latest['DWJZ'])
                jzzzl = latest.get('JZZZL', '0')
                prices[code_str]['change_rate'] = float(jzzzl) if jzzzl else 0.0
                if len(data['Data']['LSJZList']) > 1:
                    yest_nav = float(data['Data']['LSJZList'][1]['DWJZ'])
                    prices[code_str]['change_amount'] = prices[code_str]['price'] - yest_nav
                else:
                    prices[code_str]['change_amount'] = prices[code_str]['price'] * (prices[code_str]['change_rate'] / 100)
                continue
        except: pass
    return prices

@st.cache_data(ttl=3600)
def get_history_data(code):
    code_str = str(code).strip().zfill(6)
    df = pd.DataFrame()
    try:
        df_fund = ef.fund.get_quote_history(code_str)
        if df_fund is not None and not df_fund.empty:
            df = df_fund.rename(columns={'日期': 'date', '单位净值': 'nav', '收盘': 'nav'})
            if 'nav' not in df.columns and '累计净值' in df.columns:
                df['nav'] = df['累计净值']
    except: pass
    
    if df.empty or 'nav' not in df.columns:
        try:
            df_stock = ef.stock.get_quote_history(code_str)
            if df_stock is not None and not df_stock.empty:
                df = df_stock.rename(columns={'日期': 'date', '收盘': 'nav'})
        except: pass

    if not df.empty and 'date' in df.columns and 'nav' in df.columns:
        df['date'] = pd.to_datetime(df['date']).dt.date
        df['nav'] = pd.to_numeric(df['nav'], errors='coerce')
        return df[['date', 'nav']].sort_values('date').dropna()
    return pd.DataFrame(columns=['date', 'nav'])

unique_codes = list(transactions['code'].unique())
live_prices = get_live_prices(unique_codes)

# ==========================================
# 4. 计算持仓明细、XIRR 与今日收益
# ==========================================
holdings = {}
cash_flows = []

for _, row in transactions.iterrows():
    c_date = row['date'].date()
    c_type = row['type']
    c_amount = float(row['amount'])
    c_fee = float(row['fee'])
    c_code = str(row['code']).zfill(6)
    c_name = row['name']
    
    if c_code not in holdings:
        holdings[c_code] = {'name': c_name, 'shares': 0.0, 'total_cost': 0.0}
    
    if c_type == 'BUY':
        cash_flows.append((c_date, -(c_amount + c_fee)))
        holdings[c_code]['shares'] += float(row['shares'])
        holdings[c_code]['total_cost'] += (c_amount + c_fee)
    elif c_type == 'SELL':
        cash_flows.append((c_date, c_amount - c_fee))
        holdings[c_code]['shares'] -= float(row['shares'])
        holdings[c_code]['total_cost'] -= (c_amount - c_fee)

total_market_value = 0.0
total_today_profit = 0.0
holding_rows = []

for code, info in holdings.items():
    if info['shares'] <= 0.001: continue
        
    market_data = live_prices.get(code, {'price': 0.0, 'change_rate': 0.0, 'change_amount': 0.0})
    current_price = market_data['price']
    market_val = info['shares'] * current_price
    today_profit = info['shares'] * market_data['change_amount']
    
    total_market_value += market_val
    total_today_profit += today_profit
    
    profit = market_val - info['total_cost']
    profit_rate = (profit / info['total_cost'] * 100) if info['total_cost'] > 0 else 0.0
    avg_cost = info['total_cost'] / info['shares'] if info['shares'] > 0 else 0.0
    
    holding_rows.append({
        "标的代码": code, "标的名称": info['name'], "持仓份额": info['shares'],
        "购买均价": avg_cost, "最新单价": current_price, "当前市值": market_val,
        "持仓本金": info['total_cost'], "持有收益": profit, "收益率": profit_rate, "今日收益": today_profit
    })

cash_flows.append((datetime.now().date(), total_market_value))
try:
    portfolio_xirr = xirr(cash_flows)
    xirr_display = f"{portfolio_xirr * 100:.2f}%" if portfolio_xirr is not None else "数据不足"
except: xirr_display = "等待更多数据"

total_cost_all = sum(info['total_cost'] for info in holdings.values() if info['shares'] > 0.001)
total_cumulative_profit = total_market_value - total_cost_all
yesterday_market_value = total_market_value - total_today_profit
today_return_rate = (total_today_profit / yesterday_market_value) * 100 if yesterday_market_value > 0 else 0.0

# ==========================================
# 5. 渲染顶部核心数据区与全盘回溯走势图
# ==========================================
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("持仓总市值", f"¥ {total_market_value:,.2f}")
col2.metric("累计投入本金", f"¥ {total_cost_all:,.2f}")
col3.metric("全盘累计收益", f"¥ {total_cumulative_profit:,.2f}", f"{total_cumulative_profit/total_cost_all*100:+.2f}%" if total_cost_all>0 else "0%", delta_color="inverse")
col4.metric("今日全盘收益", f"¥ {total_today_profit:,.2f}", f"{today_return_rate:+.2f}%", delta_color="inverse")
col5.metric("XIRR 年化收益率", xirr_display)

st.markdown("### 📈 资产与收益历史走势")
tab1, tab2 = st.tabs(["累计收益走势", "总资产走势"])

with st.spinner('正在基于历史流水进行全盘核算...'):
    if not transactions.empty:
        min_date = transactions['date'].min().date()
        today_date = datetime.now().date()
        port_timeline = pd.DataFrame({'date': pd.date_range(min_date, today_date).date})
        
        port_timeline['total_val'] = 0.0
        port_timeline['total_cost'] = 0.0
        
        for code in unique_codes:
            hist_nav = get_history_data(code)
            code_trans = transactions[transactions['code'] == code].copy()
            code_trans['date'] = code_trans['date'].dt.date
            
            daily_trans = code_trans.groupby('date').apply(
                lambda x: pd.Series({
                    'buy_shares': x[x['type']=='BUY']['shares'].sum() - x[x['type']=='SELL']['shares'].sum(),
                    'buy_cost': x[x['type']=='BUY'].apply(lambda r: r['amount']+r['fee'], axis=1).sum() - x[x['type']=='SELL'].apply(lambda r: r['amount']-r['fee'], axis=1).sum()
                })
            ).reset_index()
            
            code_timeline = pd.merge(port_timeline[['date']], daily_trans, on='date', how='left').fillna(0)
            code_timeline['cum_shares'] = code_timeline['buy_shares'].cumsum()
            code_timeline['cum_cost'] = code_timeline['buy_cost'].cumsum()
            
            if not hist_nav.empty:
                code_timeline = pd.merge(code_timeline, hist_nav, on='date', how='left')
                code_timeline['nav'] = code_timeline['nav'].ffill().bfill()
            else:
                code_timeline['nav'] = 1.0 # 如果完全获取不到历史净值的兜底处理
                
            port_timeline['total_val'] += code_timeline['cum_shares'] * code_timeline['nav']
            port_timeline['total_cost'] += code_timeline['cum_cost']
        
        port_timeline['total_profit'] = port_timeline['total_val'] - port_timeline['total_cost']
        port_timeline.set_index('date', inplace=True)
        
        with tab1: st.line_chart(port_timeline['total_profit'], use_container_width=True)
        with tab2: st.area_chart(port_timeline['total_val'], use_container_width=True)
    else:
        st.info("暂无交易记录，无法绘制走势图")

# ==========================================
# 6. 渲染表格通用样式
# ==========================================
center_css = [dict(selector="th", props=[("text-align", "center")]), dict(selector="td", props=[("text-align", "center")])]
def color_red_green(val):
    if type(val) in [int, float]:
        if val > 0: return 'color: #ff4b4b; font-weight: bold;'
        elif val < 0: return 'color: #09ab3b; font-weight: bold;'
    return ''

st.subheader("📊 当前持仓与实时盈亏")
if holding_rows:
    df_holdings = pd.DataFrame(holding_rows)
    styled_holdings = df_holdings.style.format({
        "持仓份额": "{:,.2f}", "购买均价": "{:,.4f}", "最新单价": "{:,.4f}", "当前市值": "¥ {:,.2f}",
        "持仓本金": "¥ {:,.2f}", "持有收益": "¥ {:,.2f}", "收益率": "{:+.2f}%", "今日收益": "¥ {:+.2f}"
    })
    
    try: styled_holdings = styled_holdings.map(color_red_green, subset=["持有收益", "收益率", "今日收益"])
    except AttributeError: styled_holdings = styled_holdings.applymap(color_red_green, subset=["持有收益", "收益率", "今日收益"])
        
    styled_holdings = styled_holdings.set_table_styles(center_css)
    st.dataframe(styled_holdings, use_container_width=True)
else:
    st.info("暂无持仓记录或已全部清仓")

# ==========================================
# 7. 单标的下钻深度分析 
# ==========================================
if holding_rows:
    st.divider()
    st.subheader("🔍 单只标的下钻分析")
    
    selected_name = st.selectbox("选择要分析的持仓标的", [row['标的名称'] for row in holding_rows])
    selected_code = next(row['标的代码'] for row in holding_rows if row['标的名称'] == selected_name)
    
    with st.spinner('正在同步历史净值与核算数据...'):
        hist_nav = get_history_data(selected_code)
        
        if not hist_nav.empty:
            code_trans = transactions[transactions['code'] == selected_code].copy()
            code_trans['date'] = pd.to_datetime(code_trans['date']).dt.date
            
            min_date = code_trans['date'].min()
            today_date = datetime.now().date()
            timeline = pd.DataFrame({'date': pd.date_range(min_date, today_date).date})
            
            daily_trans = code_trans.groupby('date').apply(
                lambda x: pd.Series({
                    'buy_shares': x[x['type']=='BUY']['shares'].sum() - x[x['type']=='SELL']['shares'].sum(),
                    'buy_cost': x[x['type']=='BUY'].apply(lambda r: r['amount']+r['fee'], axis=1).sum() - x[x['type']=='SELL'].apply(lambda r: r['amount']-r['fee'], axis=1).sum()
                })
            ).reset_index()
            
            timeline = pd.merge(timeline, daily_trans, on='date', how='left').fillna(0)
            timeline['cum_shares'] = timeline['buy_shares'].cumsum()
            timeline['cum_cost'] = timeline['buy_cost'].cumsum()
            
            timeline = pd.merge(timeline, hist_nav, on='date', how='left')
            timeline['nav'] = timeline['nav'].ffill().bfill() 
            timeline['market_val'] = timeline['cum_shares'] * timeline['nav']
            timeline['profit'] = timeline['market_val'] - timeline['cum_cost']
            
            d_col1, d_col2 = st.columns(2)
            with d_col1:
                st.markdown("##### 📈 历史净值走势")
                st.line_chart(timeline.set_index('date')['nav'], use_container_width=True)
            with d_col2:
                st.markdown("##### 💰 累计收益走势")
                st.area_chart(timeline.set_index('date')['profit'], use_container_width=True)
                
            st.markdown("##### 📅 月度收益变化日历 (元)")
            timeline['year'] = pd.to_datetime(timeline['date']).dt.year
            timeline['month'] = pd.to_datetime(timeline['date']).dt.month
            
            month_end_df = timeline.groupby(['year', 'month']).last().reset_index()
            month_end_df['prev_profit'] = month_end_df['profit'].shift(1).fillna(0.0)
            month_end_df['monthly_gain'] = month_end_df['profit'] - month_end_df['prev_profit']
            
            cal_pivot = month_end_df.pivot(index='year', columns='month', values='monthly_gain').fillna(0.0)
            cal_pivot.columns = [f"{int(c)}月" for c in cal_pivot.columns]
            cal_pivot.index.name = "年份"
            
            styled_cal = cal_pivot.style.format("¥ {:+.2f}").set_table_styles(center_css)
            try: styled_cal = styled_cal.map(color_red_green)
            except AttributeError: styled_cal = styled_cal.applymap(color_red_green)
            
            st.dataframe(styled_cal, use_container_width=True)
            
        else:
            st.warning("暂无该标的的历史净值数据，可能接口限制或代码类型不受支持。")

st.divider()
st.subheader("📝 历史交易流水 (含数据校验)")
if not transactions.empty:
    df_trans = transactions.sort_values(by="date", ascending=False).copy()
    df_trans['date'] = df_trans['date'].dt.strftime('%Y-%m-%d')
    
    def verify_data(row):
        if row['type'] == 'BUY': expected_amt = (row['shares'] * row['nav']) + row['fee']
        elif row['type'] == 'SELL': expected_amt = (row['shares'] * row['nav']) - row['fee']
        else: return "➖"
        diff = row['amount'] - expected_amt
        return "✅ 账实相符" if abs(diff) <= 0.05 else f"⚠️ 误差 {diff:+.2f} 元"

    df_trans['数据校对'] = df_trans.apply(verify_data, axis=1)
    df_trans = df_trans[['date', 'code', 'name', 'type', 'nav', 'amount', 'shares', 'fee', '数据校对']]
    df_trans = df_trans.rename(columns={'date': '交易日期', 'code': '标的代码', 'name': '标的名称', 'type': '交易类型', 'nav': '成交净值', 'amount': '发生金额', 'shares': '成交份额', 'fee': '手续费'})
    
    styled_trans = df_trans.style.format({"成交净值": "{:,.4f}", "发生金额": "¥ {:,.2f}", "成交份额": "{:,.2f}", "手续费": "¥ {:,.2f}"}).set_table_styles(center_css)
    st.dataframe(styled_trans, use_container_width=True)