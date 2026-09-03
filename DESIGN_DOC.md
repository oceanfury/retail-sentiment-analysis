# 散户情绪分析系统 — 详细设计文档

> **版本**：v1.0 | **更新日期**：2026-09-03 | **适用项目**：Retail\_sentiment

***

## 目录

1. [项目概述](#1-项目概述)
2. [系统架构](#2-系统架构)
3. [完整工作流程](#3-完整工作流程)
4. [数据采集模块](#4-数据采集模块)
5. [情绪分析引擎](#5-情绪分析引擎)
6. [指标计算逻辑](#6-指标计算逻辑)
7. [多维周期状态模型](#7-多维周期状态模型)
8. [数据存储模块](#8-数据存储模块)
9. [报告生成模块](#9-报告生成模块)
10. [自选股模块](#10-自选股模块)
11. [历史趋势模块](#11-历史趋势模块)
12. [配置参数速查](#12-配置参数速查)
13. [阈值与常量速查](#13-阈值与常量速查)

***

## 1. 项目概述

### 1.1 目标

采集东方财富股吧和雪球两大散户聚集平台的讨论数据，基于词典法情感分析计算个股情绪指数、热度评分和分歧度，最终生成可视化 HTML 日报，辅助判断散户情绪走向。

### 1.2 核心特点

- **双平台独立**：股吧和雪球分别采集、独立计算、分别生成报告

- **词典法情感分析**：自建情感词典，支持修饰词、否定词、歧义词上下文判断

- **当日帖子过滤**：只分析当天发布的帖子，避免历史数据干扰

- **增量采集**：支持一天内多次运行，帖子自动去重合并

- **自选股监控**：独立配置关注股票，平台隔离计算

- **历史趋势**：90天滚动存储，ECharts 趋势图展示

***

## 2. 系统架构

```
Retail_sentiment/
├── main.py                     # 主入口，编排全流程
├── config/
│   ├── settings.py             # 全局配置（路径、数量、延迟、Cookie）
│   ├── watchlist.json          # 自选股配置
│   └── sentiment_dict.json     # 情感词典
├── collectors/
│   ├── hot_stocks.py           # 热门股票列表获取（股吧人气榜）
│   ├── guba_crawler.py         # 股吧帖子采集
│   └── xueqiu_crawler.py       # 雪球帖子采集
├── analysis/
│   ├── sentiment.py            # 情绪分析引擎（单帖打分）
│   ├── metrics.py              # 指标计算（个股/市场级）
│   └── cycle_model.py          # 多维周期状态模型
├── storage/
│   └── data_store.py           # 数据读写（JSON/CSV/历史快照）
├── report/
│   ├── report_generator.py     # 报告渲染
│   └── templates/
│       └── report.html         # Jinja2 HTML 模板
├── browsers/                   # Playwright Chromium（平台相关）
└── data/
    ├── raw/                    # 原始数据 + 聚合数据 + 历史
    └── reports/                # 生成的 HTML 报告
```

### 2.1 技术栈

| 层       | 技术                                              |
| ------- | ----------------------------------------------- |
| 数据采集    | Playwright（无头 Chromium）+ curl\_cffi（东方财富行情 API） |
| HTML 解析 | BeautifulSoup4 + lxml                           |
| 情绪分析    | 自建情感词典 + 规则匹配（jieba 分词辅助）                       |
| 指标计算    | 纯 Python 加权/简单平均 + 对数缩放                         |
| 数据存储    | JSON（主要）+ CSV（导出）                               |
| 报告渲染    | Jinja2 模板引擎 + ECharts 图表                        |

***

## 3. 完整工作流程

### 3.1 全量运行流程（`python main.py`）

```
┌─────────────────────────────────────────────────────┐
│  Step 1: 获取热门股票                                │
│  ├─ 股吧人气榜 → get_hot_stocks_from_gainers()       │
│  └─ 雪球热股榜 → get_hot_stocks_from_xueqiu()        │
├─────────────────────────────────────────────────────┤
│  Step 2: 采集帖子                                    │
│  ├─ 股吧热门股帖子 → fetch_guba_posts()              │
│  ├─ 雪球热门股帖子 → fetch_xueqiu_posts()             │
│  ├─ 股吧自选股帖子 → fetch_guba_posts()               │
│  └─ 雪球自选股帖子 → fetch_xueqiu_posts()             │
│  （增量合并：加载当天已有数据，按 post_id 去重追加）     │
├─────────────────────────────────────────────────────┤
│  Step 3: 情绪分析                                    │
│  └─ analyzer.analyze_post(post) → 每帖打分           │
│     返回 {sentiment, score[-1,1], confidence, ...}   │
├─────────────────────────────────────────────────────┤
│  Step 4: 指标计算（按平台独立）                        │
│  ├─ calculate_stock_metrics()  → 个股指标             │
│  ├─ calculate_market_overview() → 市场概览            │
│  └─ determine_cycle_stage()    → 周期状态             │
├─────────────────────────────────────────────────────┤
│  Step 5: 数据保存                                    │
│  ├─ save_daily_data()     → 聚合 JSON                │
│  ├─ save_raw_posts_json() → 原始帖子 JSON             │
│  ├─ save_raw_posts_csv()  → 原始帖子 CSV              │
│  └─ save_history_snapshot() → 历史快照                │
├─────────────────────────────────────────────────────┤
│  Step 6: 报告生成                                    │
│  ├─ 股吧报告 → guba_report_{date}.html               │
│  └─ 雪球报告 → xueqiu_report_{date}.html              │
└─────────────────────────────────────────────────────┘
```

### 3.2 命令行参数

| 参数               | 默认值   | 说明                              |
| ---------------- | ----- | ------------------------------- |
| `-n / --count`   | 20    | 监控股票数量                          |
| `--guba-pages`   | 5     | 股吧每只股票抓取页数                      |
| `--xueqiu-count` | 50    | 雪球每只股票抓取数量                      |
| `--skip-guba`    | False | 跳过股吧采集                          |
| `--skip-xueqiu`  | False | 跳过雪球采集                          |
| `--collect-only` | False | 仅采集数据不生成报告                      |
| `--report-only`  | False | 仅从已保存数据生成报告                     |
| `--date`         | 当天    | 指定日期（YYYYMMDD），配合 --report-only |

**典型用法**：

```bash
# 全量运行
python main.py

# 白天增量采集（不生成报告，可多次运行）
python main.py --collect-only

# 收盘后仅生成报告（用白天采集的数据）
python main.py --report-only

# 重跑某天的报告
python main.py --report-only --date 20260903
```

***

## 4. 数据采集模块

### 4.1 热门股票获取

#### 股吧人气榜（`hot_stocks.py → get_hot_stocks_from_gainers`）

- **数据源**：`https://guba.eastmoney.com/rank/stock?code=xxx`

- **方式**：Playwright 无头浏览器，访问股吧首页获取 Cookie 后翻页采集

- **每页**：20 只股票

- **过滤**：排除北交所代码（前缀 4/8/92）

- **提取字段**：代码、名称、股价、涨跌幅

#### 雪球热股榜（`xueqiu_crawler.py → get_hot_stocks_from_xueqiu`）

- **数据源**：`https://stock.xueqiu.com/v5/stock/hot_stock/list.json`

- **方式**：Playwright 注入 Cookie 后访问 API

- **提取字段**：代码、名称、股价、涨跌幅、雪球原生热度值

> 两个平台独立选股，股吧报告用股吧人气榜 TOP20，雪球报告用雪球热股榜 TOP20。

### 4.2 股吧帖子采集（`guba_crawler.py`）

#### 采集流程

1. 启动 Playwright 无头 Chromium（全局单例，首次调用时懒加载）
2. 注入反检测脚本（隐藏 webdriver 标识、伪造 plugins/languages）
3. 访问股吧首页获取 Cookie
4. 逐只股票访问 `https://guba.eastmoney.com/list,{code}_{page}.html`
5. 用 BeautifulSoup 解析帖子列表表格
6. **当日过滤**：只保留 `publish_time` 日期为今天的帖子
7. **智能停止**：某页无当日帖子时停止翻页（第1页除外）
8. 通过东方财富行情 API 获取实时股价和涨跌幅（curl\_cffi）
9. 访问人气排名页获取该股票的股吧人气排名

#### 系统用户过滤

发帖作者为以下类型的将被过滤：

| 规则   | 说明                                 |
| ---- | ---------------------------------- |
| 后缀匹配 | 作者名以「资讯」「新闻」「公告」「研报」「数据宝」「导读」结尾    |
| 精确匹配 | 13个已知媒体账号：东方财富网、同花顺、财联社、证券时报、界面新闻等 |

#### 返回字段

```python
{
    "source": "guba",
    "stock_code": "600519",           # 纯数字代码
    "stock_name": "贵州茅台",
    "title": "...",
    "url": "https://guba.eastmoney.com/news,xxx.html",
    "post_id": "xxx",
    "read_count": 1234,
    "comment_count": 56,
    "author": "张三",
    "publish_time": "2026-09-03 14:30:00",
    "content": "...",                  # 列表页无正文，用标题代替
    "stock_price": 1500.0,
    "change_percent": 1.02,
}
```

### 4.3 雪球帖子采集（`xueqiu_crawler.py`）

#### 采集流程

1. 启动 Playwright 无头 Chromium（全局单例）
2. 注入雪球 Cookie（`xq_a_token` + `u`）
3. 访问雪球首页获取额外 Cookie
4. 逐只股票访问 `https://xueqiu.com/S/{SH/SZ+代码}`
5. **API 响应拦截**：监听 `statuses/search` 和 `symbol/search` 的 JSON 响应
6. 从页面 DOM 提取关注量、股价、涨跌幅（DOM 选择器 + 脚本标签正则双重 fallback）
7. 标准化拦截到的 API 数据为统一格式
8. **媒体/公告过滤**：过滤新闻媒体账号和公司官方公告号
9. **当日过滤**：只保留当天帖子（publish\_time 未知帖保留）

#### 媒体/公告过滤规则

| 规则    | 说明                          |
| ----- | --------------------------- |
| 媒体账号  | 证券日报、新浪财经、华尔街见闻等已知媒体账号      |
| 公司公告号 | `公司名(SH/SZ+6位代码)` 格式的官方公告账号 |

#### 返回字段

```python
{
    "source": "xueqiu",
    "stock_code": "SH600519",         # 带交易所前缀
    "stock_name": "贵州茅台",
    "post_id": "xxx",
    "title": "...",
    "content": "...",
    "author": "张三",
    "author_followers": 1234,
    "publish_time": "2026-09-03 14:30:00",
    "read_count": 1234,
    "comment_count": 56,
    "like_count": 78,
    "retweet_count": 12,
    "url": "https://xueqiu.com/...",
    "stock_followers": 12345,          # 股票关注量
    "stock_price": 1500.0,
    "change_percent": 1.02,
}
```

### 4.4 反爬策略对比

| 策略     | 股吧                        | 雪球                     |
| ------ | ------------------------- | ---------------------- |
| 无头浏览器  | Playwright Chromium       | Playwright Chromium    |
| Cookie | 自动获取                      | 手动注入（xq\_a\_token + u） |
| 反检测    | webdriver 隐藏 + plugins 伪造 | 同左                     |
| 数据获取   | HTML 解析                   | API 响应拦截 + DOM 提取      |
| 请求间隔   | 1.5 秒                     | 2.5 秒                  |
| 智能停止   | 当页无当日帖子时停止                | API 返回数量有限             |
| 已知限制   | 采集页数过多可能触发频控              | 每只股票约 10 条（API 限制）     |

### 4.5 增量合并机制

一天内可多次运行采集（`--collect-only`），帖子自动去重：

1. 加载当天已有帖子 JSON（`guba_raw_posts_{date}.json`）
2. 按 `post_id` → `url` → `stock_code + title + publish_time` 三级去重
3. 追加新帖子后重新保存
4. 四路独立：`guba`、`xueqiu`、`guba_wl`（自选股）、`xueqiu_wl`（自选股）

***

## 5. 情绪分析引擎

### 5.1 概述

`analysis/sentiment.py` 中的 `SentimentAnalyzer` 类负责对每篇帖子进行情感打分，采用**自建词典 + 规则匹配**方式。

### 5.2 情感词典结构（`config/sentiment_dict.json`）

| 键                   | 类型                     | 说明         | 示例                    |
| ------------------- | ---------------------- | ---------- | --------------------- |
| `positive_words`    | List\[str]             | 正向词        | 大涨、涨停、加仓、利好           |
| `negative_words`    | List\[str]             | 负向词        | 大跌、跌停、割肉、利空           |
| `neutral_words`     | List\[str]             | 中性词        | 关注、观望、震荡              |
| `ambiguous_words`   | Dict\[str, List\[str]] | 歧义词→负面上下文词 | "抄底" → \["割","亏","套"] |
| `intensifier_words` | Dict\[str, float]      | 程度增强词→倍数   | "极其": 1.8, "特别": 1.5  |
| `negation_words`    | List\[str]             | 否定词        | 不、没有、绝非               |
| `degree_words`      | Dict\[str, float]      | 程度词→倍数     | "超级": 1.5, "稍微": 0.7  |
| `info_patterns`     | List\[str]             | 信息帖模式      | 资金流向、龙虎榜              |
| `ad_patterns`       | List\[str]             | 广告帖模式      | 加群、扫码、带单              |
| `question_patterns` | List\[str]             | 疑问句模式      | ？、吗、呢                 |

### 5.3 单帖打分流程

```
输入: 帖子文本（title + content）
  │
  ├─ 1. 分句: 按句号/感叹号/问号/换行拆分
  │
  ├─ 2. 逐句匹配情感词（长词优先）
  │     ├─ 正向词: 检查前缀修饰词（否定词/程度词）
  │     │    ├─ 有否定词 → 翻转为负向 (modifier = -0.8 × degree)
  │     │    └─ 有程度词 → 放大/缩小 (modifier = degree)
  │     ├─ 负向词: 同上，方向相反
  │     └─ 歧义词: 检查前后8字符是否有负面上下文词
  │          ├─ 有负面上下文 → 计为负向 (0.1 × count)
  │          └─ 无负面上下文 → 计为正向 (0.05 × count)
  │
  ├─ 3. 汇总分数
  │     pos_score = Σ 所有正向词得分
  │     neg_score = Σ 所有负向词得分
  │     total_score = pos_score - neg_score
  │     total_intensity = |pos_score| + |neg_score|
  │
  ├─ 4. 归一化
  │     normalized = total_score / (total_intensity + 0.5)
  │     裁剪到 [-1.0, 1.0]
  │
  ├─ 5. 特殊规则修正
  │     ├─ 价格预测 ("到XX元") → pos_score += 0.3, score ≥ 0.3
  │     ├─ 信息帖 → score × 0.1（强制接近中性）
  │     └─ 广告帖 → score = 0.0（强制中性）
  │
  ├─ 6. 分类
  │     score > 0.2  → "positive"
  │     score < -0.2 → "negative"
  │     其他         → "neutral"
  │
  └─ 7. 置信度
        confidence = min(1.0, total_intensity × 0.3 + word_count × 0.1)
        疑问帖 × 0.5, 信息帖/广告帖 × 0.3
```

### 5.4 修饰词检测逻辑

取目标词前 5 个字符作为 prefix，检查：

1. **标点隔断**：prefix 末尾 2 字符有逗号/句号等 → 无修饰
2. **否定词**：prefix 中存在否定词（不、没有、绝非） → `modifier = -0.8 × degree_modifier`
3. **程度词**：prefix 中存在程度词 → `modifier = degree_value`
4. **增强词**：prefix 中存在增强词 → `modifier *= intensifier_value`

最终：有否定 → 翻转方向；有程度/增强 → 放大/缩小。

***

## 6. 指标计算逻辑

### 6.1 个股情绪指数

#### 权重计算（互动量）

```python
interactions = read_count × 0.01 + comment_count × 2 + like_count × 1.5
weight = max(1.0, interactions)
```

> 雪球帖子有 `read_count`、`comment_count`、`like_count`；股吧帖子有 `read_count`、`comment_count`，无 `like_count`。

#### 加权平均模式（雪球，`weighted=True`）

```
weighted_score = Σ(score_i × weight_i) / Σ(weight_i)
```

**权重上限**：单条帖子权重不超过总权重的 10%，超限部分截断重新计算。

#### 简单平均模式（股吧，`weighted=False`）

```
avg_score = Σ(score_i) / N
```

> 股吧采用简单平均的原因：股吧中财富号帖子有平台流量倾斜，互动量异常高，加权会导致情绪被少数帖子扭曲。雪球用户更专业，互动量更能反映真实关注度。

#### 情绪指数映射

```
sentiment_index = (avg_score + 1) × 50
sentiment_index = clamp(0, 100)
```

将 `[-1, 1]` 线性映射到 `[0, 100]`：50 为中性，>50 偏多，<50 偏空。

### 6.2 热度评分

#### 股吧热度（对数缩放人气排名）

```
heat_score = max(0, (1 - log10(guba_rank) / log10(GUBA_RANK_MAX)) × 100)
```

- `GUBA_RANK_MAX = 5500`（基准总股票数）

- 第 1 名 → 100 分，第 5500 名 → 0 分

- 对数缩放：头部排名差距大，尾部差距小

> 排名从股吧人气排名页获取，如果获取失败则热度为 0。

#### 雪球热度（互动量 + 关注量）

```
interaction_score = log10(total_interactions) / log10(2000) × 50    # 满分50
follow_score = log10(follow_count) / log10(100000) × 50             # 满分50
heat_score = interaction_score + follow_score
```

- 互动量基准 2000，关注量基准 100000

- 两者各占 50%，对数缩放

- `total_interactions` = 所有帖子的互动量总和

> 移除了帖子数权重，因为雪球 API 每只股票返回约 10 条帖子，帖子数区分度不足。

### 6.3 分歧度

```
if positive_count + negative_count > 0:
    pos_ratio = positive_count / (positive_count + negative_count)
    divergence = 1 - |pos_ratio - 0.5| × 2
else:
    divergence = 0.5
```

- 范围 `[0, 1]`

- 0 = 完全一致（全部看多或全部看空）

- 0.5 = 最分歧（50/50 多空对半）

- 只统计 positive 和 negative 帖子，neutral 不参与

### 6.4 市场整体情绪

```
total_heat = Σ(heat_score_i)

if total_heat > 0:
    overall_sentiment = Σ(sentiment_index_i × heat_score_i) / total_heat
else:
    overall_sentiment = Σ(sentiment_index_i) / N

overall_heat = Σ(heat_score_i) / N
bullish_count = count(sentiment_index > 55)
bearish_count = count(sentiment_index ≤ 45)
neutral_count = total - bullish - bearish
```

> 按热度加权平均：越热门的股票对市场情绪影响越大。

### 6.5 平台差异对照

| 维度    | 股吧        | 雪球              |
| ----- | --------- | --------------- |
| 情绪计算  | 简单算术平均    | 互动量加权平均         |
| 热度来源  | 人气排名对数缩放  | 互动量50% + 关注量50% |
| 权重限制  | 无（每帖一票）   | 单帖 ≤ 总权重10%     |
| 用户画像  | 散户为主      | 偏专业投资者          |
| 帖子数特征 | 较多（几十到几百） | 较少（\~10条/股）     |

***

## 7. 多维周期状态模型

### 7.1 三维状态划分

#### 情绪维度（`_sentiment_level`）

| 范围   | 标签   |
| ---- | ---- |
| ≥ 80 | 极度乐观 |
| ≥ 55 | 偏多   |
| ≥ 45 | 中性   |
| ≥ 20 | 偏空   |
| < 20 | 极度悲观 |

#### 热度维度（`_heat_level`）

| 范围   | 标签 |
| ---- | -- |
| ≥ 75 | 过热 |
| ≥ 50 | 活跃 |
| ≥ 25 | 适中 |
| < 25 | 低迷 |

#### 分歧度维度（`_divergence_level`）

| 范围     | 标签  |
| ------ | --- |
| ≥ 0.65 | 分歧大 |
| ≥ 0.35 | 适中  |
| < 0.35 | 一致  |

### 7.2 趋势判定

```
recent_3 = 最近3条历史数据
avg_history = mean(history[key])
trend = (current[key] - avg_history) / avg_history

trend > 0.05  → 上升 (↑)
trend < -0.05 → 下降 (↓)
其他          → 持平 (—)
```

> 历史数据不足时 trend = 0.0（视为持平）。

### 7.3 十一种状态模式

按优先级从高到低匹配，命中一个即停止：

`_match_pattern` 返回 5 元组 `(pattern_name, emoji, color, css_class, signal)`，其中 `css_class` 用于前端样式分级（1=低位, 2=正常, 4=预警, 5=极端）。

| #  | 模式  | 判断条件                                             | 颜色         | CSS等级 | 信号                       |
| -- | --- | ------------------------------------------------ | ---------- | ----- | ------------------------ |
| 1  | 冰点期 | 情绪<20 AND 热度<25 AND 分歧<0.35                      | 紫(#722ed1) | 5     | 一致性悲观，关注度低迷，市场情绪降至冰点     |
| 2  | 过热期 | 情绪>80 AND 热度>75 AND 分歧<0.35                      | 红(#f5222d) | 5     | 一致性乐观，热度爆表，注意回调风险        |
| 3  | 加速期 | 情绪>55 AND 热度>50 AND 0.35≤分歧<0.65 AND 情绪↑ AND 热度↑ | 蓝(#1890ff) | 2     | 情绪偏多且上升，热度活跃且上升，多头氛围浓厚   |
| 4  | 退热期 | 情绪>55 AND 热度>50 AND 0.35≤分歧<0.65 AND 情绪↓ AND 热度↓ | 橙(#fa8c16) | 4     | 情绪仍偏多但下降，热度开始回落，炒作可能接近尾声 |
| 5  | 活跃期 | 情绪>55 AND 热度>50 AND 0.35≤分歧<0.65（非加速/退热）         | 蓝(#1890ff) | 2     | 情绪偏多，讨论活跃，多空仍在博弈         |
| 6  | 恐慌期 | 情绪<45 AND 热度>50 AND 分歧>0.5                       | 橙(#fa8c16) | 4     | 情绪偏空但讨论激烈，疑似恐慌蔓延         |
| 7  | 蓄势期 | 45≤情绪≤55 AND 热度<30 AND 分歧>0.6                    | 绿(#52c41a) | 1     | 情绪中性，热度低迷，多空分歧大，可能在蓄势    |
| 8  | 分歧期 | 分歧>0.65                                          | 橙(#fa8c16) | 4     | 多空分歧极大，方向不明，需等待信号确认      |
| 9  | 存量期 | 情绪>55 AND 热度<30                                  | 紫(#722ed1) | 5     | 情绪偏多但热度低迷，可能是存量博弈或底部缓慢吸筹 |
| 10 | 低迷期 | 情绪<45 AND 热度<25                                  | 紫(#722ed1) | 5     | 情绪偏空，热度低迷，市场关注度低         |
| 11 | 震荡期 | 默认兜底                                             | 灰(#8c8c8c) | 2     | 动态拼接当前三维状态               |

> **注意**：`_match_pattern` 必须返回完整的 5 元组。此前"过热期"分支遗漏了 `css_class` 参数（只返回 4 个值），导致解包报 `ValueError: not enough values to unpack`，已于 2026-09-03 修复，补全 `css_class=5`。

### 7.4 置信度计算

```
sent_extremity = |sentiment - 50| / 50          # 情绪极端度
heat_extremity = heat / 100                      # 热度极端度
trend_strength = (|sent_trend| + |heat_trend|) / 2  # 趋势强度
div_factor = 1 - divergence                      # 一致性因子

confidence = sent_extremity × 0.3
           + heat_extremity × 0.3
           + trend_strength × 0.2
           + div_factor × 0.2

confidence = clamp(0.1, 0.95)
```

> 情绪越极端、热度越高、趋势越明显、分歧越小 → 置信度越高。

***

## 8. 数据存储模块

### 8.1 文件命名规则

所有数据文件存于 `data/raw/`：

| 文件名模式                             | 用途            | 写入时机   |
| --------------------------------- | ------------- | ------ |
| `guba_data_{date}.json`           | 股吧每日聚合数据      | Step 5 |
| `xueqiu_data_{date}.json`         | 雪球每日聚合数据      | Step 5 |
| `guba_posts_{date}.csv`           | 股吧原始帖子 CSV 导出 | Step 5 |
| `xueqiu_posts_{date}.csv`         | 雪球原始帖子 CSV 导出 | Step 5 |
| `guba_raw_posts_{date}.json`      | 股吧原始帖子（增量合并用） | Step 2 |
| `xueqiu_raw_posts_{date}.json`    | 雪球原始帖子（增量合并用） | Step 2 |
| `guba_wl_raw_posts_{date}.json`   | 股吧自选股原始帖子     | Step 2 |
| `xueqiu_wl_raw_posts_{date}.json` | 雪球自选股原始帖子     | Step 2 |
| `history.json`                    | 市场级历史快照（90天）  | Step 5 |
| `watchlist_history.json`          | 自选股历史快照（90天）  | Step 5 |

报告文件存于 `data/reports/`：

| 文件名模式                       | 用途     |
| --------------------------- | ------ |
| `guba_report_{date}.html`   | 股吧情绪日报 |
| `xueqiu_report_{date}.html` | 雪球情绪日报 |

### 8.2 聚合数据 JSON 结构

```json
{
  "_meta": {
    "generated_at": "2026-09-03 15:00:00",
    "date": "20260903",
    "version": "1.0"
  },
  "overview": {
    "overall_sentiment": 53.2,
    "overall_heat": 72.6,
    "bullish_count": 14,
    "bearish_count": 6,
    "neutral_count": 0,
    "bullish_ratio": 0.7
  },
  "stock_metrics": [
    {
      "stock_code": "600519",
      "stock_name": "贵州茅台",
      "sentiment_index": 55.3,
      "heat_score": 85.2,
      "divergence": 0.3,
      "positive_count": 8,
      "negative_count": 2,
      "neutral_count": 5,
      "total_posts": 15,
      "change_percent": 1.02,
      "price": 1500.0,
      "cycle_stage": {
        "stage": 2,
        "stage_name": "活跃期",
        "stage_emoji": "📊",
        "signal": "..."
      }
    }
  ],
  "market_cycle": {
    "stage_name": "活跃期",
    "stage_emoji": "📊",
    "confidence": 0.65,
    "description": "...",
    "signal": "...",
    "score_details": {
      "sentiment_position": "偏多",
      "heat_position": "活跃",
      "divergence": 0.3
    }
  },
  "watchlist_metrics": [
    {
      "stock_code": "600031",
      "stock_name": "三一重工",
      "sentiment_index": 62.5,
      "heat_score": 40.0,
      "divergence": 0.2,
      "total_posts": 3,
      "stock_price": 20.10,
      "change_percent": 2.39,
      "cycle_stage": {...}
    }
  ]
}
```

### 8.3 CSV 结构

```
stock_code, stock_name, source, title, content, author, publish_time,
read_count, comment_count, like_count, stock_followers,
sentiment, score, confidence, url
```

***

## 9. 报告生成模块

### 9.1 生成流程（`report_generator.py`）

1. **加载模板**：Jinja2 读取 `report/templates/report.html`
2. **数据预处理**：

   - 按情绪指数排序 → 热门个股情绪榜

   - 按热度排序 → 热度排行

   - 情绪极值预警（>80 高 / <20 低）

   - 情绪排行 TOP15 名称/数值
3. **加载历史数据**：`load_history(source, days=30)` 获取市场级趋势
4. **加载自选股历史**：对每只自选股 `load_watchlist_history(code, days=30, source)`
5. **渲染 HTML**：传入 15 个变量给 Jinja2 模板
6. **输出文件**：`data/reports/{source}_report_{date}.html`

### 9.2 报告内容模块

| 模块      | 说明                             |
| ------- | ------------------------------ |
| 报告头部    | 日期、平台、生成时间                     |
| 市场概览卡片  | 情绪指数、讨论热度、看多/看空/中性数、置信度        |
| 周期状态卡片  | 当前阶段名称、emoji、信号描述、三维状态明细       |
| 趋势图     | ECharts 三线图：情绪指数/热度指数/分歧度 × 日期 |
| 热门个股情绪榜 | 全部 N 只股票，按情绪排序，含多/空/中帖子数、阶段标签  |
| 自选股情绪分析 | 自选股列表 + 股价/涨跌幅/情绪/热度/多空分布/趋势图  |

### 9.3 双平台报告差异

| 模块   | 股吧报告                        | 雪球报告                              |
| ---- | --------------------------- | --------------------------------- |
| 标题   | "股吧情绪日报"                    | "雪球情绪日报"                          |
| 选股来源 | 股吧人气榜 TOP20                 | 雪球热股榜 TOP20                       |
| 情绪计算 | 简单平均                        | 互动量加权                             |
| 热度公式 | 人气排名对数                      | 互动量+关注量                           |
| 帖子字段 | read\_count, comment\_count | 额外有 like\_count, stock\_followers |

***

## 10. 自选股模块

### 10.1 配置

编辑 `config/watchlist.json`：

```json
[
  {"code": "600298", "name": "安琪酵母", "symbol": "600298"},
  {"code": "600031", "name": "三一重工", "symbol": "600031"}
]
```

- `code`：纯数字代码

- `symbol`：股吧用的 symbol（与 code 相同）

- 雪球使用时自动加交易所前缀（6开头→SH，0/3开头→SZ）

### 10.2 处理逻辑

1. 从 `watchlist.json` 加载自选股列表
2. 分股吧/雪球两个独立阶段采集
3. 每只自选股独立计算指标（股吧简单平均，雪球加权+关注量）
4. 历史数据按 `{stock_code}_{source}` 独立存储
5. 报告中独立展示，含股价、涨跌幅、情绪、热度、多空分布、周期阶段
6. 无帖子的自选股也会显示（情绪默认50，热度默认0，阶段为震荡期）

### 10.3 平台隔离

自选股在股吧和雪球日报中**独立展示**：

- 数据来源隔离

- 情绪算法隔离（股吧简单平均 vs 雪球加权）

- 热度算法隔离（排名 vs 互动量+关注量）

- 历史存储隔离（`watchlist_history.json` 按 source 分 key）

***

## 11. 历史趋势模块

### 11.1 市场级历史（`history.json`）

```json
{
  "guba": [
    {"date": "20260901", "sentiment": 52.1, "heat": 70.0, "divergence": 0.3, "stage_name": "活跃期"},
    {"date": "20260902", "sentiment": 53.5, "heat": 71.2, "divergence": 0.28, "stage_name": "活跃期"}
  ],
  "xueqiu": [...]
}
```

- 每次运行 `save_history_snapshot` 追加一条

- 每平台最多保留 90 条（90天滚动窗口）

- 报告中取最近 30 条渲染三线趋势图

### 11.2 自选股历史（`watchlist_history.json`）

```json
{
  "600031_guba": [
    {"date": "20260901", "sentiment": 60.0, "heat": 40.0, "divergence": 0.2, "stage_name": "活跃期", "post_count": 3, "source": "guba"}
  ],
  "600031_xueqiu": [...]
}
```

- 按 `{stock_code}_{source}` 分 key

- 每只每平台最多 90 条

- 报告中点击自选股展开时渲染 ECharts 趋势图

### 11.3 趋势计算

```
recent_3 = 最近3条历史
avg = mean(recent_3[key])
trend = (current[key] - avg) / avg

trend > 0.05  → ↑
trend < -0.05 → ↓
其他          → —
```

> 历史数据不足 3 条时 trend = 0.0。当前只有 1-2 天数据时趋势图只有 1-2 个点，会随每日运行逐步完善。

***

## 12. 配置参数速查

### 12.1 路径配置（`config/settings.py`）

| 参数             | 默认值                | 说明     |
| -------------- | ------------------ | ------ |
| `BASE_DIR`     | 项目根目录              | 自动推导   |
| `DATA_DIR`     | `BASE_DIR/data`    | 数据目录   |
| `RAW_DATA_DIR` | `DATA_DIR/raw`     | 原始数据目录 |
| `REPORT_DIR`   | `DATA_DIR/reports` | 报告输出目录 |

### 12.2 采集配置

| 参数                      | 默认值              | 说明                          |
| ----------------------- | ---------------- | --------------------------- |
| `HOT_STOCKS_COUNT`      | 20               | 热门股票数量                      |
| `HOT_STOCKS_MARKET`     | `"hs_a"`         | 市场类型（沪深A股）                  |
| `EXCLUDE_CODE_PREFIXES` | `["4","8","92"]` | 排除北交所                       |
| `GUBA_PAGE_COUNT`       | 5                | 股吧每只股票抓取页数                  |
| `GUBA_REQUEST_DELAY`    | 1.5 秒            | 股吧请求间隔                      |
| `GUBA_RANK_MAX`         | 5500             | 股吧人气排名总数基准                  |
| `XUEQIU_COOKIE`         | —                | 雪球 Cookie（xq\_a\_token + u） |
| `XUEQIU_POST_COUNT`     | 50               | 雪球每只股票抓取数量                  |
| `XUEQIU_REQUEST_DELAY`  | 2.5 秒            | 雪球请求间隔                      |

### 12.3 其他配置

| 参数                    | 默认值                          | 说明                   |
| --------------------- | ---------------------------- | -------------------- |
| `SENTIMENT_DICT_PATH` | `config/sentiment_dict.json` | 情感词典路径               |
| `REPORT_TEMPLATE`     | `"report.html"`              | 报告模板文件名              |
| `THEMES`              | 14个题材映射                      | 题材关键词（当前未使用，主题分析已移除） |

***

## 13. 阈值与常量速查

### 13.1 情绪分析阈值

| 阈值        | 值            | 位置           | 说明           |
| --------- | ------------ | ------------ | ------------ |
| 正向分类      | score > 0.2  | sentiment.py | 判定为 positive |
| 负向分类      | score < -0.2 | sentiment.py | 判定为 negative |
| 信息帖系数     | × 0.1        | sentiment.py | 压制接近中性       |
| 广告帖系数     | = 0.0        | sentiment.py | 强制中性         |
| 价格预测加成    | + 0.3        | sentiment.py | "到XX元"模式     |
| 疑问帖置信度    | × 0.5        | sentiment.py | 降低置信度        |
| 信息/广告帖置信度 | × 0.3        | sentiment.py | 降低置信度        |

### 13.2 指标计算权重

| 权重             | 值       | 位置          | 说明     |
| -------------- | ------- | ----------- | ------ |
| read\_count    | × 0.01  | metrics.py  | 互动量权重  |
| comment\_count | × 2.0   | metrics.py  | 互动量权重  |
| like\_count    | × 1.5   | metrics.py  | 互动量权重  |
| 单帖权重上限         | 总权重 10% | metrics.py  | 防止单帖主导 |
| 雪球互动量基准        | 2000    | metrics.py  | 热度满分基准 |
| 雪球关注量基准        | 100000  | metrics.py  | 热度满分基准 |
| 股吧排名基准         | 5500    | settings.py | 热度对数基准 |

### 13.3 周期模型阈值

| 阈值     | 值       | 说明                  |
| ------ | ------- | ------------------- |
| 情绪极度乐观 | ≥ 80    | \_sentiment\_level  |
| 情绪偏多   | ≥ 55    | \_sentiment\_level  |
| 情绪中性   | ≥ 45    | \_sentiment\_level  |
| 情绪偏空   | ≥ 20    | \_sentiment\_level  |
| 情绪极度悲观 | < 20    | \_sentiment\_level  |
| 热度过热   | ≥ 75    | \_heat\_level       |
| 热度活跃   | ≥ 50    | \_heat\_level       |
| 热度适中   | ≥ 25    | \_heat\_level       |
| 热度低迷   | < 25    | \_heat\_level       |
| 分歧大    | ≥ 0.65  | \_divergence\_level |
| 分歧适中   | ≥ 0.35  | \_divergence\_level |
| 分歧一致   | < 0.35  | \_divergence\_level |
| 趋势上升   | > 0.05  | \_trend\_arrow      |
| 趋势下降   | < -0.05 | \_trend\_arrow      |
| 置信度上限  | 0.95    | \_calc\_confidence  |
| 置信度下限  | 0.1     | \_calc\_confidence  |

### 13.4 置信度权重

| 因子    | 权重  | 说明             |                |      |             |       |
| ----- | --- | -------------- | -------------- | ---- | ----------- | ----- |
| 情绪极端度 | 0.3 | <br />         | sentiment - 50 | / 50 |             |       |
| 热度极端度 | 0.3 | heat / 100     |                |      |             |       |
| 趋势强度  | 0.2 | (              | sent\_trend    | +    | heat\_trend | ) / 2 |
| 一致性因子 | 0.2 | 1 - divergence |                |      |             |       |

### 13.5 历史数据保留

| 数据      | 保留天数  | 文件                                        |
| ------- | ----- | ----------------------------------------- |
| 市场级历史   | 90 条  | history.json                              |
| 自选股历史   | 90 条  | watchlist\_history.json                   |
| 聚合数据    | 不自动清理 | guba\_data\_*.json / xueqiu\_data\_*.json |
| 原始帖子    | 不自动清理 | _raw\_posts_.json                         |
| CSV 导出  | 不自动清理 | _posts_.csv                               |
| HTML 报告 | 不自动清理 | _report_.html                             |

***

> **文档结束**。如有疑问，可对照 `README.md`（快速上手）和源代码查看实现细节。

