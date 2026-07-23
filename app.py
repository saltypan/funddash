import streamlit as st
import pandas as pd
import efinance as ef
from pyxirr import xirr
from datetime import datetime
import os
import requests
import json

# ==========================================
# 1. 页面配置与初始化
# ==========================================
st.set_page_config(page_title="个人投资理财看板", layout="wide")
st.title("📈 个人投资看板 & XIRR 实时追踪")

# 确保初始 CSV 文件存在
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
        t_type = st.selectbox("交易类型", ["BUY", "SELL", "FEE"], 
                              help="BUY=买入/定投；SELL=卖出/分红；FEE=扣除单独手续费")
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
# 3. 数据读取与实时行情获取
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
        if not code_str:
            continue
            
        try:
            quote_df = ef.stock.get_realtime_quotes(code_str)
            if quote_df is not None and not quote_df.empty:
                prices[code_str] = float(quote_df['最新价'].iloc[0])
                continue 
        except Exception:
            pass
            
        try:
            url = f"http://api.fund.eastmoney.com/f10/lsjz?fundCode={code_str}&pageIndex=1&pageSize=1"
            headers = {'Referer': 'http://fund.eastmoney.com/'}
            res = requests.get(url, headers=headers, timeout=5)
            data = json.loads(res.text)
            if data.get('Data') and data['Data'].get('LSJZList'):
                prices[code_str] = float(data['Data']['LSJZList'][0]['DWJZ'])
                continue
        except Exception as e:
            pass
            
        prices[code_str] = 0.0 
                
    return prices

unique_codes = list(transactions['code'].unique())
live_prices = get_live_prices(unique_codes)

# ==========================================
# 4. 计算持仓明细与 XIRR
# ==========================================
# 这里的定义非常关键，也是刚才报错缺失的部分
holdings = {}
cash_flows = []

# 遍历计算全盘资金流向和持仓成本
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
holding_rows = []

for code, info in holdings.items():
    if info['shares'] <= 0.001:  
        continue
        
    current_price = live_prices.get(code, 0.0)
    market_val = info['shares'] * current_price
    total_market_value += market_val
    profit = market_val - info['total_cost']
    profit_rate = (profit / info['total_cost'] * 100) if info['total_cost'] > 0 else 0.0
    
    avg_cost = info['total_cost'] / info['shares'] if info['shares'] > 0 else 0.0
    
    holding_rows.append({
        "标的代码": code,
        "标的名称": info['name'],
        "持仓份额": info['shares'],
        "购买均价": avg_cost,          
        "最新单价": current_price,
        "当前市值": market_val,
        "持仓本金": info['total_cost'],
        "持有收益": profit,
        "收益率": profit_rate        
    })

# 压入今天的一笔虚拟全平仓现金流以计算 XIRR
cash_flows.append((datetime.now().date(), total_market_value))

try:
    portfolio_xirr = xirr(cash_flows)
    xirr_display = f"{portfolio_xirr * 100:.2f}%" if portfolio_xirr is not None else "数据不足"
except Exception:
    xirr_display = "等待更多数据"


# ==========================================
# 5. 渲染前端 Dashboard
# ==========================================
col1, col2, col3 = st.columns(3)
col1.metric("持仓总市值 (CNY)", f"¥ {total_market_value:,.2f}")
col2.metric("组合累计投入本金", f"¥ {sum(info['total_cost'] for info in holdings.values() if info['shares'] > 0.001):,.2f}")
col3.metric("全盘 XIRR 年化收益率", xirr_display)

center_css = [
    dict(selector="th", props=[("text-align", "center")]),
    dict(selector="td", props=[("text-align", "center")])
]

# 红涨绿跌颜色映射逻辑
def color_red_green(val):
    if type(val) in [int, float]:
        if val > 0:
            return 'color: #ff4b4b; font-weight: bold;'
        elif val < 0:
            return 'color: #09ab3b; font-weight: bold;'
    return ''

st.subheader("📊 当前持仓与实时盈亏")
if holding_rows:
    df_holdings = pd.DataFrame(holding_rows)
    styled_holdings = df_holdings.style.format({
        "持仓份额": "{:,.2f}",
        "购买均价": "{:,.4f}",  
        "最新单价": "{:,.4f}",  
        "当前市值": "¥ {:,.2f}",
        "持仓本金": "¥ {:,.2f}",
        "持有收益": "¥ {:,.2f}",
        "收益率": "{:+.2f}%"
    })
    
    try:
        styled_holdings = styled_holdings.map(color_red_green, subset=["持有收益", "收益率"])
    except AttributeError:
        styled_holdings = styled_holdings.applymap(color_red_green, subset=["持有收益", "收益率"])
        
    styled_holdings = styled_holdings.set_table_styles(center_css)
    
    st.dataframe(styled_holdings, use_container_width=True)
else:
    st.info("暂无持仓记录或已全部清仓")

st.subheader("📝 历史交易流水 (含数据校验)")
if not transactions.empty:
    df_trans = transactions.sort_values(by="date", ascending=False).copy()
    df_trans['date'] = df_trans['date'].dt.strftime('%Y-%m-%d')
    
    def verify_data(row):
        if row['type'] == 'BUY':
            expected_amt = (row['shares'] * row['nav']) + row['fee']
        elif row['type'] == 'SELL':
            expected_amt = (row['shares'] * row['nav']) - row['fee']
        else:
            return "➖"
        
        diff = row['amount'] - expected_amt
        
        if abs(diff) <= 0.05:
            return "✅ 账实相符"
        else:
            return f"⚠️ 误差 {diff:+.2f} 元"

    df_trans['数据校对'] = df_trans.apply(verify_data, axis=1)
    
    df_trans = df_trans[['date', 'code', 'name', 'type', 'nav', 'amount', 'shares', 'fee', '数据校对']]
    
    df_trans = df_trans.rename(columns={
        'date': '交易日期',
        'code': '标的代码',
        'name': '标的名称',
        'type': '交易类型',
        'nav': '成交净值',
        'amount': '发生金额',
        'shares': '成交份额',
        'fee': '手续费'
    })
    
    styled_trans = df_trans.style.format({
        "成交净值": "{:,.4f}",
        "发生金额": "¥ {:,.2f}",
        "成交份额": "{:,.2f}",
        "手续费": "¥ {:,.2f}"
    }).set_table_styles(center_css)
    
    st.dataframe(styled_trans, use_container_width=True)
else:
    st.write("请在左侧边栏录入第一笔交易！")