# -*- coding: utf-8 -*-
"""
散户情绪分析系统 - 主入口
每日手动运行，生成雪球情绪日报 + 股吧情绪日报
"""
import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import Dict, List

# 确保项目根目录在路径中
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

# 设置 Playwright 浏览器路径（如果项目目录下有 browsers 文件夹）
_browsers_dir = BASE_DIR / "browsers"
if _browsers_dir.exists():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_browsers_dir))

from config import settings as _settings
from config.settings import HOT_STOCKS_COUNT, GUBA_PAGE_COUNT, XUEQIU_POST_COUNT
from collectors.hot_stocks import get_hot_stocks_from_gainers
from collectors.guba_crawler import fetch_guba_posts, close_browser as close_guba_browser, get_stock_rank
from collectors.xueqiu_crawler import (
    fetch_xueqiu_posts, get_hot_stocks_from_xueqiu, get_collect_records,
    close_browser as close_xq_browser,
    STATUS_OK, STATUS_NO_COOKIE,
)
from analysis.sentiment import get_sentiment_analyzer
from analysis.metrics import (
    calculate_stock_metrics,
    calculate_market_overview,
)
from analysis.cycle_model import determine_cycle_stage, determine_market_cycle
from storage.data_store import (
    save_daily_data, save_raw_posts_csv, save_history_snapshot, load_history,
    save_watchlist_history, load_watchlist_history, load_daily_data,
    load_watchlist_data,
    save_raw_posts_json, load_raw_posts_json,
    save_market_heat, load_market_heat, get_market_heat_by_date,
    save_collect_status, HISTORY_FILE,
)
from report.report_generator import generate_report

# 新增配置项用 getattr 兜底，兼容未更新的本地 settings.py
XUEQIU_HOT_MAX_PAGES = getattr(_settings, "XUEQIU_HOT_MAX_PAGES", 1)
MAX_BACKFILL_DAYS = getattr(_settings, "MAX_BACKFILL_DAYS", 7)

from collectors.market_heat import (
    fetch_uv_history, build_market_heat_data,
    get_market_heat_for_date, close_browser as close_market_heat_browser,
)

WATCHLIST_FILE = BASE_DIR / "config" / "watchlist.json"


def _load_watchlist() -> List[Dict]:
    """加载自选股配置"""
    if not WATCHLIST_FILE.exists():
        return []
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _merge_posts(new_posts: List[Dict], existing_posts: List[Dict]) -> List[Dict]:
    """合并新旧帖子，按 post_id 或 url 去重，新帖优先"""
    seen = set()
    merged = []
    for p in new_posts:
        key = p.get("post_id") or p.get("url") or f"{p.get('stock_code','')}_{p.get('title','')}_{p.get('publish_time','')}"
        if key not in seen:
            seen.add(key)
            merged.append(p)
    for p in existing_posts:
        key = p.get("post_id") or p.get("url") or f"{p.get('stock_code','')}_{p.get('title','')}_{p.get('publish_time','')}"
        if key not in seen:
            seen.add(key)
            merged.append(p)
    return merged


def _to_xq_symbol(code: str) -> str:
    """将纯数字股票代码转为雪球格式（带交易所前缀）"""
    code = code.upper()
    if code.startswith(("SH", "SZ")):
        return code
    if code.startswith("6"):
        return "SH" + code
    return "SZ" + code


def _analyze_source(posts: List[Dict], hot_stocks: List[Dict],
                    source: str, market_heat: Dict = None) -> Dict:
    """
    对单一来源的帖子进行情绪分析、指标计算和周期判断

    Args:
        posts: 该来源的帖子列表（已带情绪分析结果）
        hot_stocks: 热门股票列表
        source: 来源标识（"xueqiu" / "guba"）
        market_heat: 可选的大盘热度数据（用于覆盖整体热度）

    Returns:
        dict: 完整的分析结果数据
    """
    if not posts:
        return {}

    # 按股票分组
    stock_posts = defaultdict(list)
    for post in posts:
        stock_key = post.get("stock_code", "")
        stock_posts[stock_key].append(post)

    # 计算各股票指标
    stock_metrics = []
    for stock in hot_stocks:
        symbol = stock["code"]
        sposts = stock_posts.get(symbol, [])
        if not sposts:
            continue
        xq_heat = 0  # 弃用平台原生热度值
        guba_rank = get_stock_rank(stock.get("symbol", "")) if source == "guba" else 0
        # 股吧使用简单平均（避免财富号推流扭曲），雪球使用加权
        use_weighted = (source == "xueqiu")
        # 从帖子中提取关注量（雪球爬虫写入每条帖子）
        follow_count = sposts[0].get("stock_followers", 0) if source == "xueqiu" else 0
        metrics = calculate_stock_metrics(
            sposts, symbol, stock["name"],
            xueqiu_heat=xq_heat, guba_rank=guba_rank,
            weighted=use_weighted, follow_count=follow_count,
        )
        metrics["change_percent"] = stock.get("change_percent", 0)
        metrics["price"] = stock.get("price", 0)
        metrics["turnover_rate"] = stock.get("turnover_rate", 0)
        metrics["cycle_stage"] = determine_cycle_stage(metrics, history=None)
        stock_metrics.append(metrics)

    stock_metrics.sort(key=lambda x: x["heat_score"], reverse=True)

    # 计算市场概览（股吧使用外部大盘热度数据）
    overview = calculate_market_overview(stock_metrics, posts, market_heat=market_heat)

    # 计算市场周期
    market_cycle = determine_market_cycle(overview, [], stock_metrics)

    return {
        "overview": overview,
        "stock_metrics": stock_metrics,
        "market_cycle": market_cycle,
        "all_posts_count": len(posts),
        "hot_stocks_count": len(hot_stocks),
    }


def _today_str() -> str:
    """今天的 YYYYMMDD"""
    return datetime.now().strftime("%Y%m%d")


def _to_iso(date_str: str) -> str:
    """YYYYMMDD → YYYY-MM-DD"""
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"


def collect_watchlist_data(date_str: str = None, guba_pages: int = None,
                           xueqiu_count: int = None, skip_guba: bool = False,
                           skip_xueqiu: bool = False) -> Dict:
    """
    只采集自选股的双平台数据

    与市场链路完全解耦：不读热股榜、不碰大盘 UV 指数、不写 history.json。
    因此即使热股榜接口全挂，这条链路依然能独立跑完；也因此可以指定任意历史日期补采。

    Args:
        date_str: 目标日期 YYYYMMDD，None 表示今天
        guba_pages: 股吧抓取页数
        xueqiu_count: 雪球抓取数量
        skip_guba: 跳过股吧采集
        skip_xueqiu: 跳过雪球采集

    Returns:
        dict: {"guba": {...}|None, "xueqiu": {...}|None}，各含 watchlist_metrics
    """
    if date_str is None:
        date_str = _today_str()
    target_iso = _to_iso(date_str)
    is_backfill = date_str != _today_str()

    if is_backfill:
        print(f"\n🕰️  按日期补采自选股：{target_iso}")
        print("    热度依赖实时人气排名，无法回溯，股吧热度将记为不可用（显示为「—」）")

    watchlist = _load_watchlist()
    if not watchlist:
        print("  [WARN] 自选股列表为空，跳过自选股采集")
        return {"guba": None, "xueqiu": None}

    watchlist_guba_posts = []
    watchlist_xq_posts = []
    watchlist_xq_stock_info = {}  # 雪球自选股基本信息（即使没有帖子也有）
    xq_wl_records = {}            # 雪球自选股采集状态（skip_xueqiu 时保持为空）

    # ========== 1. 采集 ==========
    if not skip_guba:
        print(f"\n  --- 自选股 - 股吧 ---")
        for stock in watchlist:
            print(f"  {stock['name']}({stock['code']}) - 股吧...")
            try:
                posts = fetch_guba_posts(
                    stock["symbol"], stock["name"], page_count=guba_pages,
                    target_date=target_iso,
                    # 人气排名页只有「此刻」的数值，补采时跳过，避免拿今天的排名标注历史数据
                    fetch_rank=not is_backfill,
                )
                for p in posts:
                    p["stock_code"] = stock["code"]
                watchlist_guba_posts.extend(posts)
            except Exception as e:
                print(f"      [ERROR] 自选股吧采集失败: {e}")
        try:
            close_guba_browser()
        except:
            pass

    if not skip_xueqiu:
        print(f"\n  --- 自选股 - 雪球 ---")
        for stock in watchlist:
            xq_code = _to_xq_symbol(stock["code"])
            print(f"  {stock['name']}({stock['code']}) - 雪球...")
            try:
                posts, stock_info = fetch_xueqiu_posts(
                    xq_code, stock["name"], count=xueqiu_count,
                    return_stock_info=True, target_date=target_iso,
                )
                watchlist_xq_posts.extend(posts)
                watchlist_xq_stock_info[stock["code"]] = stock_info
            except Exception as e:
                print(f"      [ERROR] 自选雪球采集失败: {e}")
                watchlist_xq_stock_info[stock["code"]] = {
                    "stock_code": xq_code, "stock_name": stock["name"],
                    "stock_followers": 0, "stock_price": 0, "change_percent": 0
                }

        xq_wl_records = get_collect_records()
        incomplete = [r for r in xq_wl_records.values()
                      if r.get("status") not in (STATUS_OK, STATUS_NO_COOKIE)]
        if incomplete:
            print(f"\n  ⚠ 雪球 {len(incomplete)} 只自选股采集不完整: " + "、".join(
                f"{r['stock_name']}[{r['status_text']}]" for r in incomplete))
        try:
            close_xq_browser()
        except:
            pass

    if not watchlist_guba_posts and not watchlist_xq_posts:
        print("  [ERROR] 自选股没有采集到任何帖子数据")
        if xq_wl_records:
            try:
                save_collect_status(xq_wl_records, date_str, source="xueqiu_wl")
            except Exception as e:
                print(f"  [WARN] 采集状态落盘失败: {e}")
        return {"guba": None, "xueqiu": None}

    # ========== 2. 情绪分析（自选股走大模型）==========
    print(f"\n💭 自选股情绪分析...")
    from analysis.llm_sentiment import analyze_posts_with_llm
    watchlist_guba_sent = analyze_posts_with_llm(watchlist_guba_posts) if watchlist_guba_posts else []
    watchlist_xq_sent = analyze_posts_with_llm(watchlist_xq_posts) if watchlist_xq_posts else []

    # 增量合并：同一天多次采集时，与已有帖子去重合并
    if not skip_guba:
        existing_guba_wl = load_raw_posts_json(date_str, source="guba_wl")
        if existing_guba_wl and watchlist_guba_sent:
            before_wl = len(watchlist_guba_sent)
            watchlist_guba_sent = _merge_posts(watchlist_guba_sent, existing_guba_wl)
            print(f"  股吧自选股增量合并: {before_wl} + {len(existing_guba_wl)} → {len(watchlist_guba_sent)} 条（去重）")
    if not skip_xueqiu:
        existing_xq_wl = load_raw_posts_json(date_str, source="xueqiu_wl")
        if existing_xq_wl and watchlist_xq_sent:
            before_wl = len(watchlist_xq_sent)
            watchlist_xq_sent = _merge_posts(watchlist_xq_sent, existing_xq_wl)
            print(f"  雪球自选股增量合并: {before_wl} + {len(existing_xq_wl)} → {len(watchlist_xq_sent)} 条（去重）")

    # ========== 3. 计算指标（按平台独立）==========
    watchlist_guba_metrics = []
    watchlist_xq_metrics = []

    if watchlist_guba_sent:
        print(f"  --- 股吧自选股 ---")
        for stock in watchlist:
            code = stock["code"]
            posts = [p for p in watchlist_guba_sent if p.get("stock_code") == code]
            if not posts:
                continue
            guba_rank = 0
            if not is_backfill:
                try:
                    guba_rank = get_stock_rank(stock.get("symbol", ""))
                except Exception:
                    pass
            metrics = calculate_stock_metrics(
                posts, code, stock["name"],
                xueqiu_heat=0, guba_rank=guba_rank,
                weighted=False,
            )
            metrics["cycle_stage"] = determine_cycle_stage(metrics, history=None)
            metrics["guba_posts"] = len(posts)
            metrics["xueqiu_posts"] = 0
            metrics["stock_price"] = posts[0].get("stock_price", 0)
            metrics["change_percent"] = posts[0].get("change_percent", 0)
            if is_backfill:
                _mark_heat_unavailable(metrics)
            watchlist_guba_metrics.append(metrics)
            save_watchlist_history(
                code, date_str, metrics["sentiment_index"],
                metrics["heat_score"], metrics["divergence"],
                metrics["cycle_stage"]["stage_name"],
                metrics["total_posts"], source="guba")
            print(f"  {stock['name']}({code}): {metrics['total_posts']}帖, "
                  f"情绪{metrics['sentiment_index']:.1f}, 热度{metrics['heat_score']:.1f}, "
                  f"{metrics['cycle_stage']['stage_emoji']} {metrics['cycle_stage']['stage_name']}")
        try:
            close_guba_browser()
        except:
            pass

    print(f"  --- 雪球自选股 ---")
    for stock in watchlist:
        code = stock["code"]
        xq_code = _to_xq_symbol(code)
        posts = [p for p in watchlist_xq_sent if p.get("stock_code") == xq_code]
        stock_info = watchlist_xq_stock_info.get(code, {})
        follow_count = stock_info.get("stock_followers", 0)
        stock_price = stock_info.get("stock_price", 0)
        change_percent = stock_info.get("change_percent", 0)

        if posts:
            if not follow_count:
                follow_count = posts[0].get("stock_followers", 0)
            if not stock_price:
                stock_price = posts[0].get("stock_price", 0)
            if not change_percent:
                change_percent = posts[0].get("change_percent", 0)

        metrics = calculate_stock_metrics(
            posts, code, stock["name"],
            xueqiu_heat=0, guba_rank=0,
            weighted=True, follow_count=follow_count,
        )
        metrics["cycle_stage"] = determine_cycle_stage(metrics, history=None)
        metrics["guba_posts"] = 0
        metrics["xueqiu_posts"] = len(posts)
        metrics["stock_price"] = stock_price
        metrics["change_percent"] = change_percent
        # 采集状态：报告里用来区分「当日真没人讨论」与「被限流没抓到」
        metrics["collect_status"] = stock_info.get("collect_status", "")
        metrics["collect_status_text"] = stock_info.get("collect_status_text", "")
        metrics["collect_pages"] = stock_info.get("collect_pages", 0)
        if is_backfill:
            metrics["backfill"] = True
        watchlist_xq_metrics.append(metrics)
        save_watchlist_history(
            code, date_str, metrics["sentiment_index"],
            metrics["heat_score"], metrics["divergence"],
            metrics["cycle_stage"]["stage_name"],
            metrics["total_posts"], source="xueqiu")
        print(f"  {stock['name']}({code}): {metrics['total_posts']}帖, "
              f"情绪{metrics['sentiment_index']:.1f}, 热度{metrics['heat_score']:.1f}, "
              f"{metrics['cycle_stage']['stage_emoji']} {metrics['cycle_stage']['stage_name']}")

    # ========== 4. 落盘（只写自选股文件）==========
    out = {"guba": None, "xueqiu": None}

    if watchlist_guba_metrics:
        data = {
            "watchlist_metrics": watchlist_guba_metrics,
            "backfill": is_backfill,
        }
        if watchlist_guba_sent:
            save_raw_posts_json(watchlist_guba_sent, date_str, source="guba_wl")
        save_daily_data(data, date_str, source="guba_wl")
        out["guba"] = data

    if watchlist_xq_metrics:
        data = {
            "watchlist_metrics": watchlist_xq_metrics,
            "collect_status": xq_wl_records,
            "backfill": is_backfill,
        }
        if watchlist_xq_sent:
            save_raw_posts_json(watchlist_xq_sent, date_str, source="xueqiu_wl")
        if xq_wl_records:
            save_collect_status(xq_wl_records, date_str, source="xueqiu_wl")
        save_daily_data(data, date_str, source="xueqiu_wl")
        out["xueqiu"] = data

    return out


def _mark_heat_unavailable(metrics: Dict) -> None:
    """
    标记「这一天拿不到热度」，并把热度归零

    必须显式置位：calculate_stock_metrics 在 guba_rank<=0 时会静默回落到雪球那套
    互动量公式，产出一个换了刻度的假热度。补采日没有实时人气排名，那个数不是热度低，
    是根本不存在——报告里渲染成「—」而不是 0.0。
    """
    metrics["heat_score"] = 0.0
    metrics["heat_unavailable"] = True
    metrics["backfill"] = True


def collect_market_data(stock_count: int = None, guba_pages: int = None,
                        xueqiu_count: int = None, skip_guba: bool = False,
                        skip_xueqiu: bool = False,
                        collect_only: bool = False) -> Dict:
    """
    采集全市场口径的数据（热股榜 → 帖子 → 大盘热度 → 指标 → 落盘）

    只写市场文件（{source}_data_{date}.json / history.json / 市场级 CSV）。
    自选股由 collect_watchlist_data 单独负责，两者互不覆盖。

    Args:
        stock_count: 监控股票数量，默认使用配置
        guba_pages: 股吧抓取页数
        xueqiu_count: 雪球抓取数量
        skip_guba: 跳过股吧采集
        skip_xueqiu: 跳过雪球采集
        collect_only: 只采集数据，不生成报告

    Returns:
        dict: { "xueqiu": {...}, "guba": {...} } 各来源的分析结果，
            另含 "_reports": {来源: 报告路径}
    """
    if stock_count is None:
        stock_count = HOT_STOCKS_COUNT

    # ========== 1. 获取热门股票列表（各平台独立）==========
    print("\n📈 [1/5] 获取热门股票列表...")

    xueqiu_hot_stocks = []
    guba_hot_stocks = []

    # 获取雪球热股榜
    if not skip_xueqiu:
        print("  获取雪球热股榜...")
        xueqiu_hot_stocks = get_hot_stocks_from_xueqiu(stock_count)
        try:
            close_xq_browser()
        except:
            pass
        if xueqiu_hot_stocks:
            print(f"  ✓ 雪球热股榜: {len(xueqiu_hot_stocks)} 只")
        else:
            print("  [WARN] 雪球热股榜获取失败")

    # 获取股吧人气榜
    if not skip_guba:
        print("  获取股吧人气榜...")
        guba_hot_stocks = get_hot_stocks_from_gainers(stock_count)
        if guba_hot_stocks:
            print(f"  ✓ 股吧人气榜: {len(guba_hot_stocks)} 只")
        else:
            print("  [WARN] 股吧人气榜获取失败")

    if not xueqiu_hot_stocks and not guba_hot_stocks:
        print("  [ERROR] 未能获取任何热门股票列表，跳过市场采集")
        return {}

    # ========== 2. 采集帖子数据 ==========
    print(f"\n🕸️  [2/5] 采集社交媒体帖子...")

    guba_posts_list = []
    xueqiu_posts_list = []
    source_stats = defaultdict(int)

    # 阶段 A: 股吧采集（使用股吧人气榜）
    if not skip_guba and guba_hot_stocks:
        print(f"\n  --- 东方财富股吧（股吧人气榜 TOP{len(guba_hot_stocks)}）---")
        for i, stock in enumerate(guba_hot_stocks, 1):
            symbol = stock["symbol"]
            code = stock["code"]
            name = stock["name"]
            print(f"\n  [{i}/{len(guba_hot_stocks)}] {name}({code}) - 股吧...")
            try:
                posts = fetch_guba_posts(symbol, name, page_count=guba_pages)
                for p in posts:
                    p["stock_code"] = code
                guba_posts_list.extend(posts)
                source_stats["guba"] += len(posts)
            except Exception as e:
                print(f"      [ERROR] 股吧采集失败: {e}")
        try:
            close_guba_browser()
        except:
            pass

    # 阶段 B: 雪球采集（使用雪球热股榜）
    if not skip_xueqiu and xueqiu_hot_stocks:
        print(f"\n  --- 雪球（雪球热股榜 TOP{len(xueqiu_hot_stocks)}）---")
        for i, stock in enumerate(xueqiu_hot_stocks, 1):
            code = stock["code"]
            name = stock["name"]
            print(f"\n  [{i}/{len(xueqiu_hot_stocks)}] {name}({code}) - 雪球...")
            try:
                posts = fetch_xueqiu_posts(code, name, count=xueqiu_count,
                                            max_pages=XUEQIU_HOT_MAX_PAGES)
                xueqiu_posts_list.extend(posts)
                source_stats["xueqiu"] += len(posts)
            except Exception as e:
                print(f"      [ERROR] 雪球采集失败: {e}")

        # 采集状态汇总：把「被限流」与「当日真没人讨论」区分开
        xq_records = get_collect_records()
        incomplete = [r for r in xq_records.values()
                      if r.get("status") not in (STATUS_OK, STATUS_NO_COOKIE)]
        if incomplete:
            print(f"\n  ⚠ 雪球 {len(incomplete)} 只股票采集不完整: " + "、".join(
                f"{r['stock_name']}[{r['status_text']}]" for r in incomplete))

        try:
            close_xq_browser()
        except:
            pass

    all_posts = guba_posts_list + xueqiu_posts_list
    print(f"\n  ✓ 采集完成，共 {len(all_posts)} 条帖子")
    for src, cnt in source_stats.items():
        src_name = {"guba": "东方财富股吧", "xueqiu": "雪球"}.get(src, src)
        print(f"    - {src_name}: {cnt} 条")

    date_str = _today_str()

    if not all_posts:
        print("  [ERROR] 没有采集到任何帖子数据")
        # 一条帖子都没采到，但采集状态仍需留痕，便于事后区分「被限流」与「真没数据」
        if not skip_xueqiu and xueqiu_hot_stocks:
            try:
                save_collect_status(get_collect_records(), date_str, source="xueqiu")
            except Exception as e:
                print(f"  [WARN] 采集状态落盘失败: {e}")
        return {}

    # ========== 3. 情绪分析 ==========
    print(f"\n💭 [3/5] 情绪分析...")
    analyzer = get_sentiment_analyzer()

    def analyze_posts(posts):
        result = []
        for post in posts:
            sentiment = analyzer.analyze_post(post)
            result.append({**post, **sentiment})
        return result

    guba_with_sent = analyze_posts(guba_posts_list) if not skip_guba else []
    xueqiu_with_sent = analyze_posts(xueqiu_posts_list) if not skip_xueqiu else []

    # 增量合并：当天多次采集时，与已有帖子去重合并
    if not skip_guba:
        existing_guba = load_raw_posts_json(date_str, source="guba")
        if existing_guba:
            before = len(guba_with_sent)
            guba_with_sent = _merge_posts(guba_with_sent, existing_guba)
            print(f"  股吧增量合并: {before} + {len(existing_guba)} → {len(guba_with_sent)} 条（去重）")
    if not skip_xueqiu:
        existing_xq = load_raw_posts_json(date_str, source="xueqiu")
        if existing_xq:
            before = len(xueqiu_with_sent)
            xueqiu_with_sent = _merge_posts(xueqiu_with_sent, existing_xq)
            print(f"  雪球增量合并: {before} + {len(existing_xq)} → {len(xueqiu_with_sent)} 条（去重）")

    total_posts = len(guba_with_sent) + len(xueqiu_with_sent)
    all_pos = sum(1 for p in guba_with_sent + xueqiu_with_sent if p["sentiment"] == "positive")
    all_neg = sum(1 for p in guba_with_sent + xueqiu_with_sent if p["sentiment"] == "negative")
    all_neu = sum(1 for p in guba_with_sent + xueqiu_with_sent if p["sentiment"] == "neutral")

    print(f"  ✓ 分析完成（共 {total_posts} 条）")
    if total_posts:
        print(f"    看多: {all_pos} ({all_pos/total_posts*100:.1f}%)")
        print(f"    看空: {all_neg} ({all_neg/total_posts*100:.1f}%)")
        print(f"    中性: {all_neu} ({all_neu/total_posts*100:.1f}%)")

    # ========== 4. 采集市场热度（大盘UV指数）==========
    print(f"\n📈 [4/6] 采集市场热度数据...")
    market_heat_data = {}
    guba_market_heat_today = None

    if not skip_guba:
        try:
            uv_history = fetch_uv_history(90)
            if uv_history:
                today_str = datetime.now().strftime("%Y-%m-%d")
                market_heat_data = build_market_heat_data(uv_history, today_str)

                # 保存（回填历史真实值，更新临时值）
                save_market_heat(market_heat_data)

                # 取今天的热度数据
                guba_market_heat_today = market_heat_data.get(today_str, {})
                if guba_market_heat_today:
                    status = "临时值" if guba_market_heat_today.get("is_provisional") else "真实值"
                    print(f"  ✓ 大盘热度: {guba_market_heat_today['heat_score']:.1f}分 "
                          f"(UV={guba_market_heat_today['uv_index']:.0f}万, {status})")
                else:
                    print("  [WARN] 未获取到今日大盘热度")
            else:
                print("  [WARN] 市场热度采集失败，使用内部热度")
        except Exception as e:
            print(f"  [ERROR] 市场热度采集异常: {e}")
        finally:
            try:
                close_market_heat_browser()
            except:
                pass

    # ========== 5. 计算指标（按来源分开）==========
    print(f"\n📐 [5/6] 计算情绪指标...")

    results = {}

    if guba_with_sent:
        results["guba"] = _analyze_source(guba_with_sent, guba_hot_stocks, "guba",
                                            market_heat=guba_market_heat_today)
        print(f"  ✓ 股吧: {len(results['guba']['stock_metrics'])} 只股票，"
              f"情绪 {results['guba']['overview']['overall_sentiment']:.1f}，"
              f"{results['guba']['market_cycle']['stage_emoji']} {results['guba']['market_cycle']['stage_name']}")

    if xueqiu_with_sent:
        results["xueqiu"] = _analyze_source(xueqiu_with_sent, xueqiu_hot_stocks, "xueqiu")
        print(f"  ✓ 雪球: {len(results['xueqiu']['stock_metrics'])} 只股票，"
              f"情绪 {results['xueqiu']['overview']['overall_sentiment']:.1f}，"
              f"{results['xueqiu']['market_cycle']['stage_emoji']} {results['xueqiu']['market_cycle']['stage_name']}")

    # ========== 6. 保存数据 & 生成报告（按来源分开）==========
    if collect_only:
        print(f"\n💾 [6/6] 保存数据（跳过报告生成）...")
    else:
        print(f"\n📄 [6/6] 保存数据并生成报告...")

    _run_heat_backfill()

    report_paths = {}

    if "guba" in results:
        save_daily_data(results["guba"], source="guba")
        save_raw_posts_json(guba_with_sent, source="guba")
        try:
            save_raw_posts_csv(guba_with_sent, source="guba")
        except PermissionError:
            print(f"  ⚠ CSV 写入被拒，跳过（不影响 JSON 数据）")
        save_history_snapshot(results["guba"], source="guba")
        if not collect_only:
            report_paths["guba"] = generate_report(results["guba"], data_source="guba")

    if "xueqiu" in results:
        results["xueqiu"]["collect_status"] = get_collect_records()
        save_collect_status(get_collect_records(), date_str, source="xueqiu")
        save_daily_data(results["xueqiu"], source="xueqiu")
        save_raw_posts_json(xueqiu_with_sent, source="xueqiu")
        try:
            save_raw_posts_csv(xueqiu_with_sent, source="xueqiu")
        except PermissionError:
            print(f"  ⚠ CSV 写入被拒，跳过（不影响 JSON 数据）")
        save_history_snapshot(results["xueqiu"], source="xueqiu")
        if not collect_only:
            report_paths["xueqiu"] = generate_report(results["xueqiu"], data_source="xueqiu")

    results["_reports"] = report_paths
    return results


def run_sentiment_analysis(stock_count: int = None, guba_pages: int = None,
                           xueqiu_count: int = None, skip_guba: bool = False,
                           skip_xueqiu: bool = False, collect_only: bool = False,
                           date_str: str = None, run_market: bool = True,
                           run_watchlist: bool = True) -> Dict:
    """
    运行情绪分析流程：自选股链路 + 市场链路，各自独立落盘与出报告

    Args:
        stock_count: 市场链路监控股票数量，默认使用配置
        guba_pages: 股吧抓取页数
        xueqiu_count: 雪球抓取数量
        skip_guba: 跳过股吧采集
        skip_xueqiu: 跳过雪球采集
        collect_only: 只采集数据，不生成报告
        date_str: 自选股目标日期 YYYYMMDD（市场链路不可回溯，只对自选股生效）
        run_market: 是否运行市场链路
        run_watchlist: 是否运行自选股链路

    Returns:
        dict: {
            "watchlist": {"guba": {...}|None, "xueqiu": {...}|None},
            "market": {"guba": {...}, "xueqiu": {...}},
            "reports": {名称: 路径},
        }
    """
    start_time = time.time()
    print("=" * 60)
    print("📊 散户情绪分析系统")
    print(f"🕐 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    out = {"watchlist": {}, "market": {}, "reports": {}}

    # ---- 自选股链路必须先跑 ----
    # 雪球按 IP 限流（实测约 4 次请求/30 秒），20 只热股足以把配额耗尽，
    # 自选股排在热股之后会成批抓不到帖子。顺序不能调换。
    if run_watchlist:
        out["watchlist"] = collect_watchlist_data(
            date_str=date_str,
            guba_pages=guba_pages,
            xueqiu_count=xueqiu_count,
            skip_guba=skip_guba,
            skip_xueqiu=skip_xueqiu,
        )
        if not collect_only and (out["watchlist"].get("guba") or out["watchlist"].get("xueqiu")):
            from report.report_generator import generate_watchlist_report
            out["reports"]["watchlist"] = generate_watchlist_report(
                out["watchlist"].get("guba") or {"watchlist_metrics": []},
                out["watchlist"].get("xueqiu") or {"watchlist_metrics": []},
            )

    # 自选股采完立刻重置采集记录，免得两个链路的雪球状态混在一起
    if run_market and not skip_xueqiu:
        try:
            from collectors.xueqiu_crawler import reset_collect_records
            reset_collect_records()
        except Exception:
            pass

    if run_market:
        out["market"] = collect_market_data(
            stock_count=stock_count,
            guba_pages=guba_pages,
            xueqiu_count=xueqiu_count,
            skip_guba=skip_guba,
            skip_xueqiu=skip_xueqiu,
            collect_only=collect_only,
        )
        out["reports"].update(out["market"].get("_reports", {}))

    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"✅ 分析完成！总耗时: {elapsed:.1f} 秒")
    for src, path in out["reports"].items():
        src_name = {"guba": "股吧", "xueqiu": "雪球", "watchlist": "自选股"}.get(src, src)
        print(f"📄 {src_name}报告: {path}")
    print(f"{'=' * 60}")

    return out


def _apply_market_heat(guba_data: Dict, date_iso: str) -> bool:
    """
    用 market_heat.json 中的 UV 指数覆盖股吧整体热度

    股吧整体热度不是「个股热度均值」，而是东方财富 APP 的 UV 指数归一化。
    calculate_market_overview 在没有 market_heat 时会退化成均值口径，因此必须覆盖。

    Args:
        guba_data: 股吧数据（就地修改）
        date_iso: "YYYY-MM-DD"

    Returns:
        bool: 该日期是否有可用 UV 数据
    """
    from collectors.market_heat import uv_to_heat_score

    market_heat = get_market_heat_by_date(date_iso)
    if not market_heat or market_heat.get("uv_index", 0) <= 0:
        return False

    overview = guba_data.setdefault("overview", {})
    overview["overall_heat"] = uv_to_heat_score(market_heat["uv_index"])
    overview["heat_is_provisional"] = market_heat.get("is_provisional", False)
    overview["heat_source"] = market_heat.get("source", "external")
    overview["heat_uv_index"] = market_heat.get("uv_index", 0)
    return True


def backfill_guba_history_heat() -> Dict:
    """
    用后来公布的真实 UV 指数回填股吧历史热度

    UV 指数有较长滞后，当天只能拿「最近一周均值」顶替；2026-09-19 之前整体热度更是
    压根没接 UV（heat_source=internal，实际存的是「个股热度均值」，口径完全不同）。
    market_heat.json 会自我修复（真实值覆盖临时值），但 history.json 是每天写死的快照，
    不会跟着更新，报告里的历史热度曲线因此长期偏离实际。

    这里以 market_heat.json 为准，重算所有已有真实 UV 的日期，并同步修正当日存档
    guba_data_*.json 的整体热度与周期阶段；UV 尚未公布的日期保持原样。

    Returns:
        dict: {"fixed": [被修正的日期...], "pending": [UV 仍未公布的日期...]}
    """
    from collectors.market_heat import uv_to_heat_score

    empty = {"fixed": [], "pending": []}
    if not HISTORY_FILE.exists():
        return empty

    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)

    entries = history.get("guba", [])
    if not entries:
        return empty

    market_heat = load_market_heat()
    if not market_heat:
        return empty

    fixed, pending = [], []
    changed = False

    for entry in entries:
        date_str = entry.get("date", "")
        if len(date_str) != 8:
            continue
        date_iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        mh = market_heat.get(date_iso)
        if not mh or mh.get("uv_index", 0) <= 0:
            continue

        is_provisional = bool(mh.get("is_provisional"))
        new_heat = uv_to_heat_score(mh["uv_index"])

        if is_provisional:
            # UV 尚未公布，用最近一周均值顶着。值本身会随窗口滑动而变，
            # 但仍以 market_heat.json 为准，并记上口径，报告里才看得出这是占位值。
            pending.append(date_str)
            if (entry.get("heat_is_provisional") is not True
                    or entry.get("heat_source") != mh.get("source")
                    or abs(new_heat - entry.get("heat", 0)) >= 0.05):
                entry["heat"] = new_heat
                entry["heat_source"] = mh.get("source", "week_avg")
                entry["heat_is_provisional"] = True
                changed = True
            continue

        new_stage = entry.get("stage_name", "")

        # 先在内存里算出正确阶段，判断确实有变化再落盘：
        # 每次都写会把历史存档的 generated_at 刷成今天，也会平白多出十几次磁盘写。
        day = load_daily_data(date_str, source="guba")
        day_fixed = bool(day) and _apply_market_heat(day, date_iso)
        if day_fixed:
            day["market_cycle"] = determine_market_cycle(
                day["overview"], [], day.get("stock_metrics", []))
            day["overview"]["cycle_stage"] = day["market_cycle"]
            new_stage = day["market_cycle"].get("stage_name", new_stage)

        if (abs(new_heat - entry.get("heat", 0)) < 0.05
                and entry.get("heat_is_provisional") is False
                and entry.get("stage_name", "") == new_stage):
            continue

        # 同步修正当日存档，让 JSON、历史快照、报告三者口径一致
        if day_fixed:
            save_daily_data(day, date_str=date_str, source="guba")

        entry["heat"] = new_heat
        entry["heat_source"] = mh.get("source", "external")
        entry["heat_is_provisional"] = False
        entry["stage_name"] = new_stage
        fixed.append(date_str)
        changed = True

    if changed:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    return {"fixed": fixed, "pending": pending}


def _fmt_date_list(dates: List[str], limit: int = 6) -> str:
    """把 ["20260910", ...] 压成 "09-10, 09-11 …" 便于打日志"""
    shown = [f"{d[4:6]}-{d[6:8]}" for d in dates[:limit]]
    if len(dates) > limit:
        shown.append(f"…共{len(dates)}天")
    return "、".join(shown)


def _run_heat_backfill():
    """回填股吧历史热度（幂等，正常流程和 --report-only 都会跑）"""
    try:
        result = backfill_guba_history_heat()
    except Exception as e:
        print(f"  ⚠ 历史热度回填失败（不影响本次报告）: {e}")
        return
    if result["fixed"]:
        print(f"  🔧 回填股吧历史热度 {len(result['fixed'])} 天: "
              f"{_fmt_date_list(result['fixed'])}")
    if result["pending"]:
        print(f"  ⏳ 股吧热度仍是临时值 {len(result['pending'])} 天"
              f"（UV 指数尚未公布）: {_fmt_date_list(result['pending'])}")


def _recalc_heat_scores(metrics: List[Dict], source: str) -> None:
    """
    用当前公式重新计算热度（参数变更后需重算）

    Args:
        metrics: 待重算的指标列表（市场级传 stock_metrics，自选股传 watchlist_metrics）
        source: "guba" 或 "xueqiu"，决定用哪套公式
    """
    import math
    from config.settings import (
        XUEQIU_HEAT_INTERACTION_WEIGHT, XUEQIU_HEAT_FOLLOW_WEIGHT,
        XUEQIU_HEAT_INTERACTION_BASE, XUEQIU_HEAT_FOLLOW_BASE,
        GUBA_RANK_MAX,
    )
    if source == "xueqiu":
        for m in metrics:
            ti = m.get("total_interactions", 0)
            fc = m.get("follow_count", 0)
            if ti > 0:
                iscore = min(XUEQIU_HEAT_INTERACTION_WEIGHT,
                    math.log10(max(1, ti)) / math.log10(XUEQIU_HEAT_INTERACTION_BASE) * XUEQIU_HEAT_INTERACTION_WEIGHT)
            else:
                iscore = 0
            if fc > 0:
                fscore = min(XUEQIU_HEAT_FOLLOW_WEIGHT,
                    math.log10(max(1, fc)) / math.log10(XUEQIU_HEAT_FOLLOW_BASE) * XUEQIU_HEAT_FOLLOW_WEIGHT)
            else:
                fscore = 0
            m["heat_score"] = round(iscore + fscore, 2)
    elif source == "guba":
        for m in metrics:
            # 补采日没有实时人气排名，热度本就不存在，不要从旧热度反推排名造假数据
            if m.get("heat_unavailable"):
                m["heat_score"] = 0.0
                continue
            rank = m.get("guba_rank") or 0
            if rank > 0:
                m["heat_score"] = round(max(0, (1 - (rank - 1) ** 0.3 / GUBA_RANK_MAX ** 0.3) * 100), 2)
            else:
                # 旧数据没有 guba_rank，从旧热度反推排名再算新热度
                old_heat = m.get("heat_score", 0)
                if old_heat > 0 and old_heat <= 100:
                    old_ratio = old_heat / 100.0
                    if old_ratio < 1.0:
                        rank = max(1, round(10 ** ((1 - old_ratio) * math.log10(GUBA_RANK_MAX))))
                    else:
                        rank = 1
                    m["guba_rank"] = rank
                    m["heat_score"] = round(max(0, (1 - (rank - 1) ** 0.3 / GUBA_RANK_MAX ** 0.3) * 100), 2)


def _recalc_cycle_stages(metrics: List[Dict], source: str) -> None:
    """用当前阈值重新计算一个范围（市场级 / 自选股）的周期阶段"""
    from storage.data_store import load_watchlist_history
    for m in metrics:
        code = m.get("stock_code", "")
        hist = load_watchlist_history(code, source=source) if code else None
        m["cycle_stage"] = determine_cycle_stage(m, history=hist)


def _recalc_market_cycle(data: Dict) -> None:
    """重算市场级周期阶段（只对市场数据有意义）"""
    # 市场概览：overview 的键是 overall_sentiment / overall_heat，而 determine_cycle_stage
    # 读的是 sentiment_index / heat_score，直接把 overview 传进去会全部落到默认值
    # (50 / 0 / 0.5)，无论当日数据如何都判成「震荡期」。determine_market_cycle 专门
    # 负责这层键映射，市场级阶段必须走它。
    overview = data.get("overview", {})
    data["market_cycle"] = determine_market_cycle(
        overview, [], data.get("stock_metrics", []))
    overview["cycle_stage"] = data["market_cycle"]


def _recalc_overview_heat(data: Dict) -> None:
    """重算市场整体热度：由 _apply_market_heat 之后调用，兜底为个股热度均值"""
    overview = data.get("overview", {})
    stocks = data.get("stock_metrics", [])
    if stocks:
        overview["overall_heat"] = round(sum(m["heat_score"] for m in stocks) / len(stocks), 1)


def _fill_watchlist_prices(xq_wl: Dict, guba_wl: Dict, date_str: str) -> None:
    """用雪球侧的股价补齐股吧自选股缺失的股价和涨跌幅"""
    if not guba_wl:
        return
    xq_prices = {}
    for m in (xq_wl or {}).get("watchlist_metrics", []):
        code = m.get("stock_code", "")
        if m.get("stock_price", 0) > 0:
            xq_prices[code] = (m.get("stock_price", 0), m.get("change_percent", 0))

    missing = [m.get("stock_code", "") for m in guba_wl.get("watchlist_metrics", [])
               if m.get("stock_price", 0) == 0 and m.get("stock_code", "") not in xq_prices]
    api_prices = {}
    if missing:
        try:
            from collectors.price_fetcher import fetch_stock_prices
            api_prices = fetch_stock_prices(missing)
        except Exception as e:
            print(f"  [WARN] 补股价失败: {e}")

    filled = 0
    for m in guba_wl.get("watchlist_metrics", []):
        if m.get("stock_price", 0) != 0:
            continue
        code = m.get("stock_code", "")
        if code in xq_prices:
            m["stock_price"], m["change_percent"] = xq_prices[code]
            filled += 1
        elif code in api_prices:
            m["stock_price"], m["change_percent"] = api_prices[code]
            filled += 1
    if filled:
        print(f"  📊 补充了 {filled} 只自选股的股价信息")
    save_daily_data(guba_wl, date_str=date_str, source="guba_wl")


def generate_reports_only(date_str: str = None, skip_guba: bool = False,
                          skip_xueqiu: bool = False, only: str = None):
    """
    仅从已保存的数据生成报告（不重新抓取）

    Args:
        date_str: 目标日期 YYYYMMDD
        skip_guba / skip_xueqiu: 跳过对应平台
        only: None=市场+自选股，"market"=只出市场报告，"watchlist"=只出自选股报告
    """
    from report.report_generator import generate_report, generate_watchlist_report

    if date_str is None:
        date_str = _today_str()

    do_market = only in (None, "market")
    do_watchlist = only in (None, "watchlist")

    print(f"\n📄 从已保存数据生成报告 (日期: {date_str})")
    print(f"{'=' * 60}")

    if do_market:
        _run_heat_backfill()

    report_paths = {}

    # ---------- 市场报告 ----------
    if do_market:
        for src in ("guba", "xueqiu"):
            if (src == "guba" and skip_guba) or (src == "xueqiu" and skip_xueqiu):
                continue
            data = load_daily_data(date_str, source=src)
            if not data:
                print(f"  ✗ 未找到{ {'guba': '股吧', 'xueqiu': '雪球'}[src] }数据: {src}_data_{date_str}.json")
                continue
            data.pop("watchlist_metrics", None)  # 拆分前遗留的混合文件，自选股部分交给自选股报告
            _recalc_heat_scores(data.get("stock_metrics", []), src)
            if src == "guba":
                # 必须先套用 UV 大盘热度再算周期阶段：周期阶段读的是 overview.overall_heat，
                # 顺序反了就会用「个股热度均值」判阶段，和报告里显示的热度对不上。
                if not _apply_market_heat(data, _to_iso(date_str)):
                    _recalc_overview_heat(data)
            _recalc_market_cycle(data)
            save_daily_data(data, date_str=date_str, source=src)
            save_history_snapshot(data, source=src)
            report_paths[src] = generate_report(data, data_source=src)
            print(f"  ✓ {'股吧' if src == 'guba' else '雪球'}报告: {report_paths[src]}")

    # ---------- 自选股报告 ----------
    if do_watchlist:
        wl_data = {}
        for src in ("guba", "xueqiu"):
            if (src == "guba" and skip_guba) or (src == "xueqiu" and skip_xueqiu):
                continue
            data = load_watchlist_data(date_str, source=f"{src}_wl")
            if not data.get("watchlist_metrics"):
                print(f"  ✗ 未找到{ {'guba': '股吧', 'xueqiu': '雪球'}[src] }自选股数据: {src}_wl_data_{date_str}.json")
                continue
            _recalc_heat_scores(data.get("watchlist_metrics", []), src)
            _recalc_cycle_stages(data.get("watchlist_metrics", []), src)
            save_daily_data(data, date_str=date_str, source=f"{src}_wl")
            wl_data[src] = data

        if wl_data.get("xueqiu") and wl_data.get("guba"):
            _fill_watchlist_prices(wl_data["xueqiu"], wl_data["guba"], date_str)

        if wl_data:
            report_paths["watchlist"] = generate_watchlist_report(
                wl_data.get("guba") or {"watchlist_metrics": []},
                wl_data.get("xueqiu") or {"watchlist_metrics": []},
            )
            print(f"  ✓ 自选股报告: {report_paths['watchlist']}")

    print(f"\n{'=' * 60}")
    print(f"✅ 报告生成完成！")
    for src, path in report_paths.items():
        src_name = {"guba": "股吧", "xueqiu": "雪球", "watchlist": "自选股"}.get(src, src)
        print(f"📄 {src_name}报告: {path}")
    print(f"{'=' * 60}")

    return report_paths


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description="散户情绪分析系统")
    parser.add_argument("-n", "--count", type=int, default=None,
                        help=f"监控股票数量 (默认: {HOT_STOCKS_COUNT})")
    parser.add_argument("--guba-pages", type=int, default=None,
                        help=f"股吧抓取页数 (默认: {GUBA_PAGE_COUNT})")
    parser.add_argument("--xueqiu-count", type=int, default=None,
                        help=f"雪球每只股票目标条数上限 (默认: {XUEQIU_POST_COUNT}；服务端单页上限 20)")
    parser.add_argument("--skip-guba", action="store_true",
                        help="跳过股吧采集")
    parser.add_argument("--skip-xueqiu", action="store_true",
                        help="跳过雪球采集")
    parser.add_argument("--collect-only", action="store_true",
                        help="仅采集数据，不生成报告")
    parser.add_argument("--report-only", action="store_true",
                        help="仅从已保存数据生成报告（不抓取）")
    parser.add_argument("--watchlist-only", action="store_true",
                        help="只跑自选股链路（不采热股榜、不采大盘热度）")
    parser.add_argument("--market-only", action="store_true",
                        help="只跑市场链路（不采自选股）")
    parser.add_argument("--date", type=str, default=None, metavar="YYYYMMDD",
                        help="目标日期。采集时表示按该日期补采（只有自选股可回溯，"
                             "市场数据是实时接口拿不到历史）；--report-only 时表示读哪天的数据")

    args = parser.parse_args()

    if args.watchlist_only and args.market_only:
        parser.error("--watchlist-only 与 --market-only 不能同时使用")

    if args.date is not None:
        if not (len(args.date) == 8 and args.date.isdigit()):
            parser.error(f"--date 需要 8 位数字 YYYYMMDD，收到 {args.date!r}")

    if args.report_only:
        only = "market" if args.market_only else "watchlist" if args.watchlist_only else None
        generate_reports_only(
            date_str=args.date,
            skip_guba=args.skip_guba,
            skip_xueqiu=args.skip_xueqiu,
            only=only,
        )
        return

    run_watchlist = not args.market_only
    run_market = not args.watchlist_only

    if args.date is not None:
        # 热股榜、股吧人气排名、大盘 UV 指数全是实时接口，错过了就补不回来；
        # 只有自选股能按日期回溯。所以带 --date 时市场链路一律不跑。
        if args.market_only:
            parser.error("市场数据依赖实时接口，无法按日期补采，"
                         "--date 不能与 --market-only 一起使用")
        if run_market:
            print("ℹ️  --date 指定的是补采目标日：市场数据不可回溯，"
                  "本次按「仅自选股」处理（市场报告请用 --report-only 从已有数据出）")
            run_market = False

        from datetime import date as _date
        try:
            target = datetime.strptime(args.date, "%Y%m%d").date()
        except ValueError:
            parser.error(f"--date 不是合法日期: {args.date}")
        gap = (_date.today() - target).days
        if gap > MAX_BACKFILL_DAYS:
            print(f"⚠️  目标日距今 {gap} 天，超过 {MAX_BACKFILL_DAYS} 天："
                  f"列表可能翻不到该日期，建议加大 --guba-pages")

    run_sentiment_analysis(
        stock_count=args.count,
        guba_pages=args.guba_pages,
        xueqiu_count=args.xueqiu_count,
        skip_guba=args.skip_guba,
        skip_xueqiu=args.skip_xueqiu,
        collect_only=args.collect_only,
        date_str=args.date,
        run_market=run_market,
        run_watchlist=run_watchlist,
    )


if __name__ == "__main__":
    main()
