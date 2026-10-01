# 散户情绪分析系统

基于社交媒体发帖数据，分析热门题材和热门股票的散户情绪，辅助判断股票炒作周期位置。

## 功能特点

- 📈 **自动获取热门股票**：从东方财富股吧人气榜和雪球热股榜自动抓取当日热门股票

- 🕸️ **多平台数据采集**：支持东方财富股吧、雪球两大平台，独立采集独立生成报告

- 💭 **情绪分析**：热门股使用词典法规则分析，自选股使用 Deepseek 大模型分析（更准确判断复杂语义）

- 📐 **多维度指标**：情绪指数、讨论热度、分歧度、变化率

- 🔥 **热度计算**：

  - 股吧：个股热度 = 人气排名对数缩放；整体热度 = 东方财富APP UV指数归一化（数据来源 apppc.com，存在较长时间滞后，滞后期用最近一周均值顶替并标记，真实值公布后自动回填历史）

  - 雪球：互动量 70% + 关注量 30%（对数缩放，互动量基准5000，关注量基准500000）

- 🚫 **媒体/公告过滤**：雪球自动剔除证券日报、新浪财经等媒体账号及上市公司官方公告号

- 📅 **当日帖子过滤**：股吧和雪球均只保留当日发布的帖子

- 🚦 **雪球翻页与限流控制**：雪球按 IP 限流（实测约 4 次请求 / 30 秒），采集中所有请求统一排队节流，并按时间倒序翻页直到当日帖子采完；每只股票记录采集状态，报告里可区分「当日真没人讨论」与「被限流没抓到」

- 📌 **自选股分析**：支持自定义自选股列表，独立分析每只股票的情绪与趋势

- 🔄 **多维周期定位**：基于情绪×热度×分歧度三维度，匹配 10+ 种市场状态模式

- 📄 **HTML 日报**：股吧/雪球市场报告 + 自选股报告，精美可视化，带 ECharts 图表

- 🤖 **LLM 情绪分析**：自选股的操作建议和关键发现由 Deepseek 大模型生成，规则引擎兜底

- 📊 **历史数据**：自动保存每日数据，支持趋势分析

- ➕ **增量采集**：支持一天内多次运行，帖子自动去重合并

- ⏰ **定时自动运行**：Windows 计划任务 + 交易日历判断，自动跳过周末和节假日，每天 3 次自动采集和生成报告

## 项目结构

```
Retail_sentiment/
├── main.py                  # 主入口
├── reanalyze_wl.py          # 重新用LLM分析旧自选股帖子（修复旧词典法数据）
├── migrate_split_watchlist.py # 一次性迁移：把自选股数据从混合文件拆出（幂等）
├── run_daily.ps1            # 定时运行脚本（含交易日历判断）
├── manage_schedule.ps1      # 计划任务管理（创建/删除/查看/测试）
├── test_demo.py             # 模拟数据测试
├── requirements.txt         # 依赖列表
├── XUEQIU_SETUP.md          # 雪球配置说明
├── config/
│   ├── settings.py          # 全局配置（gitignore，含API Key等敏感信息）
│   ├── settings_sample.py   # 配置示例文件（复制为settings.py后填写）
│   ├── watchlist.json       # 自选股配置（gitignore）
│   ├── watchlist_sample.json # 自选股示例文件（复制为watchlist.json后修改）
│   ├── holidays.json        # A股休市日配置（节假日+调休补班日）
│   └── sentiment_dict.json  # 情感词典
├── collectors/
│   ├── hot_stocks.py        # 热门股票列表获取
│   ├── guba_crawler.py      # 东方财富股吧爬虫
│   ├── xueqiu_crawler.py    # 雪球爬虫
│   ├── market_heat.py       # 东方财富APP UV指数采集
│   └── price_fetcher.py     # 腾讯财经API股价获取（补充缺失股价）
├── analysis/
│   ├── sentiment.py         # 情绪分析引擎（词典法，热门股用）
│   ├── llm_sentiment.py     # Deepseek 大模型情绪分析（自选股用）
│   ├── metrics.py           # 情绪指标计算
│   └── cycle_model.py       # 炒作周期模型
├── storage/
│   └── data_store.py        # 数据存储
├── report/
│   ├── report_generator.py  # 报告生成器（市场报告 + 自选股报告）
│   └── templates/
│       ├── report.html      # 市场整体报告模板（股吧/雪球共用）
│       ├── watchlist_report.html   # 自选股报告模板（雪球段 + 股吧段）
│       ├── echarts.min.js   # ECharts 5.4.3（随报告分发，不走 CDN）
│       └── logo_hunshifan_v4.jpg   # 报告 Logo
├── browsers/                # Playwright 浏览器（自动安装，平台相关）
├── logs/                    # 定时任务日志（schedule_YYYYMMDD.log）
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

### 配置 Deepseek API Key

`config/settings.py` 和 `config/watchlist.json` 已加入 `.gitignore`（含敏感信息）。项目提供示例文件：

```bash
# 复制示例文件为正式配置
cp config/settings_sample.py config/settings.py
cp config/watchlist_sample.json config/watchlist.json
```

在 `config/settings.py` 中设置 Deepseek API Key（用于自选股情绪分析和自选股报告生成）：

```python
DEEPSEEK_API_KEY = "sk-your-api-key-here"
```

> 未配置时，自选股情绪分析回退到词典法，自选股报告的操作建议和关键发现回退到规则引擎。

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
# 完整运行（市场 + 自选股，生成三份报告）
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

# 只跑自选股链路（不采热股榜、不采大盘热度）
python main.py --watchlist-only

# 只跑市场链路（不采自选股）
python main.py --market-only

# 仅生成报告（从已保存数据生成，不重新抓取）
python main.py --report-only

# 指定日期生成报告
python main.py --report-only --date 20260903

# 只生成自选股报告
python main.py --report-only --date 20260903 --watchlist-only
```

### 按日期补采自选股

热股榜、股吧人气排名、大盘 UV 指数都是实时接口，**错过了就补不回来**；只有自选股能按日期回溯。
因此 `--date` 只对自选股链路生效：

```bash
# 补采 2026-09-30 的自选股（自动只跑自选股链路）
python main.py --date 20260930 --watchlist-only

# 用补采到的数据出报告
python main.py --report-only --date 20260930 --watchlist-only
```

补采时的已知限制：

- **股吧热度不可用**。热度来自实时人气排名页，历史日期拿不到。报告里渲染成「—」而不是 0.0。
- **行情不是当日收盘价**，是抓取那一刻的价格。若补采日之后一直休市，才恰好等于收盘价。
- 目标日距今超过 7 天会有告警（股吧列表翻页有上限，太久远的日期可能翻不到），必要时加大 `--guba-pages`。

带 `--date` 却不加 `--watchlist-only` 时，程序会提示「市场数据不可回溯，按自选股补采处理」并只跑自选股；
显式写 `--date ... --market-only` 会直接报错退出。

### 测试（模拟数据）

```bash
python test_demo.py
```

## 定时自动运行

系统支持通过 Windows 计划任务实现全自动运行，包含交易日历判断（自动跳过周末和节假日）。

### 快速设置

```powershell
# 1. 创建全部定时任务（需管理员权限）
.\manage_schedule.ps1 -Action create

# 2. 查看任务状态
.\manage_schedule.ps1 -Action status

# 3. 立即测试运行一次
.\manage_schedule.ps1 -Action test

# 4. 删除全部定时任务
.\manage_schedule.ps1 -Action delete
```

### 每日执行计划

| 时间 | 任务 | 模式 | 说明 |
|------|------|------|------|
| 12:00 | RetailSentiment_CollectMidday | collect | 午盘采集帖子（增量合并） |
| 15:30 | RetailSentiment_CollectClose | collect | 收盘采集帖子（增量合并） |
| 16:00 | RetailSentiment_Report | report | 生成三份日报（股吧市场/雪球市场/自选股） |

### 交易日历

`config/holidays.json` 存储 A 股休市日配置：

- `holidays`：法定节假日（周末休市日 + 工作日节假日）
- `workdays`：调休补班日（周末但开市）

> 每年根据国务院发布的放假通知更新此文件。脚本会自动跳过非交易日，日志记录到 `logs/schedule_YYYYMMDD.log`。

### 手动运行定时脚本

```powershell
# 手动执行采集
.\run_daily.ps1 -Mode collect

# 手动执行报告生成
.\run_daily.ps1 -Mode report

# 手动全量运行
.\run_daily.ps1 -Mode full
```

## 配置说明

主要配置在 `config/settings.py` 中：

| 配置项                    | 默认值 | 说明                 |
| ---------------------- | --- | ------------------ |
| HOT\_STOCKS\_COUNT     | 20  | 监控的热门股票数量          |
| GUBA\_PAGE\_COUNT      | 5   | 每只股票抓取股吧页数         |
| GUBA\_REQUEST\_DELAY   | 1.5 | 股吧请求间隔（秒）          |
| XUEQIU\_POST\_COUNT    | 30  | 每只股票目标雪球帖子数上限（翻页达到即提前停止） |
| XUEQIU\_MAX\_PAGES     | 2   | 自选股单只翻页上限           |
| XUEQIU\_HOT\_MAX\_PAGES | 1  | 热股单只翻页上限            |
| XUEQIU\_BACKFILL\_MAX\_PAGES | 6  | 按日期补采时的翻页上限（翻过目标日会提前停止） |
| MAX\_BACKFILL\_DAYS    | 7   | 补采目标日距今超过此天数时告警 |
| XUEQIU\_MIN\_REQUEST\_INTERVAL | 8.0 | 雪球最小请求间隔（秒），受限于其 IP 限流 |
| MARKET\_HEAT\_UV\_LOW  | 5000.0 | UV指数下限（=0分）       |
| MARKET\_HEAT\_UV\_HIGH | 9000.0 | UV指数上限（=100分）     |
| DEEPSEEK\_API\_KEY | — | Deepseek API Key（自选股情绪分析用） |
| DEEPSEEK\_MODEL | `deepseek-chat` | Deepseek 模型名 |
| LLM\_BATCH\_SIZE | 20 | LLM 每批帖子数 |
| LLM\_CONCURRENCY | 3 | LLM 并发批次数 |

## 报告说明

系统生成三份报告，标题带「混市FAN」品牌 Logo：

| 报告 | 文件名 | 说明 |
| --- | --- | --- |
| 混市FAN股吧情绪报告 | `guba_report_YYYYMMDD.html` | 全市场口径，东方财富股吧数据 |
| 混市FAN雪球情绪报告 | `xueqiu_report_YYYYMMDD.html` | 全市场口径，雪球社区数据 |
| 混市FAN自选股情绪报告 | `watchlist_report_YYYYMMDD.html` | 自选股口径，雪球段 + 股吧段 |

市场报告与自选股报告的数据完全分开：前者读 `{source}_data_YYYYMMDD.json`，后者读 `{source}_wl_data_YYYYMMDD.json`。
两者的采集也各自独立，可以分开跑（见「按日期补采自选股」）。

> 报告依赖两个同目录文件：`logo_hunshifan_v4.jpg`（标题栏 Logo）和 `echarts.min.js`（图表库）。
> 生成报告时会自动从 `report/templates/` 复制过去，无需手动放置。图表库不走 CDN，报告可完全离线打开；
> 拷贝报告时请连同这两个文件一起拷贝，否则图表区域会显示「图表库加载失败」提示。

### 市场情绪报告（股吧 / 雪球）

每份报告包含：

- 市场情绪温度、讨论热度、多空比例等核心指标

- 多维周期状态定位（情绪×热度×分歧度三维模型）

- 个股情绪排行 TOP15

- 情绪分布

- 热门个股情绪榜（完整榜单，含股价、涨跌幅、情绪、热度、分歧度、多空比、周期阶段）

- 情绪极值预警

- 市场指标历史趋势图

### 自选股情绪报告

一份报告里分两段，段内各自列出全部自选股：

- **雪球自选股情绪**：股价、涨跌幅、情绪指数、热度、分歧度、多空帖数、周期阶段，点击个股展开 30 天趋势图

- **股吧自选股情绪**：同上，热度来自股吧人气排名

- **明日操作建议**：每只自选股一个方向标签（偏多/偏空/谨慎/中性/回避）+ 具体建议 + 理由

- **关键发现**：2-4 张分析卡片，识别平台分歧、共振看多/看空、热度异常等信号

操作建议与关键发现由 Deepseek 大模型生成，失败时回退到规则引擎。

补采历史日期时，股吧热度列显示「—」并给出提示条——人气排名是实时接口，历史日期拿不到，
那不是「热度低」而是「没有这个数」。

## 多维周期状态模型

系统基于 **情绪位置 × 热度位置 × 分歧度** 三个维度，通过模式匹配判定当前市场状态，而非简单的线性五阶段模型。

| 状态     | 特征                                | 典型场景        |
| ------ | --------------------------------- | ----------- |
| 🔥 过热期 | 情绪极度乐观(>70)，热度过热(>75)，一致性高(<0.35) | 炒作高潮，一致性看多  |
| 🚀 加速期 | 情绪偏多，热度活跃，分歧适中，情绪和热度均上行           | 多头趋势加速      |
| 📊 活跃期 | 情绪偏多，热度活跃，多空博弈                    | 热点轮动，板块活跃   |
| 📉 退热期 | 情绪仍偏多但下降，热度回落                     | 炒作接近尾声      |
| ⚔️ 分歧期 | 多空分歧极大(>0.65)，方向不明                | 震荡整理，等待方向选择 |
| 😨 恐慌期 | 情绪偏空(<45)，热度高(>50)，分歧大            | 下跌过程中恐慌蔓延   |
| 🌱 蓄势期 | 情绪中性(45-55)，热度低(<30)，分歧大          | 横盘蓄势，等待突破   |
| 🧊 存量期 | 情绪偏多(>55)但热度低迷(<30)               | 存量博弈，底部吸筹   |
| 🥶 低迷期 | 情绪偏空(<45)，热度低迷(<25)               | 关注度低，交投清淡   |
| ❄️ 冰点期 | 情绪极度悲观(<30)，热度低迷(<25)，一致性高        | 一致性悲观，情绪见底  |
| 📊 震荡期 | 其他中性组合                            | 多空平衡，区间震荡   |

## 免责声明

本工具仅为散户情绪数据分析，不构成任何投资建议。股市有风险，投资需谨慎。
