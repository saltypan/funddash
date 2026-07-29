# 更新时间 / 2026-07-28 1:00 

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

# 确保交易流水文件存在
if not os.path.exists('transactions.csv'):
    df_init = pd.DataFrame(columns=['date', 'platform', 'code', 'name', 'type', 'nav', 'amount', 'shares', 'fee'])
    df_init.to_csv('transactions.csv', index=False)

# ==========================================
# 2. 核心数据与函数定义
# ==========================================
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

def load_transactions():
    df = pd.read_csv('transactions.csv')
    df['date'] = pd.to_datetime(df['date'])
    df['code'] = df['code'].astype(str).str.zfill(6)
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

# ==========================================
# 3. 侧边栏：全局看板筛选 & 智能单笔录入
# ==========================================
with st.sidebar:
    st.header("🔎 全局看板筛选")
    existing_platforms = list(transactions['platform'].dropna().unique())
    platforms_options = ["全盘总览"] + existing_platforms
    selected_platform = st.selectbox("选择要查看的平台/账户", platforms_options)
    
    st.divider()
    
    st.header("➕ 录入新交易")
    t_date = st.date_input("交易日期 (自动匹配当日净值)", datetime.now())
    t_code = st.text_input("标的代码 (输入代码并回车)", key="input_code")
    code_str = str(t_code).strip().zfill(6) if t_code else ""
    
    auto_name = ""
    auto_nav = 0.0000
    
    # 智能名称与净值抓取
    if code_str:
        history_match = transactions[transactions['code'] == code_str]
        if not history_match.empty: auto_name = history_match['name'].iloc[0]
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

    t_platform = st.selectbox("交易平台 ", ["东方财富", "天天基金", "支付宝", "招商银行", "中国银行", "工商银行", "其他"])
    t_name = st.text_input("标的名称 ", value=auto_name)
    t_type = st.selectbox("交易类型", ["BUY", "SELL", "DIVIDEND", "FEE"])
    
    col1, col2 = st.columns([6, 4])
    with col1:
        t_amount = st.number_input("发生金额 (元) ", min_value=0.0, step=100.0, value=0.0)
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
    
    submitted = st.button("✅ 提交交易记录", use_container_width=True)
    
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
            
            updated_transactions = pd.concat([transactions, new_record], ignore_index=True)
            updated_transactions['date'] = pd.to_datetime(updated_transactions['date']).dt.strftime('%Y-%m-%d')
            updated_transactions.to_csv('transactions.csv', index=False)
            
            st.success(f"✅ {t_name} 记录已保存！")
            st.rerun()            
        else:
            st.error("⚠️ 请完善必填项且金额大于0。")

# ==========================================
# 4. 全局数据过滤与核算引擎 (保留核心财务逻辑)
# ==========================================
if selected_platform == "全盘总览":
    filtered_trans = transactions.copy()
    display_title_suffix = "(全盘)"
else:
    filtered_trans = transactions[transactions['platform'] == selected_platform].copy()
    display_title_suffix = f"({selected_platform})"

unique_codes = list(filtered_trans['code'].unique())
live_prices = get_live_prices(unique_codes)

holdings = {}
realized_pnl = {} # 用于记录每只基金的历史已实现盈亏
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
        holdings[c_code] = {
            'name': c_name, 'shares': 0.0, 'pure_cost': 0.0, 
            'total_fee': 0.0, 'total_div': 0.0,
            'first_buy': None, 'cf': [], 'total_buy_cost': 0.0
        }
        realized_pnl[c_code] = 0.0
    
    if c_type == 'BUY':
        if holdings[c_code]['first_buy'] is None:
            holdings[c_code]['first_buy'] = c_date
            
        pure_in = c_amount - c_fee
        cash_flows.append((c_date, -pure_in))
        holdings[c_code]['cf'].append((c_date, -pure_in))
        holdings[c_code]['shares'] += c_shares
        holdings[c_code]['pure_cost'] += pure_in
        holdings[c_code]['total_buy_cost'] += pure_in  
        holdings[c_code]['total_fee'] += c_fee
        
    elif c_type == 'SELL':
        pure_out = c_amount + c_fee
        cash_flows.append((c_date, pure_out))
        holdings[c_code]['cf'].append((c_date, pure_out))
        
        avg_cost = holdings[c_code]['pure_cost'] / holdings[c_code]['shares'] if holdings[c_code]['shares'] > 0 else 0
        cost_sold = c_shares * avg_cost
        
        trade_pnl = (c_amount - c_fee) - cost_sold
        realized_pnl[c_code] += trade_pnl
        
        holdings[c_code]['shares'] -= c_shares
        holdings[c_code]['pure_cost'] -= cost_sold
        
        if holdings[c_code]['shares'] <= 0.001:
            holdings[c_code]['pure_cost'] = 0.0
            holdings[c_code]['shares'] = 0.0
        holdings[c_code]['total_fee'] += c_fee
        
    elif c_type == 'DIVIDEND':
        cash_flows.append((c_date, c_amount))
        holdings[c_code]['cf'].append((c_date, c_amount))
        holdings[c_code]['total_div'] += c_amount

total_market_value = 0.0
total_today_profit = 0.0
holding_rows = []

# 生成动态日期角标
today_d = datetime.now().date()
yest_d = today_d - timedelta(days=1)
today_str = today_d.strftime("%m-%d")
yest_str = yest_d.strftime("%m-%d")

col_today_profit = f"今日收益({today_str})"
col_today_rate = f"今日收益率({today_str})"
col_yest_profit = f"昨日收益({yest_str})"
col_yest_rate = f"昨日收益率({yest_str})"

for code, info in holdings.items():
    if info['shares'] <= 0.001 and info['total_div'] == 0 and realized_pnl.get(code, 0.0) == 0: 
        continue
        
    market_data = live_prices.get(code, {'price': 0.0, 'change_rate': 0.0, 'change_amount': 0.0})
    current_price = market_data['price']
    market_val = info['shares'] * current_price
    
    today_profit = 0.0
    today_profit_rate = 0.0
    yesterday_profit = 0.0
    yesterday_profit_rate = 0.0
    mdd = 0.0
    twr = 0.0
    
    hist = get_history_data(code)
    
    # --- 强时间戳校验：精准核算昨日与今日 ---
    if not hist.empty:
        hist = hist.copy()
        hist['prev_nav'] = hist['nav'].shift(1)
        hist['change_amt'] = hist['nav'] - hist['prev_nav']
        hist['change_rate'] = (hist['change_amt'] / hist['prev_nav']) * 100
        
        last_row = hist.iloc[-1]
        last_hist_date = last_row['date']
        
        # 1. 匹配今日收益
        if last_hist_date == today_d:
            # 基金历史净值已经更新到了今天
            today_profit = info['shares'] * float(last_row['change_amt']) if pd.notna(last_row['change_amt']) else 0.0
            today_profit_rate = float(last_row['change_rate']) if pd.notna(last_row['change_rate']) else 0.0
        elif last_hist_date < today_d and abs(current_price - float(last_row['nav'])) > 0.0001:
            # 历史还是昨天的，但实时拉取的现价变了（说明是盘中正在交易的 ETF 或 股票）
            today_profit = info['shares'] * market_data['change_amount']
            today_profit_rate = market_data['change_rate']
        else:
            # 未更新数据的场外基金，强制归零
            today_profit = 0.0
            today_profit_rate = 0.0
            
        # 2. 匹配昨日收益 (严格按日期在历史库中寻找)
        yest_mask = hist['date'] == yest_d
        if yest_mask.any():
            yest_row = hist[yest_mask].iloc[0]
            if pd.notna(yest_row['change_amt']):
                yesterday_profit = info['shares'] * float(yest_row['change_amt'])
                yesterday_profit_rate = float(yest_row['change_rate'])
                
        # 3. TWR 与 最大回撤
        if info['first_buy']:
            hist_held = hist[hist['date'] >= info['first_buy']].copy()
            if not hist_held.empty:
                hist_held['peak'] = hist_held['nav'].cummax()
                hist_held['drawdown'] = (hist_held['nav'] - hist_held['peak']) / hist_held['peak']
                mdd = float(hist_held['drawdown'].min() * 100)
                
                first_nav = float(hist_held.iloc[0]['nav'])
                if first_nav > 0:
                    twr = ((current_price / first_nav) - 1) * 100
    else:
        # 兜底逻辑
        if current_price > 0:
            today_profit = info['shares'] * market_data['change_amount']
            today_profit_rate = market_data['change_rate']

    total_market_value += market_val
    total_today_profit += today_profit
    
    holding_profit = market_val - info['pure_cost'] + info['total_div']
    total_history_profit = holding_profit + realized_pnl.get(code, 0.0)
    
    profit_rate = (holding_profit / info['pure_cost'] * 100) if info['pure_cost'] > 0 else 0.0
    total_history_rate = (total_history_profit / info['total_buy_cost'] * 100) if info['total_buy_cost'] > 0 else 0.0
    avg_cost = info['pure_cost'] / info['shares'] if info['shares'] > 0 else 0.0
    
    days_held = (datetime.now().date() - info['first_buy']).days if info['first_buy'] else 0
    days_held = max(1, days_held)
    
    base = 1 + profit_rate / 100
    annualized_rate = (base ** (365 / days_held) - 1) * 100 if base > 0 else -100.0
        
    h_cf = info['cf'].copy()
    h_cf.append((datetime.now().date(), market_val))
    try:
        h_xirr = xirr(h_cf)
        h_xirr_val = h_xirr * 100 if h_xirr is not None else 0.0
    except:
        h_xirr_val = 0.0
    
    holding_rows.append({
        "标的代码": code, 
        "标的名称": info['name'], 
        "当前市值": market_val,         
        "持仓本金": info['pure_cost'], 
        "持仓份额": info['shares'],
        "购买均价": avg_cost, 
        "最新单价": current_price, 
        "累计手续费": info['total_fee'], 
        "累计分红": info['total_div'],
        col_today_profit: today_profit,
        col_today_rate: today_profit_rate,
        col_yest_profit: yesterday_profit,
        col_yest_rate: yesterday_profit_rate,
        "持有收益": holding_profit,      
        "持有收益率": profit_rate,
        "总历史收益": total_history_profit, 
        "总历史收益率": total_history_rate,
        "日年化收益": annualized_rate,    
        "XIRR": h_xirr_val,           
        "TWR": twr,                   
        "持有时间": f"{days_held}天",     
        "最大回撤": mdd                
    })

cash_flows.append((datetime.now().date(), total_market_value))
try:
    portfolio_xirr = xirr(cash_flows) if len(cash_flows) > 1 else None
    xirr_display = f"{portfolio_xirr * 100:.2f}%" if portfolio_xirr is not None else "数据不足"
except: xirr_display = "等待更多数据"

total_pure_cost_all = sum(info['pure_cost'] for info in holdings.values())
total_fee_all = sum(info['total_fee'] for info in holdings.values())
total_div_all = sum(info['total_div'] for info in holdings.values())

total_cumulative_profit = total_market_value - total_pure_cost_all + total_div_all
yesterday_market_value = total_market_value - total_today_profit
today_return_rate = (total_today_profit / yesterday_market_value) * 100 if yesterday_market_value > 0 else 0.0

# ==========================================
# 6. 渲染看板与报表
# ==========================================
st.markdown(f"### 📊 核心指标 {display_title_suffix}")
col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("持仓总市值", f"¥ {total_market_value:,.2f}")
col2.metric("持仓总本金 (纯)", f"¥ {total_pure_cost_all:,.2f}")
col3.metric("累计收益(含分红)", f"¥ {total_cumulative_profit:,.2f}", f"{total_cumulative_profit/total_pure_cost_all*100:+.2f}%" if total_pure_cost_all>0 else "0%", delta_color="inverse")
col4.metric("今日收益", f"¥ {total_today_profit:,.2f}", f"{today_return_rate:+.2f}%", delta_color="inverse")
col5.metric("累计分红(手续费)", f"¥ {total_div_all:,.2f}", f"费: {total_fee_all:.2f}", delta_color="off")
col6.metric("全盘 XIRR", xirr_display)

st.markdown(f"### 📈 资产与收益历史走势 {display_title_suffix}")
tab1, tab2 = st.tabs(["累计收益走势", "总资产走势"])

with st.spinner('正在拉取行情与渲染走势图...'):
    if not filtered_trans.empty:
        min_date = filtered_trans['date'].min().date()
        today_date = datetime.now().date()
        port_timeline = pd.DataFrame({'date': pd.date_range(min_date, today_date).date})
        
        port_timeline['total_val'] = 0.0
        port_timeline['total_cost'] = 0.0
        port_timeline['total_div'] = 0.0
        
        for code in unique_codes:
            hist_nav = get_history_data(code)
            code_trans = filtered_trans[filtered_trans['code'] == code].copy()
            code_trans['date'] = code_trans['date'].dt.date
            
            records = []
            cur_sh, cur_cost, cur_div = 0.0, 0.0, 0.0
            for dt, grp in code_trans.groupby('date'):
                for _, r in grp.iterrows():
                    amt, fee, sh = float(r['amount']), float(r['fee']), float(r['shares'])
                    if r['type'] == 'BUY':
                        cur_sh += sh; cur_cost += (amt - fee)
                    elif r['type'] == 'SELL':
                        avg_c = cur_cost / cur_sh if cur_sh > 0 else 0
                        cur_sh -= sh; cur_cost -= sh * avg_c
                        if cur_sh <= 0.001: cur_cost = 0.0
                    elif r['type'] == 'DIVIDEND':
                        cur_div += amt
                records.append({'date': dt, 'shares': cur_sh, 'pure_cost': cur_cost, 'total_div': cur_div})
            
            df_state = pd.DataFrame(records)
            code_timeline = pd.merge(port_timeline[['date']], df_state, on='date', how='left')
            code_timeline['shares'] = code_timeline['shares'].ffill().fillna(0)
            code_timeline['pure_cost'] = code_timeline['pure_cost'].ffill().fillna(0)
            code_timeline['total_div'] = code_timeline['total_div'].ffill().fillna(0)
            
            if not hist_nav.empty:
                code_timeline = pd.merge(code_timeline, hist_nav, on='date', how='left')
                code_timeline['nav'] = code_timeline['nav'].ffill().bfill()
            else:
                code_timeline['nav'] = 1.0 
                
            port_timeline['total_val'] += code_timeline['shares'] * code_timeline['nav']
            port_timeline['total_cost'] += code_timeline['pure_cost']
            port_timeline['total_div'] += code_timeline['total_div']
        
        port_timeline['total_profit'] = port_timeline['total_val'] - port_timeline['total_cost'] + port_timeline['total_div']
        port_timeline.set_index('date', inplace=True)
        
        with tab1: st.line_chart(port_timeline['total_profit'], use_container_width=True)
        with tab2: st.area_chart(port_timeline['total_val'], use_container_width=True)
    else:
        st.info("该平台暂无交易记录，无法绘制走势图")

center_css = [dict(selector="th", props=[("text-align", "center")]), dict(selector="td", props=[("text-align", "center")])]
def color_red_green(val):
    if type(val) in [int, float]:
        if val > 0: return 'color: #ff4b4b; font-weight: bold;'
        elif val < 0: return 'color: #09ab3b; font-weight: bold;'
    return ''

st.subheader(f"📊 当前持仓与实时盈亏 {display_title_suffix}")
if holding_rows:
    df_holdings = pd.DataFrame(holding_rows)
    df_holdings = df_holdings.sort_values(by="当前市值", ascending=False).reset_index(drop=True)
    
    # 【核心修改】：将前两列设置为索引，Streamlit 渲染时会自动将其冻结在左侧
    df_holdings.set_index(["标的代码", "标的名称"], inplace=True)
    
    # 动态匹配刚生成的带日期的列名
    format_dict = {
        "当前市值": "¥ {:,.2f}",
        "持仓本金": "¥ {:,.2f}", 
        "持仓份额": "{:,.2f}", 
        "购买均价": "{:,.4f}", 
        "最新单价": "{:,.4f}", 
        "累计手续费": "¥ {:,.2f}", 
        "累计分红": "¥ {:,.2f}", 
        col_today_profit: "¥ {:+.2f}",
        col_today_rate: "{:+.2f}%",
        col_yest_profit: "¥ {:+.2f}",
        col_yest_rate: "{:+.2f}%",
        "持有收益": "¥ {:+.2f}", 
        "持有收益率": "{:+.2f}%",
        "总历史收益": "¥ {:+.2f}", 
        "总历史收益率": "{:+.2f}%",
        "日年化收益": "{:+.2f}%",
        "XIRR": "{:+.2f}%",
        "TWR": "{:+.2f}%",
        "最大回撤": "{:.2f}%"
    }
    styled_holdings = df_holdings.style.format(format_dict)
    
    # 全部核心盈亏指标均应用红绿着色
    color_cols = [
        col_today_profit, col_today_rate, 
        col_yest_profit, col_yest_rate,
        "持有收益", "持有收益率", 
        "总历史收益", "总历史收益率", 
        "日年化收益", "XIRR", "TWR"
    ]
    try: 
        styled_holdings = styled_holdings.map(color_red_green, subset=color_cols)
    except AttributeError: 
        styled_holdings = styled_holdings.applymap(color_red_green, subset=color_cols)
        
    st.dataframe(styled_holdings.set_table_styles(center_css), use_container_width=True)
else:
    st.info("暂无持仓记录")

if holding_rows:
    st.divider()
    st.subheader(f"🔍 单只标的下钻分析 {display_title_suffix}")
    valid_names = [row['标的名称'] for row in holding_rows]
    if valid_names:
        selected_name = st.selectbox("选择要分析的持仓标的", valid_names)
        selected_code = next(row['标的代码'] for row in holding_rows if row['标的名称'] == selected_name)
        
        with st.spinner('同步下钻数据中...'):
            hist_nav = get_history_data(selected_code)
            if not hist_nav.empty:
                code_trans = filtered_trans[filtered_trans['code'] == selected_code].copy()
                code_trans['date'] = pd.to_datetime(code_trans['date']).dt.date
                min_date = code_trans['date'].min()
                timeline = pd.DataFrame({'date': pd.date_range(min_date, datetime.now().date()).date})
                
                records = []
                cur_sh, cur_cost, cur_div = 0.0, 0.0, 0.0
                for dt, grp in code_trans.groupby('date'):
                    for _, r in grp.iterrows():
                        amt, fee, sh = float(r['amount']), float(r['fee']), float(r['shares'])
                        if r['type'] == 'BUY': cur_sh += sh; cur_cost += (amt - fee)
                        elif r['type'] == 'SELL':
                            avg_c = cur_cost / cur_sh if cur_sh > 0 else 0
                            cur_sh -= sh; cur_cost -= sh * avg_c
                            if cur_sh <= 0.001: cur_cost = 0.0
                        elif r['type'] == 'DIVIDEND': cur_div += amt
                    records.append({'date': dt, 'cum_shares': cur_sh, 'cum_cost': cur_cost, 'cum_div': cur_div})
                
                df_state = pd.DataFrame(records)
                timeline = pd.merge(timeline, df_state, on='date', how='left').ffill().fillna(0)
                timeline = pd.merge(timeline, hist_nav, on='date', how='left')
                timeline['nav'] = timeline['nav'].ffill().bfill() 
                timeline['market_val'] = timeline['cum_shares'] * timeline['nav']
                timeline['profit'] = timeline['market_val'] - timeline['cum_cost'] + timeline['cum_div']
                
                d_col1, d_col2 = st.columns(2)
                with d_col1:
                    st.markdown("##### 📈 历史净值走势")
                    st.line_chart(timeline.set_index('date')['nav'], use_container_width=True)
                with d_col2:
                    st.markdown("##### 💰 累计收益走势 (含分红)")
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
                st.warning("暂无该标的的历史净值数据")

st.divider()
st.subheader(f"📝 历史交易流水 {display_title_suffix}")
if not filtered_trans.empty:
    df_trans = filtered_trans.sort_values(by="date", ascending=False).copy()
    df_trans['date'] = df_trans['date'].dt.strftime('%Y-%m-%d')
    def verify_data(row):
        if row['type'] == 'BUY': expected_amt = (row['shares'] * row['nav']) + row['fee']
        elif row['type'] == 'SELL': expected_amt = (row['shares'] * row['nav']) - row['fee']
        elif row['type'] == 'DIVIDEND': return "➖ 分红"
        else: return "➖"
        diff = row['amount'] - expected_amt
        return "✅ 账实相符" if abs(diff) <= 0.05 else f"⚠️ 误差 {diff:+.2f} 元"

    df_trans['数据校对'] = df_trans.apply(verify_data, axis=1)
    df_trans = df_trans[['date', 'platform', 'code', 'name', 'type', 'nav', 'amount', 'shares', 'fee', '数据校对']]
    df_trans = df_trans.rename(columns={'date': '交易日期', 'platform': '交易平台', 'code': '标的代码', 'name': '标的名称', 'type': '交易类型', 'nav': '成交净值', 'amount': '发生金额', 'shares': '成交份额', 'fee': '手续费'})
    styled_trans = df_trans.style.format({"成交净值": "{:,.4f}", "发生金额": "¥ {:,.2f}", "成交份额": "{:,.2f}", "手续费": "¥ {:,.2f}"}).set_table_styles(center_css)
    st.dataframe(styled_trans, use_container_width=True)
