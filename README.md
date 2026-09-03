# 散户情绪分析系统

基于社交媒体发帖数据，分析热门题材和热门股票的散户情绪，辅助判断股票炒作周期位置。

## 功能特点

- 📈 **自动获取热门股票**：从东方财富股吧人气榜和雪球热股榜自动抓取当日热门股票

- 🕸️ **多平台数据采集**：支持东方财富股吧、雪球两大平台，独立采集独立生成报告

- 💭 **情绪分析**：基于金融领域情感词典的规则法情绪分析

- 📐 **多维度指标**：情绪指数、讨论热度、分歧度、变化率

- 🔥 **热度计算**：

  - 股吧：人气排名对数缩放

  - 雪球：互动量 50% + 关注量 50%（对数缩放，保留区分度）

- 🚫 **媒体/公告过滤**：雪球自动剔除证券日报、新浪财经等媒体账号及上市公司官方公告号

- 📅 **当日帖子过滤**：股吧和雪球均只保留当日发布的帖子

- 📌 **自选股分析**：支持自定义自选股列表，独立分析每只股票的情绪与趋势

- 🔄 **多维周期定位**：基于情绪×热度×分歧度三维度，匹配 10+ 种市场状态模式

- 📄 **HTML 日报**：双平台独立报告，精美可视化，带 ECharts 图表

- 📊 **历史数据**：自动保存每日数据，支持趋势分析

- ➕ **增量采集**：支持一天内多次运行，帖子自动去重合并

## 项目结构

```
Retail_sentiment/
├── main.py                  # 主入口
├── test_demo.py             # 模拟数据测试
├── requirements.txt         # 依赖列表
├── XUEQIU_SETUP.md          # 雪球配置说明
├── config/
│   ├── settings.py          # 全局配置
│   ├── watchlist.json       # 自选股配置
│   └── sentiment_dict.json  # 情感词典
├── collectors/
│   ├── hot_stocks.py        # 热门股票列表获取
│   ├── guba_crawler.py      # 东方财富股吧爬虫
│   └── xueqiu_crawler.py    # 雪球爬虫
├── analysis/
│   ├── sentiment.py         # 情绪分析引擎
│   ├── metrics.py           # 情绪指标计算
│   └── cycle_model.py       # 炒作周期模型
├── storage/
│   └── data_store.py        # 数据存储
├── report/
│   ├── report_generator.py  # 报告生成器
│   └── templates/
│       └── report.html      # HTML 报告模板
├── browsers/                # Playwright 浏览器（自动安装，平台相关）
└── data/
    ├── raw/                 # 原始数据（JSON/CSV）
    └── reports/             # 生成的报告（HTML）
```

> `browsers/` 是 Playwright 安装的 Chromium 浏览器，用于无头爬取。与操作系统平台绑定，跨平台迁移时需删除并在新机器上重新执行 `playwright install chromium`。

## 快速开始

### 环境要求

- Python 3.9+

- 跨平台支持：Windows / macOS / Linux

### 安装依赖

#### Windows（PowerShell）

```powershell
# 1. 创建虚拟环境
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. 安装依赖
pip install -r requirements.txt

# 3. 安装 Playwright 浏览器
playwright install chromium
```

#### macOS / Linux

```bash
# 1. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 安装 Playwright 浏览器
playwright install chromium
```

> **注意**：不要直接拷贝 Windows 上的 `venv/` 目录到 macOS/Linux，需在新系统上重新创建虚拟环境。Playwright 的浏览器也是平台相关的，会自动下载对应平台的版本。

### 配置雪球 Cookie

详见 [XUEQIU\_SETUP.md](XUEQIU_SETUP.md)

> Cookie 绑定浏览器会话，换机器后需要重新获取。

### 配置自选股

编辑 `config/watchlist.json`，添加或修改自选股：

```json
[
  {"code": "600031", "name": "三一重工", "symbol": "600031"},
  {"code": "601899", "name": "紫金矿业", "symbol": "601899"}
]
```

### 运行

```bash
# 完整运行（股吧 + 雪球，生成双份报告）
python main.py

# 指定热门股票数量
python main.py -n 20

# 跳过股吧（只用雪球）
python main.py --skip-guba

# 跳过雪球（只用股吧）
python main.py --skip-xueqiu

# 自定义股吧页数和雪球帖子数
python main.py --guba-pages 3 --xueqiu-count 30

# 仅采集数据（不生成报告，用于增量采集）
python main.py --collect-only

# 仅生成报告（从已保存数据生成，不重新抓取）
python main.py --report-only

# 指定日期生成报告
python main.py --report-only --date 20260903
```

### 测试（模拟数据）

```bash
python test_demo.py
```

## 配置说明

主要配置在 `config/settings.py` 中：

| 配置项                    | 默认值 | 说明                 |
| ---------------------- | --- | ------------------ |
| HOT\_STOCKS\_COUNT     | 20  | 监控的热门股票数量          |
| GUBA\_PAGE\_COUNT      | 5   | 每只股票抓取股吧页数         |
| GUBA\_REQUEST\_DELAY   | 1.5 | 股吧请求间隔（秒）          |
| XUEQIU\_POST\_COUNT    | 10  | 每只股票抓取雪球帖子数（API限制） |
| XUEQIU\_REQUEST\_DELAY | 2.5 | 雪球请求间隔（秒）          |

## 报告说明

系统生成两份独立报告：

| 报告     | 文件名                           | 说明       |
| ------ | ----------------------------- | -------- |
| 股吧情绪日报 | `guba_report_YYYYMMDD.html`   | 东方财富股吧数据 |
| 雪球情绪日报 | `xueqiu_report_YYYYMMDD.html` | 雪球社区数据   |

每份报告包含：

- 市场情绪温度、讨论热度、多空比例等核心指标

- 多维周期状态定位（情绪×热度×分歧度三维模型）

- 个股情绪排行 TOP15

- 情绪分布

- 热门个股情绪榜（完整榜单，含涨跌幅、情绪、热度、分歧度、多空比、周期阶段）

- 自选股情绪分析（可展开查看历史趋势图）

- 情绪极值预警

- 市场指标历史趋势图

## 多维周期状态模型

系统基于 **情绪位置 × 热度位置 × 分歧度** 三个维度，通过模式匹配判定当前市场状态，而非简单的线性五阶段模型。

| 状态     | 特征                                | 典型场景        |
| ------ | --------------------------------- | ----------- |
| 🔥 过热期 | 情绪极度乐观(>80)，热度过热(>75)，一致性高(<0.35) | 炒作高潮，一致性看多  |
| 🚀 加速期 | 情绪偏多，热度活跃，分歧适中，情绪和热度均上行           | 多头趋势加速      |
| 📊 活跃期 | 情绪偏多，热度活跃，多空博弈                    | 热点轮动，板块活跃   |
| 📉 退热期 | 情绪仍偏多但下降，热度回落                     | 炒作接近尾声      |
| ⚔️ 分歧期 | 多空分歧极大(>0.65)，方向不明                | 震荡整理，等待方向选择 |
| 😨 恐慌期 | 情绪偏空(<45)，热度高(>50)，分歧大            | 下跌过程中恐慌蔓延   |
| 🌱 蓄势期 | 情绪中性(45-55)，热度低(<30)，分歧大          | 横盘蓄势，等待突破   |
| 🧊 存量期 | 情绪偏多(>55)但热度低迷(<30)               | 存量博弈，底部吸筹   |
| 🥶 低迷期 | 情绪偏空(<45)，热度低迷(<25)               | 关注度低，交投清淡   |
| ❄️ 冰点期 | 情绪极度悲观(<20)，热度低迷(<25)，一致性高        | 一致性悲观，情绪见底  |
| 📊 震荡期 | 其他中性组合                            | 多空平衡，区间震荡   |

## 免责声明

本工具仅为散户情绪数据分析，不构成任何投资建议。股市有风险，投资需谨慎。
