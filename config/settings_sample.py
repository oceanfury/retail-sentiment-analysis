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
# 每只股票目标帖子条数上限（翻页达到即可提前停止）
XUEQIU_POST_COUNT = 30
# 服务端单页硬上限，实测传更大的值也只返回 20 条，不要调大
XUEQIU_PAGE_SIZE = 20
# 自选股单只翻页上限
XUEQIU_MAX_PAGES = 2
# 热股单只翻页上限（热股帖子只喂市场级聚合指标，1 页够用）
XUEQIU_HOT_MAX_PAGES = 1
# 按日期补采历史数据时的翻页上限。历史日期要往后翻很多页才够得着，
# 但上限不能盲目调高——雪球按 IP 限流，页数直接换算成等待时间。
# 实际通常在翻到「整页都早于目标日」时就提前停止，用不到这个上限。
XUEQIU_BACKFILL_MAX_PAGES = 6
# 补采目标日距今超过这么多天就告警：股吧列表翻页有上限，太久远的日期可能翻不到
MAX_BACKFILL_DAYS = 7

# --- 限流控制 ---
# 雪球按 IP 限流，实测约 4 次请求 / 30 秒，超限返回 400 或 JS 挑战页，
# 封禁约 32 秒后自动恢复。下列参数按此标定，调快会触发封禁。
XUEQIU_RATE_LIMIT_MAX_REQUESTS = 4      # 窗口内允许的请求数
XUEQIU_RATE_LIMIT_WINDOW = 30.0         # 滑动窗口（秒）
XUEQIU_MIN_REQUEST_INTERVAL = 8.0       # 最小请求间隔（30/4=7.5，留余量）
XUEQIU_REQUEST_JITTER = 1.5             # 间隔随机抖动上限（秒）
XUEQIU_RATE_LIMIT_RETRY_WAIT = 35.0     # 命中限流后重试等待（需 > 32 秒恢复窗）
XUEQIU_MAX_RATE_LIMIT_RETRIES = 2       # 单页最大重试次数
XUEQIU_RATE_LIMIT_SLOWDOWN = 1.5        # 命中后全局放慢倍数
XUEQIU_MAX_REQUEST_INTERVAL = 20.0      # 放慢后的间隔上限（秒）
XUEQIU_COLLECT_TIME_BUDGET = 1200.0     # 雪球采集阶段墙钟预算（秒），超时停止翻页
XUEQIU_PAGE_RENDER_WAIT = 5.0           # 个股页导航后等待渲染（秒）

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
# 互动量满分基准。原为 2000，按「每股约 10 条帖子」标定；
# 2026-09 雪球采集改为翻页取全量后帖子数上升，同步上调以保持区分度
# （否则热门股互动得分会普遍撞上 70 分上限）。新旧基准的热度不可直接比较。
XUEQIU_HEAT_INTERACTION_BASE = 5000.0
XUEQIU_HEAT_FOLLOW_BASE = 500000.0      # 关注量满分基准（50万关注量=满分）

# ============== 市场热度（大盘UV指数）配置 ==============
# apppc.com 东方财富网 APP ID
MARKET_HEAT_APP_ID = "3Eves1NRcX10yZ"
# UV 指数归一化基准（万）：5000万=0分，10000万=100分
MARKET_HEAT_UV_LOW = 5000.0
MARKET_HEAT_UV_HIGH = 9000.0
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
LLM_MAX_TOKENS = 4000         # 单次响应上限（含思维链）
# 推理模型(如 deepseek-flash)的思维链会计入 max_tokens，可能挤掉正文，
# 情绪分类用不上思维链，默认关闭；若换成非推理模型此参数会被忽略
LLM_DISABLE_THINKING = True
LLM_MIN_BATCH_SIZE = 5        # 输出截断时拆批的下限，低于此不再拆
LLM_MAX_SPLIT_DEPTH = 2       # 拆批最大递归层数
LLM_TIMEOUT = 120             # 单次请求超时（秒）
