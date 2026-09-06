# -*- coding: utf-8 -*-
"""
全局配置（示例文件）
使用时请复制为 settings.py 并填入真实配置
"""
import os
from pathlib import Path

# 项目根目录
BASE_DIR = Path(__file__).parent.parent

# 数据目录
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
REPORT_DIR = DATA_DIR / "reports"

# 确保目录存在
for d in [DATA_DIR, RAW_DATA_DIR, REPORT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ============== 热门股票配置 ==============
# 涨幅榜抓取数量
HOT_STOCKS_COUNT = 20
# 市场类型：沪深A股（排除北交所新股）
HOT_STOCKS_MARKET = "hs_a"
# 排除的股票代码前缀（北交所 4/8 开头）
EXCLUDE_CODE_PREFIXES = ["4", "8", "92"]

# ============== 股吧爬虫配置 ==============
# 每只股票抓取页数
GUBA_PAGE_COUNT = 5
# 请求间隔（秒）
GUBA_REQUEST_DELAY = 1.5
# 股吧人气排名总数基准（当前A股约5500只，定期更新）
GUBA_RANK_MAX = 5500

# ============== 雪球爬虫配置 ==============
# 雪球 Cookie（需要从浏览器登录后复制 xq_a_token 和 u 的值）
XUEQIU_COOKIE = {
    "xq_a_token": "your_xq_a_token_here",
    "u": "your_u_token_here",
}
# 每只股票抓取数量
XUEQIU_POST_COUNT = 50
# 请求间隔（秒）
XUEQIU_REQUEST_DELAY = 2.5

# ============== 情绪分析配置 ==============
# 情感词典路径
SENTIMENT_DICT_PATH = BASE_DIR / "config" / "sentiment_dict.json"

# ============== 报告配置 ==============
REPORT_TEMPLATE = "report.html"

# ============== 题材映射配置 ==============
THEMES = {
    "AI算力": ["算力", "GPU", "服务器", "光模块", "液冷", "AI芯片", "昇腾", "寒武纪", "海光"],
    "人工智能": ["人工智能", "大模型", "AIGC", "ChatGPT", "GPT", "深度学习", "机器学习"],
    "半导体": ["半导体", "芯片", "集成电路", "晶圆", "封测", "存储", "MCU", "模拟芯片"],
    "新能源": ["新能源", "光伏", "储能", "锂电池", "宁德时代", "比亚迪", "逆变器", "风电"],
    "新能源车": ["新能源车", "电动汽车", "智能驾驶", "自动驾驶", "特斯拉", "蔚来", "小鹏", "理想"],
    "医药": ["医药", "创新药", "CXO", "医疗器械", "生物制药", "疫苗", "中药"],
    "消费": ["消费", "白酒", "食品饮料", "茅台", "五粮液", "零售", "免税"],
    "地产": ["地产", "房地产", "保利", "万科", "碧桂园", "建材", "家居"],
    "金融": ["金融", "银行", "证券", "保险", "券商", "中信证券", "东方财富"],
    "军工": ["军工", "国防", "航空", "航天", "导弹", "无人机", "舰船"],
    "中字头": ["中字头", "国企改革", "央企", "中国XX", "中船", "中铁", "中建"],
    "数字经济": ["数字经济", "数据要素", "数据确权", "东数西算", "信创"],
    "机器人": ["机器人", "人形机器人", "工业机器人", "减速器", "伺服电机"],
    "5G/通信": ["5G", "通信", "运营商", "中兴", "华为", "基站"],
}

# ============== 雪球个股热度配置 ==============
# 热度公式：互动量(70%) + 关注量(30%)，对数缩放
XUEQIU_HEAT_INTERACTION_WEIGHT = 70.0   # 互动量权重
XUEQIU_HEAT_FOLLOW_WEIGHT = 30.0        # 关注量权重
XUEQIU_HEAT_INTERACTION_BASE = 2000.0   # 互动量满分基准（2000互动量=满分）
XUEQIU_HEAT_FOLLOW_BASE = 500000.0      # 关注量满分基准（50万关注量=满分）

# ============== 市场热度（大盘UV指数）配置 ==============
# apppc.com 东方财富网 APP ID
MARKET_HEAT_APP_ID = "your_apppc_app_id"
# UV 指数归一化基准（万）：5000万=0分，10000万=100分
MARKET_HEAT_UV_LOW = 5000.0
MARKET_HEAT_UV_HIGH = 9000.0
# 数据滞后天数（apppc 数据约滞后 6 天）
MARKET_HEAT_LAG_DAYS = 7
# 市场热度数据文件
MARKET_HEAT_FILE = RAW_DATA_DIR / "market_heat.json"

# ============== Deepseek 大模型情绪分析配置 ==============
# API Key：可通过环境变量或直接填写
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "your_deepseek_api_key")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
LLM_BATCH_SIZE = 20          # 每批分析帖子数
LLM_MAX_RETRIES = 3           # 最大重试次数
LLM_REQUEST_DELAY = 0.5       # 请求间隔（秒）
LLM_CONCURRENCY = 3           # 并发线程数
