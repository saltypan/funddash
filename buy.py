import akshare as ak
import pandas as pd

# 1. 一行代码拉取“易方达沪深300ETF(110020)”的历史每日净值/价格数据
df = ak.fund_etf_fund_info_em(fund="110020", start_date="20230101", end_date="20260101")

# 2. 计算 5 日和 20 日移动平均线 (MA)
df['MA5'] = df['单位净值'].rolling(window=5).mean()
df['MA20'] = df['单位净值'].rolling(window=20).mean()

# 3. 生成交易信号：当 MA5 > MA20 时标记为持仓(1)，反之为空仓(0)
df['Signal'] = 0
df.loc[df['MA5'] > df['MA20'], 'Signal'] = 1

# 4. 查看最近 5 天的数据和信号变化
print(df[['净值日期', '单位净值', 'MA5', 'MA20', 'Signal']].tail(5))