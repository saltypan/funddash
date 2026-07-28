import streamlit as st
import pandas as pd
import akshare as ak
from datetime import date, timedelta
from pyxirr import xirr
import sqlite3
import re
import time

# ==========================================
# 0. 本地数据库初始化 (增加时间与资金参数)
# ==========================================
DB_NAME = "fund_archive.db"

def init_archive_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # 新增了开始时间、结束时间、定投期数、单次金额字段
    c.execute('''
        CREATE TABLE IF NOT EXISTS run_history (
            测算时间 TEXT,
            基金代码 TEXT,
            基金名称 TEXT,
            开始时间 TEXT,
            结束时间 TEXT,
            定投期数 INTEGER,
            单次金额 REAL,
            累计投入本金 REAL,
            期末总市值 REAL,
            绝对收益率 REAL,
            真实年化_XIRR REAL,
            基金涨幅_TWR REAL,
            区间最大回撤 REAL
        )
    ''')
    conn.commit()
    conn.close()

def save_to_archive(df_results):
    if df_results.empty: return
    conn = sqlite3.connect(DB_NAME)
    
    # 增加插入时的当前时间戳
    df_db = df_results.copy()
    df_db.insert(0, "测算时间", pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    # 将 DataFrame 的列名映射到数据库表的字段名
    db_df = df_db.rename(columns={
        "单次金额(元)": "单次金额",
        "扣款次数": "定投期数",
        "累计投入(元)": "累计投入本金", 
        "期末市值(元)": "期末总市值",
        "真实年化(XIRR)": "真实年化_XIRR", 
        "基金涨幅(TWR)": "基金涨幅_TWR", 
        "最大回撤": "区间最大回撤"
    })
    
    # 仅保留数据库支持的列进行保存
    columns_to_keep = ["测算时间", "基金代码", "基金名称", "开始时间", "结束时间", "定投期数", "单次金额", "累计投入本金", "期末总市值", "绝对收益率", "真实年化_XIRR", "基金涨幅_TWR", "区间最大回撤"]
    db_df = db_df[[col for col in columns_to_keep if col in db_df.columns]]
    
    db_df.to_sql("run_history", conn, if_exists="append", index=False)
    conn.close()

def load_archive():
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql("SELECT * FROM run_history ORDER BY 测算时间 DESC", conn)
    conn.close()
    return df

# ==========================================
# 1. 实时数据获取与通用工具函数
# ==========================================
@st.cache_data(ttl=3600)
def get_fund_name_and_nav(fund_code):
    try:
        fund_list = ak.fund_name_em()
        fund_info = fund_list[fund_list['基金代码'] == fund_code]
        fund_name = fund_info['基金简称'].values[0] if not fund_info.empty else "未知基金"
        
        nav_data = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        nav_data['净值日期'] = pd.to_datetime(nav_data['净值日期']).dt.date
        nav_data = nav_data.sort_values('净值日期')
        return fund_name, nav_data
    except Exception:
        return None, None

def color_red_green(val):
    if pd.isna(val): return ''
    if isinstance(val, (int, float)):
        if val > 0: return 'color: #ff4d4f; font-weight: bold;'
        elif val < 0: return 'color: #389e0d; font-weight: bold;'
    return 'color: gray;'

# --- 核心：获取有效扣款日期列表 ---
def get_valid_dates(nav_data, start_date, end_date=None, freq_type="每日", weekday="周一", monthday=1, max_count=None):
    df = nav_data[nav_data['净值日期'] >= start_date]
    if df.empty: return []
    
    week_map = {"周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4}
    valid_dates = []
    actual_dates_set = set()
    
    curr = start_date
    last_avail_date = df['净值日期'].iloc[-1]
    
    while curr <= last_avail_date:
        if end_date and curr > end_date: break
        if max_count and len(valid_dates) >= max_count: break
            
        is_target = False
        if freq_type == "每日": is_target = True
        elif freq_type == "每周" and curr.weekday() == week_map.get(weekday, 0): is_target = True
        elif freq_type == "每月" and curr.day == monthday: is_target = True
            
        if is_target:
            avail = df[df['净值日期'] >= curr]
            if not avail.empty:
                actual_d = avail.iloc[0]['净值日期']
                if actual_d not in actual_dates_set:
                    valid_dates.append(actual_d)
                    actual_dates_set.add(actual_d)
        curr += timedelta(days=1)
        
    return valid_dates

# --- 核心：根据日期列表执行模拟 ---
def simulate_by_dates(nav_data, target_dates, dca_amount, start_date, end_date=None):
    if not target_dates: return None
    
    period_start = start_date
    period_end = end_date if end_date else target_dates[-1]
    mask = (nav_data['净值日期'] >= period_start) & (nav_data['净值日期'] <= period_end)
    period_nav = nav_data.loc[mask].copy()
    
    if period_nav.empty: return None
    
    roll_max = period_nav['单位净值'].cummax()
    max_drawdown = ((period_nav['单位净值'] - roll_max) / roll_max).min()
    
    dates, cash_flows = [], []
    total_shares = 0.0
    
    for td in target_dates:
        row = period_nav[period_nav['净值日期'] == td]
        if not row.empty:
            nav_value = row.iloc[0]['单位净值']
            total_shares += dca_amount / nav_value
            dates.append(td)
            cash_flows.append(-dca_amount)
            
    final_nav = period_nav.iloc[-1]['单位净值']
    final_value = total_shares * final_nav
    dates.append(period_nav.iloc[-1]['净值日期'])
    cash_flows.append(final_value) 
    
    total_invested = len(target_dates) * dca_amount
    absolute_return = (final_value - total_invested) / total_invested if total_invested > 0 else 0
    
    try: calc_xirr = xirr(dates, cash_flows)
    except: calc_xirr = 0.0
        
    start_nav = period_nav.iloc[0]['单位净值']
    calc_twr = (final_nav - start_nav) / start_nav if start_nav > 0 else 0
    
    return {
        "开始时间": period_start.strftime("%Y-%m-%d"),
        "结束时间": period_end.strftime("%Y-%m-%d"),
        "单次金额(元)": dca_amount,
        "扣款次数": len(target_dates),
        "累计投入(元)": total_invested,
        "期末市值(元)": final_value,
        "绝对收益率": absolute_return,
        "真实年化(XIRR)": calc_xirr,
        "基金涨幅(TWR)": calc_twr,
        "最大回撤": max_drawdown
    }

# ==========================================
# 页面布局与主逻辑
# ==========================================
st.set_page_config(page_title="全景定投分析系统", layout="wide")
init_archive_db()

st.title("📊 全景定投测算与策略分析系统")

tab1, tab2 = st.tabs(["批量测算与存档", "深度定投策略对比"])

# ==========================================
# TAB 1: 批量测算与存档
# ==========================================
with tab1:
    col_t1_1, col_t1_2 = st.columns([1, 4]) # 调整列宽让表格有更大空间
    with col_t1_1:
        st.subheader("⚙️ 批量参数配置")
        fund_codes_input = st.text_area("输入基金代码 (支持批量粘贴)", value="015453\n110020\n110007", height=120)
        start_date = st.date_input("开始日期", value=date(2023, 1, 1))
        end_date = st.date_input("结束日期", value=date.today())
        dca_amount = st.number_input("单次扣款 (元)", value=1000.0, step=100.0)
        
        dca_freq_type = st.selectbox("频率维度", ["每日", "每周", "每月"])
        dca_weekday, dca_monthday = "周一", 1
        if dca_freq_type == "每周": dca_weekday = st.selectbox("选择周几", ["周一", "周二", "周三", "周四", "周五"])
        elif dca_freq_type == "每月": dca_monthday = st.number_input("选择每月几号", min_value=1, max_value=28, value=1)
        
        run_batch = st.button("🚀 开始批量测算", type="primary", use_container_width=True)

    with col_t1_2:
        if run_batch:
            raw_codes = list(dict.fromkeys(re.findall(r'\d{6}', fund_codes_input)))
            if not raw_codes:
                st.warning("未检测到有效的代码。")
            else:
                progress_bar = st.progress(0)
                status_text = st.empty()
                results = []
                
                for i, code in enumerate(raw_codes):
                    status_text.text(f"正在抓取并测算: {code} ... ({i+1}/{len(raw_codes)})")
                    f_name, n_data = get_fund_name_and_nav(code)
                    if n_data is not None and not n_data.empty:
                        v_dates = get_valid_dates(n_data, start_date, end_date, dca_freq_type, dca_weekday, dca_monthday)
                        res = simulate_by_dates(n_data, v_dates, dca_amount, start_date, end_date)
                        if res:
                            res = {"基金代码": code, "基金名称": f_name, **res, "状态": "成功"}
                            results.append(res)
                    progress_bar.progress((i + 1) / len(raw_codes))
                    time.sleep(0.2) 
                    
                status_text.success("🎉 批量测算完成！")
                df_res = pd.DataFrame(results)
                if not df_res.empty:
                    # 去除状态列进行保存展示，保留了开始结束时间、单次金额、扣款次数
                    display_df = df_res.drop(columns=["状态"], errors="ignore")
                    save_to_archive(display_df)
                    
                    fmt = {
                        "单次金额(元)": "¥{:,.2f}", 
                        "累计投入(元)": "¥{:,.2f}", 
                        "期末市值(元)": "¥{:,.2f}", 
                        "绝对收益率": "{:.2%}", 
                        "真实年化(XIRR)": "{:.2%}", 
                        "基金涨幅(TWR)": "{:.2%}", 
                        "最大回撤": "{:.2%}"
                    }
                    st.dataframe(
                        display_df.style.format(fmt).map(color_red_green, subset=["绝对收益率", "真实年化(XIRR)", "基金涨幅(TWR)", "最大回撤"]), 
                        hide_index=True, 
                        use_container_width=True
                    )

    st.divider()

    # --- 存档数据查看区 (支持动态调整) ---
    with st.expander("🗄️ 点击查看历史测算存档 (支持实时排序与过滤)", expanded=True):
        history_df = load_archive()
        if history_df.empty:
            st.info("暂无历史测算记录，进行一次测算后即可在这里查看。")
        else:
            st.markdown("💡 **操作提示**：你可以点击表头进行**排序**，或者将鼠标悬停在列名上点击出现的汉堡图标进行**过滤** (例如过滤出 XIRR > 10% 的记录)。")
            
            # 使用更强大的 st.dataframe 配置参数，开启原生列过滤功能
            format_dict = {
                "单次金额": "¥{:,.2f}",
                "累计投入本金": "¥{:,.2f}",
                "期末总市值": "¥{:,.2f}",
                "绝对收益率": "{:.2%}",
                "真实年化_XIRR": "{:.2%}",
                "基金涨幅_TWR": "{:.2%}",
                "区间最大回撤": "{:.2%}"
            }
            styled_history = history_df.style.format(format_dict).map(
                color_red_green, 
                subset=["绝对收益率", "真实年化_XIRR", "基金涨幅_TWR", "区间最大回撤"]
            )
            
            st.dataframe(
                styled_history, 
                hide_index=True, 
                use_container_width=True,
                height=400
            )

# ==========================================
# TAB 2: 深度定投策略对比
# ==========================================
with tab2:
    st.markdown("### 🔍 频次与资金利用率对比分析")
    col_t2_1, col_t2_2 = st.columns([1, 3])
    
    with col_t2_1:
        st.subheader("⚙️ 对比参数")
        comp_fund = st.text_input("对比基金代码 (单只)", value="015453")
        comp_start_date = st.date_input("对比起始日期", value=date(2023, 1, 1), key="c_start")
        
        comp_mode = st.radio("选择对比模式", [
            "1. 同资金量 (固定总预算, 对比频率)", 
            "2. 同定投次数 (固定期数, 对比时间跨度)"
        ])
        
        if "同资金量" in comp_mode:
            comp_end_date = st.date_input("对比结束日期", value=date.today(), key="c_end")
            total_budget = st.number_input("区间总投入预算 (元)", value=120000.0, step=10000.0)
            comp_param = {"mode": 1, "end_date": comp_end_date, "total": total_budget}
        else:
            total_times = st.number_input("固定扣款次数", min_value=10, value=50, step=10)
            single_amt = st.number_input("单次扣款金额 (元)", value=1000.0, step=100.0)
            comp_param = {"mode": 2, "times": total_times, "amount": single_amt}
            
        run_comp = st.button("⚖️ 生成对比报告", type="primary", use_container_width=True)

    with col_t2_2:
        if run_comp:
            f_name, n_data = get_fund_name_and_nav(comp_fund)
            if n_data is None or n_data.empty:
                st.error("获取基金数据失败。")
            else:
                st.success(f"当前分析标的：**{f_name} ({comp_fund})**")
                comp_results = []
                
                if comp_param["mode"] == 1:
                    e_date = comp_param["end_date"]
                    t_budget = comp_param["total"]
                    
                    for freq, label in [("每日", "日定投"), ("每周", "周定投 (周一)"), ("每月", "月定投 (1号)")]:
                        v_dates = get_valid_dates(n_data, comp_start_date, e_date, freq, "周一", 1)
                        if v_dates:
                            dca_amt = t_budget / len(v_dates)
                            res = simulate_by_dates(n_data, v_dates, dca_amt, comp_start_date, e_date)
                            if res:
                                comp_results.append({"策略": label, **res})
                
                elif comp_param["mode"] == 2:
                    t_times = comp_param["times"]
                    s_amt = comp_param["amount"]
                    
                    for freq, label in [("每日", "日定投"), ("每周", "周定投 (周一)"), ("每月", "月定投 (1号)")]:
                        v_dates = get_valid_dates(n_data, comp_start_date, None, freq, "周一", 1, max_count=t_times)
                        if len(v_dates) < t_times:
                            st.warning(f"历史数据不足以支撑 {t_times} 次 {label}，仅测算 {len(v_dates)} 次。")
                        if v_dates:
                            actual_end = v_dates[-1]
                            res = simulate_by_dates(n_data, v_dates, s_amt, comp_start_date, actual_end)
                            if res:
                                comp_results.append({"策略": f"{label} ({comp_start_date} 至 {actual_end})", **res})
                
                if comp_results:
                    df_comp = pd.DataFrame(comp_results)
                    fmt = {"单次金额(元)": "¥{:,.2f}", "累计投入(元)": "¥{:,.2f}", "期末市值(元)": "¥{:,.2f}", "绝对收益率": "{:.2%}", "真实年化(XIRR)": "{:.2%}", "基金涨幅(TWR)": "{:.2%}", "最大回撤": "{:.2%}"}
                    st.dataframe(df_comp.style.format(fmt).map(color_red_green, subset=["绝对收益率", "真实年化(XIRR)", "基金涨幅(TWR)", "最大回撤"]), hide_index=True, use_container_width=True)