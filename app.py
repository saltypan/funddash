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
    df_init = pd.DataFrame(columns=['date', 'platform', 'code', 'name', 'type', 'nav', 'amount', 'shares', 'fee'])
    df_init.to_csv('transactions.csv', index=False)

# ==========================================
# 2. 核心数据与函数定义 (前置定义供侧边栏调用)
# ==========================================
@st.cache_data(ttl=60)
def load_transactions():
    df = pd.read_csv('transactions.csv')
    df['date'] = pd.to_datetime(df['date'])
    df['code'] = df['code'].astype(str).str.zfill(6)
    return df

transactions = load_transactions()

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

# ==========================================
# 3. 侧边栏：全局看板筛选 & 智能全自动录入系统
# ==========================================
with st.sidebar:
    st.header("🔎 全局看板筛选")
    existing_platforms = list(transactions['platform'].dropna().unique())
    platforms_options = ["全盘总览"] + existing_platforms
    selected_platform = st.selectbox("选择要查看的平台/账户", platforms_options)
    
    st.divider()
    st.header("➕ 录入新交易 (智能助手)")
    
    t_date = st.date_input("交易日期 (自动匹配当日净值)", datetime.now())
    t_code = st.text_input("标的代码 (输入代码并回车)", key="input_code")
    code_str = str(t_code).strip().zfill(6) if t_code else ""
    
    auto_name = ""
    auto_nav = 0.0000
    
    if code_str:
        history_match = transactions[transactions['code'] == code_str]
        if not history_match.empty:
            auto_name = history_match['name'].iloc[0]
        else:
            try:
                q = ef.stock.get_realtime_quotes(code_str)
                if q is not None and not q.empty: auto_name = q['股票名称'].iloc[0]
            except: pass
            if not auto_name:
                try:
                    url = f"http://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx?m=1&key={code_str}"
                    res = requests.get(url, timeout=3).json()
                    if res.get("Datas"): auto_name = res["Datas"][0]["NAME"]
                except: pass
                
        hist_nav_df = get_history_data(code_str)
        if not hist_nav_df.empty:
            match_nav = hist_nav_df[hist_nav_df['date'] == t_date]
            if not match_nav.empty:
                auto_nav = float(match_nav['nav'].iloc[0])
            else:
                past_navs = hist_nav_df[hist_nav_df['date'] <= t_date]
                if not past_navs.empty:
                    auto_nav = float(past_navs.iloc[-1]['nav'])

    t_platform = st.selectbox("交易平台", ["东方财富", "天天基金", "支付宝", "招商银行", "微众银行", "华泰证券", "其他"])
    t_name = st.text_input("标的名称", value=auto_name)
    t_type = st.selectbox("交易类型", ["BUY", "SELL", "FEE", "DIVIDEND"])
    
    col1, col2 = st.columns([6, 4])
    with col1:
        t_amount = st.number_input("发生金额 (元)", min_value=0.0, step=100.0, value=0.0)
    with col2:
        fee_rate = st.number_input("费率估算(%)", value=0.15, step=0.01) / 100.0
        
    auto_fee = round(t_amount * fee_rate, 2) if t_type == "BUY" and t_amount > 0 else 0.0
    auto_shares = 0.0
    if auto_nav > 0 and t_type == "BUY":
        auto_shares = round((t_amount - auto_fee) / auto_nav, 2)
    elif auto_nav > 0 and t_type == "SELL":
        auto_shares = round(t_amount / auto_nav, 2)

    t_nav = st.number_input("成交净值/单价", min_value=0.0000, step=0.0010, format="%.4f", value=float(auto_nav))
    t_shares = st.number_input("成交份额", min_value=0.0, step=1.0, value=float(auto_shares))
    t_fee = st.number_input("手续费 (元)", min_value=0.0, step=1.0, value=float(auto_fee))
    
    submitted = st.button("✅ 提交记录", use_container_width=True)
    
    if submitted:
        if code_str and t_name and t_amount > 0:
            new_record = pd.DataFrame([{
                'date': t_date.strftime('%Y-%m-%d'),
                'platform': t_platform.strip(),
                'code': code_str,
                'name': t_name.strip(),
                'type': t_type,
                'nav': t_nav,
                'amount': t_amount,
                'shares': t_shares,
                'fee': t_fee
            }])
            
            # 强制剔除时间尾巴并写入
            updated_transactions = pd.concat([transactions, new_record], ignore_index=True)
            updated_transactions['date'] = pd.to_datetime(updated_transactions['date']).dt.strftime('%Y-%m-%d')
            updated_transactions.to_csv('transactions.csv', index=False)
            
            st.success(f"✅ {t_name} 交易记录已保存！")
            st.session_state.input_code = "" 
            st.cache_data.clear() 
            st.rerun()            
        else:
            st.error("⚠️ 请确保填写了代码、名称，且金额必须大于0。")

# ==========================================
# 4. 根据侧边栏筛选器拦截数据
# ==========================================
if selected_platform == "全盘总览":
    filtered_trans = transactions.copy()
    display_title_suffix = "(全盘)"
else:
    filtered_trans = transactions[transactions['platform'] == selected_platform].copy()
    display_title_suffix = f"({selected_platform})"

unique_codes = list(filtered_trans['code'].unique())
live_prices = get_live_prices(unique_codes)

# ==========================================
# 5. 核心逻辑：份额摊薄法计算纯本金、单列手续费
# ==========================================
holdings = {}
cash_flows = []
filtered_trans = filtered_trans.sort_values(by='date')

for _, row in filtered_trans.iterrows():
    c_date = row['date'].date()
    c_type = row['type']
    c_amount = float(row['amount'])
    c_fee = float(row['fee'])
    c_shares = float(row['shares'])
    c_code = str(row['code']).zfill(6)
    c_name = row['name']
    
    if c_code not in holdings:
        holdings[c_code] = {'name': c_name, 'shares': 0.0, 'pure_cost': 0.0, 'total_fee': 0.0}
    
    if c_type == 'BUY':
        # 纯本金仅计入剔除手续费后的实际投入份额价值
        pure_in = c_amount - c_fee
        cash_flows.append((c_date, -pure_in))
        holdings[c_code]['shares'] += c_shares
        holdings[c_code]['pure_cost'] += pure_in
        holdings[c_code]['total_fee'] += c_fee
        
    elif c_type == 'SELL':
        # 卖出时，根据份额比例等比核减持仓本金
        pure_out = c_amount + c_fee
        cash_flows.append((c_date, pure_out))
        
        avg_cost = holdings[c_code]['pure_cost'] / holdings[c_code]['shares'] if holdings[c_code]['shares'] > 0 else 0
        holdings[c_code]['shares'] -= c_shares
        holdings[c_code]['pure_cost'] -= c_shares * avg_cost
        
        # 避免浮点数导致清仓后残留小数点
        if holdings[c_code]['shares'] <= 0.001:
            holdings[c_code]['pure_cost'] = 0.0
            holdings[c_code]['shares'] = 0.0
            
        holdings[c_code]['total_fee'] += c_fee

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
    
    profit = market_val - info['pure_cost']
    profit_rate = (profit / info['pure_cost'] * 100) if info['pure_cost'] > 0 else 0.0
    avg_cost = info['pure_cost'] / info['shares'] if info['shares'] > 0 else 0.0
    
    holding_rows.append({
        "标的代码": code, "标的名称": info['name'], "持仓份额": info['shares'],
        "购买均价": avg_cost, "最新单价": current_price, "当前市值": market_val,
        "持仓本金": info['pure_cost'], "累计手续费": info['total_fee'], 
        "纯持有收益": profit, "收益率": profit_rate, "今日收益": today_profit
    })

cash_flows.append((datetime.now().date(), total_market_value))
try:
    portfolio_xirr = xirr(cash_flows) if len(cash_flows) > 1 else None
    xirr_display = f"{portfolio_xirr * 100:.2f}%" if portfolio_xirr is not None else "数据不足"
except: xirr_display = "等待更多数据"

total_pure_cost_all = sum(info['pure_cost'] for info in holdings.values() if info['shares'] > 0.001)
total_fee_all = sum(info['total_fee'] for info in holdings.values() if info['shares'] > 0.001)
total_cumulative_profit = total_market_value - total_pure_cost_all
yesterday_market_value = total_market_value - total_today_profit
today_return_rate = (total_today_profit / yesterday_market_value) * 100 if yesterday_market_value > 0 else 0.0

# ==========================================
# 6. 渲染顶部核心数据区与回溯走势图
# ==========================================
st.markdown(f"### 📊 核心指标 {display_title_suffix}")
# 新增第六列展示单列的累计手续费
col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("持仓总市值", f"¥ {total_market_value:,.2f}")
col2.metric("持仓总本金 (纯)", f"¥ {total_pure_cost_all:,.2f}")
col3.metric("累计收益 (纯)", f"¥ {total_cumulative_profit:,.2f}", f"{total_cumulative_profit/total_pure_cost_all*100:+.2f}%" if total_pure_cost_all>0 else "0%", delta_color="inverse")
col4.metric("今日收益", f"¥ {total_today_profit:,.2f}", f"{today_return_rate:+.2f}%", delta_color="inverse")
col5.metric("累计手续费", f"¥ {total_fee_all:,.2f}")
col6.metric("纯资产 XIRR", xirr_display)

st.markdown(f"### 📈 资产与收益历史走势 {display_title_suffix}")
tab1, tab2 = st.tabs(["累计收益走势", "总资产走势"])

with st.spinner('正在基于摊薄成本法核算历史走势...'):
    if not filtered_trans.empty:
        min_date = filtered_trans['date'].min().date()
        today_date = datetime.now().date()
        port_timeline = pd.DataFrame({'date': pd.date_range(min_date, today_date).date})
        
        port_timeline['total_val'] = 0.0
        port_timeline['total_cost'] = 0.0
        
        for code in unique_codes:
            hist_nav = get_history_data(code)
            code_trans = filtered_trans[filtered_trans['code'] == code].copy()
            code_trans['date'] = code_trans['date'].dt.date
            
            # 使用增量遍历法完美复刻历史摊薄成本
            records = []
            cur_sh = 0.0
            cur_cost = 0.0
            for dt, grp in code_trans.groupby('date'):
                for _, r in grp.iterrows():
                    amt = float(r['amount'])
                    fee = float(r['fee'])
                    sh = float(r['shares'])
                    if r['type'] == 'BUY':
                        cur_sh += sh
                        cur_cost += (amt - fee)
                    elif r['type'] == 'SELL':
                        avg_c = cur_cost / cur_sh if cur_sh > 0 else 0
                        cur_sh -= sh
                        cur_cost -= sh * avg_c
                        if cur_sh <= 0.001: cur_cost = 0.0
                records.append({'date': dt, 'shares': cur_sh, 'pure_cost': cur_cost})
            
            df_state = pd.DataFrame(records)
            code_timeline = pd.merge(port_timeline[['date']], df_state, on='date', how='left')
            code_timeline['shares'] = code_timeline['shares'].ffill().fillna(0)
            code_timeline['pure_cost'] = code_timeline['pure_cost'].ffill().fillna(0)
            
            if not hist_nav.empty:
                code_timeline = pd.merge(code_timeline, hist_nav, on='date', how='left')
                code_timeline['nav'] = code_timeline['nav'].ffill().bfill()
            else:
                code_timeline['nav'] = 1.0 
                
            port_timeline['total_val'] += code_timeline['shares'] * code_timeline['nav']
            port_timeline['total_cost'] += code_timeline['pure_cost']
        
        port_timeline['total_profit'] = port_timeline['total_val'] - port_timeline['total_cost']
        port_timeline.set_index('date', inplace=True)
        
        with tab1: st.line_chart(port_timeline['total_profit'], use_container_width=True)
        with tab2: st.area_chart(port_timeline['total_val'], use_container_width=True)
    else:
        st.info("该平台暂无交易记录，无法绘制走势图")

# ==========================================
# 7. 渲染表格通用样式
# ==========================================
center_css = [dict(selector="th", props=[("text-align", "center")]), dict(selector="td", props=[("text-align", "center")])]
def color_red_green(val):
    if type(val) in [int, float]:
        if val > 0: return 'color: #ff4b4b; font-weight: bold;'
        elif val < 0: return 'color: #09ab3b; font-weight: bold;'
    return ''

st.subheader(f"📊 当前持仓与实时盈亏 {display_title_suffix}")
if holding_rows:
    df_holdings = pd.DataFrame(holding_rows)
    styled_holdings = df_holdings.style.format({
        "持仓份额": "{:,.2f}", "购买均价": "{:,.4f}", "最新单价": "{:,.4f}", "当前市值": "¥ {:,.2f}",
        "持仓本金": "¥ {:,.2f}", "累计手续费": "¥ {:,.2f}", "纯持有收益": "¥ {:,.2f}", "收益率": "{:+.2f}%", "今日收益": "¥ {:+.2f}"
    })
    
    try: styled_holdings = styled_holdings.map(color_red_green, subset=["纯持有收益", "收益率", "今日收益"])
    except AttributeError: styled_holdings = styled_holdings.applymap(color_red_green, subset=["纯持有收益", "收益率", "今日收益"])
        
    styled_holdings = styled_holdings.set_table_styles(center_css)
    st.dataframe(styled_holdings, use_container_width=True)
else:
    st.info("暂无持仓记录或已全部清仓")

# ==========================================
# 8. 单标的下钻深度分析 
# ==========================================
if holding_rows:
    st.divider()
    st.subheader(f"🔍 单只标的下钻分析 {display_title_suffix}")
    
    selected_name = st.selectbox("选择要分析的持仓标的", [row['标的名称'] for row in holding_rows])
    selected_code = next(row['标的代码'] for row in holding_rows if row['标的名称'] == selected_name)
    
    with st.spinner('正在同步历史净值与核算数据...'):
        hist_nav = get_history_data(selected_code)
        
        if not hist_nav.empty:
            code_trans = filtered_trans[filtered_trans['code'] == selected_code].copy()
            code_trans['date'] = pd.to_datetime(code_trans['date']).dt.date
            
            min_date = code_trans['date'].min()
            today_date = datetime.now().date()
            timeline = pd.DataFrame({'date': pd.date_range(min_date, today_date).date})
            
            records = []
            cur_sh = 0.0
            cur_cost = 0.0
            for dt, grp in code_trans.groupby('date'):
                for _, r in grp.iterrows():
                    amt = float(r['amount'])
                    fee = float(r['fee'])
                    sh = float(r['shares'])
                    if r['type'] == 'BUY':
                        cur_sh += sh
                        cur_cost += (amt - fee)
                    elif r['type'] == 'SELL':
                        avg_c = cur_cost / cur_sh if cur_sh > 0 else 0
                        cur_sh -= sh
                        cur_cost -= sh * avg_c
                        if cur_sh <= 0.001: cur_cost = 0.0
                records.append({'date': dt, 'cum_shares': cur_sh, 'cum_cost': cur_cost})
            
            df_state = pd.DataFrame(records)
            timeline = pd.merge(timeline, df_state, on='date', how='left')
            timeline['cum_shares'] = timeline['cum_shares'].ffill().fillna(0)
            timeline['cum_cost'] = timeline['cum_cost'].ffill().fillna(0)
            
            timeline = pd.merge(timeline, hist_nav, on='date', how='left')
            timeline['nav'] = timeline['nav'].ffill().bfill() 
            timeline['market_val'] = timeline['cum_shares'] * timeline['nav']
            timeline['profit'] = timeline['market_val'] - timeline['cum_cost']
            
            d_col1, d_col2 = st.columns(2)
            with d_col1:
                st.markdown("##### 📈 历史净值走势")
                st.line_chart(timeline.set_index('date')['nav'], use_container_width=True)
            with d_col2:
                st.markdown("##### 💰 累计纯收益走势")
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
st.subheader(f"📝 历史交易流水 {display_title_suffix}")
if not filtered_trans.empty:
    df_trans = filtered_trans.sort_values(by="date", ascending=False).copy()
    df_trans['date'] = df_trans['date'].dt.strftime('%Y-%m-%d')
    
    def verify_data(row):
        if row['type'] == 'BUY': expected_amt = (row['shares'] * row['nav']) + row['fee']
        elif row['type'] == 'SELL': expected_amt = (row['shares'] * row['nav']) - row['fee']
        else: return "➖"
        diff = row['amount'] - expected_amt
        return "✅ 账实相符" if abs(diff) <= 0.05 else f"⚠️ 误差 {diff:+.2f} 元"

    df_trans['数据校对'] = df_trans.apply(verify_data, axis=1)
    
    df_trans = df_trans[['date', 'platform', 'code', 'name', 'type', 'nav', 'amount', 'shares', 'fee', '数据校对']]
    df_trans = df_trans.rename(columns={
        'date': '交易日期', 'platform': '交易平台', 'code': '标的代码', 'name': '标的名称', 
        'type': '交易类型', 'nav': '成交净值', 'amount': '发生金额', 'shares': '成交份额', 'fee': '手续费'
    })
    
    styled_trans = df_trans.style.format({"成交净值": "{:,.4f}", "发生金额": "¥ {:,.2f}", "成交份额": "{:,.2f}", "手续费": "¥ {:,.2f}"}).set_table_styles(center_css)
    st.dataframe(styled_trans, use_container_width=True)