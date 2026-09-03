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

from config.settings import HOT_STOCKS_COUNT, GUBA_PAGE_COUNT, XUEQIU_POST_COUNT
from collectors.hot_stocks import get_hot_stocks_from_gainers
from collectors.guba_crawler import fetch_guba_posts, close_browser as close_guba_browser, get_stock_rank
from collectors.xueqiu_crawler import fetch_xueqiu_posts, get_hot_stocks_from_xueqiu, close_browser as close_xq_browser
from analysis.sentiment import get_sentiment_analyzer
from analysis.metrics import (
    calculate_stock_metrics,
    calculate_market_overview,
)
from analysis.cycle_model import determine_cycle_stage, determine_market_cycle
from storage.data_store import (
    save_daily_data, save_raw_posts_csv, save_history_snapshot, load_history,
    save_watchlist_history, load_watchlist_history,
    save_raw_posts_json, load_raw_posts_json,
)
from report.report_generator import generate_report

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
                    source: str) -> Dict:
    """
    对单一来源的帖子进行情绪分析、指标计算和周期判断

    Args:
        posts: 该来源的帖子列表（已带情绪分析结果）
        hot_stocks: 热门股票列表
        source: 来源标识（"xueqiu" / "guba"）

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

    # 计算市场概览
    overview = calculate_market_overview(stock_metrics, posts)

    # 计算市场周期
    market_cycle = determine_market_cycle(overview, [], stock_metrics)

    return {
        "overview": overview,
        "stock_metrics": stock_metrics,
        "market_cycle": market_cycle,
        "all_posts_count": len(posts),
        "hot_stocks_count": len(hot_stocks),
    }


def run_sentiment_analysis(stock_count: int = None, guba_pages: int = None,
                           xueqiu_count: int = None, skip_guba: bool = False,
                           skip_xueqiu: bool = False,
                           collect_only: bool = False) -> Dict:
    """
    运行完整的情绪分析流程，分别生成雪球日报和股吧日报

    Args:
        stock_count: 监控股票数量，默认使用配置
        guba_pages: 股吧抓取页数
        xueqiu_count: 雪球抓取数量
        skip_guba: 跳过股吧采集
        skip_xueqiu: 跳过雪球采集

    Returns:
        dict: { "xueqiu": {...}, "guba": {...} } 各来源的分析结果
    """
    if stock_count is None:
        stock_count = HOT_STOCKS_COUNT

    start_time = time.time()
    print("=" * 60)
    print("📊 散户情绪分析系统")
    print(f"🕐 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

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
        print("  [ERROR] 未能获取任何热门股票列表，程序退出")
        return {}

    # ========== 2. 采集帖子数据 ==========
    print(f"\n🕸️  [2/5] 采集社交媒体帖子...")

    guba_posts_list = []
    xueqiu_posts_list = []
    source_stats = defaultdict(int)

    # 自选股
    watchlist = _load_watchlist()
    watchlist_guba_posts = []
    watchlist_xq_posts = []
    watchlist_xq_stock_info = {}  # 雪球自选股基本信息（即使没有帖子也有）

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

        # 自选股 - 股吧
        if watchlist:
            print(f"\n  --- 自选股 - 股吧 ---")
            for stock in watchlist:
                print(f"  {stock['name']}({stock['code']}) - 股吧...")
                try:
                    posts = fetch_guba_posts(stock["symbol"], stock["name"], page_count=guba_pages)
                    for p in posts:
                        p["stock_code"] = stock["code"]
                    watchlist_guba_posts.extend(posts)
                except Exception as e:
                    print(f"      [ERROR] 自选股吧采集失败: {e}")
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
                posts = fetch_xueqiu_posts(code, name, count=xueqiu_count)
                xueqiu_posts_list.extend(posts)
                source_stats["xueqiu"] += len(posts)
            except Exception as e:
                print(f"      [ERROR] 雪球采集失败: {e}")

        # 自选股 - 雪球
        if watchlist:
            print(f"\n  --- 自选股 - 雪球 ---")
            for stock in watchlist:
                xq_code = _to_xq_symbol(stock["code"])
                print(f"  {stock['name']}({stock['code']}) - 雪球...")
                try:
                    posts, stock_info = fetch_xueqiu_posts(xq_code, stock["name"], count=xueqiu_count,
                                                             return_stock_info=True)
                    watchlist_xq_posts.extend(posts)
                    watchlist_xq_stock_info[stock["code"]] = stock_info
                except Exception as e:
                    print(f"      [ERROR] 自选雪球采集失败: {e}")
                    watchlist_xq_stock_info[stock["code"]] = {
                        "stock_code": xq_code, "stock_name": stock["name"],
                        "stock_followers": 0, "stock_price": 0, "change_percent": 0
                    }
        try:
            close_xq_browser()
        except:
            pass

    all_posts = guba_posts_list + xueqiu_posts_list
    print(f"\n  ✓ 采集完成，共 {len(all_posts)} 条帖子")
    for src, cnt in source_stats.items():
        src_name = {"guba": "东方财富股吧", "xueqiu": "雪球"}.get(src, src)
        print(f"    - {src_name}: {cnt} 条")

    if not all_posts:
        print("  [ERROR] 没有采集到任何帖子数据")
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
    watchlist_guba_sent = analyze_posts(watchlist_guba_posts) if watchlist_guba_posts else []
    watchlist_xq_sent = analyze_posts(watchlist_xq_posts) if watchlist_xq_posts else []

    # 增量合并：当天多次采集时，与已有帖子去重合并
    date_str = datetime.now().strftime("%Y%m%d")
    if not skip_guba:
        existing_guba = load_raw_posts_json(date_str, source="guba")
        if existing_guba:
            before = len(guba_with_sent)
            guba_with_sent = _merge_posts(guba_with_sent, existing_guba)
            print(f"  股吧增量合并: {before} + {len(existing_guba)} → {len(guba_with_sent)} 条（去重）")
        existing_guba_wl = load_raw_posts_json(date_str, source="guba_wl")
        if existing_guba_wl and watchlist_guba_sent:
            before_wl = len(watchlist_guba_sent)
            watchlist_guba_sent = _merge_posts(watchlist_guba_sent, existing_guba_wl)
            print(f"  股吧自选股增量合并: {before_wl} + {len(existing_guba_wl)} → {len(watchlist_guba_sent)} 条（去重）")
    if not skip_xueqiu:
        existing_xq = load_raw_posts_json(date_str, source="xueqiu")
        if existing_xq:
            before = len(xueqiu_with_sent)
            xueqiu_with_sent = _merge_posts(xueqiu_with_sent, existing_xq)
            print(f"  雪球增量合并: {before} + {len(existing_xq)} → {len(xueqiu_with_sent)} 条（去重）")
        existing_xq_wl = load_raw_posts_json(date_str, source="xueqiu_wl")
        if existing_xq_wl and watchlist_xq_sent:
            before_wl = len(watchlist_xq_sent)
            watchlist_xq_sent = _merge_posts(watchlist_xq_sent, existing_xq_wl)
            print(f"  雪球自选股增量合并: {before_wl} + {len(existing_xq_wl)} → {len(watchlist_xq_sent)} 条（去重）")

    total_posts = len(guba_with_sent) + len(xueqiu_with_sent)
    all_pos = sum(1 for p in guba_with_sent + xueqiu_with_sent if p["sentiment"] == "positive")
    all_neg = sum(1 for p in guba_with_sent + xueqiu_with_sent if p["sentiment"] == "negative")
    all_neu = sum(1 for p in guba_with_sent + xueqiu_with_sent if p["sentiment"] == "neutral")

    print(f"  ✓ 分析完成（共 {total_posts} 条）")
    print(f"    看多: {all_pos} ({all_pos/total_posts*100:.1f}%)")
    print(f"    看空: {all_neg} ({all_neg/total_posts*100:.1f}%)")
    print(f"    中性: {all_neu} ({all_neu/total_posts*100:.1f}%)")

    # ========== 自选股分析（按平台独立）==========
    date_str = datetime.now().strftime("%Y%m%d")
    watchlist_guba_metrics = []
    watchlist_xq_metrics = []

    if watchlist:
        print(f"\n📐 自选股情绪分析...")

        # 股吧自选股分析
        if watchlist_guba_sent:
            print(f"  --- 股吧自选股 ---")
            for stock in watchlist:
                code = stock["code"]
                posts = [p for p in watchlist_guba_sent if p.get("stock_code") == code]
                if not posts:
                    continue
                guba_rank = 0
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

        # 雪球自选股分析
        if watchlist:
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
                watchlist_xq_metrics.append(metrics)
                save_watchlist_history(
                    code, date_str, metrics["sentiment_index"],
                    metrics["heat_score"], metrics["divergence"],
                    metrics["cycle_stage"]["stage_name"],
                    metrics["total_posts"], source="xueqiu")
                print(f"  {stock['name']}({code}): {metrics['total_posts']}帖, "
                      f"情绪{metrics['sentiment_index']:.1f}, 热度{metrics['heat_score']:.1f}, "
                      f"{metrics['cycle_stage']['stage_emoji']} {metrics['cycle_stage']['stage_name']}")

    # ========== 4. 计算指标（按来源分开）==========
    print(f"\n📐 [4/5] 计算情绪指标...")

    results = {}

    if guba_with_sent:
        results["guba"] = _analyze_source(guba_with_sent, guba_hot_stocks, "guba")
        print(f"  ✓ 股吧: {len(results['guba']['stock_metrics'])} 只股票，"
              f"情绪 {results['guba']['overview']['overall_sentiment']:.1f}，"
              f"{results['guba']['market_cycle']['stage_emoji']} {results['guba']['market_cycle']['stage_name']}")

    if xueqiu_with_sent:
        results["xueqiu"] = _analyze_source(xueqiu_with_sent, xueqiu_hot_stocks, "xueqiu")
        print(f"  ✓ 雪球: {len(results['xueqiu']['stock_metrics'])} 只股票，"
              f"情绪 {results['xueqiu']['overview']['overall_sentiment']:.1f}，"
              f"{results['xueqiu']['market_cycle']['stage_emoji']} {results['xueqiu']['market_cycle']['stage_name']}")

    # ========== 5. 保存数据 & 生成报告（按来源分开）==========
    if collect_only:
        print(f"\n💾 [5/5] 保存数据（跳过报告生成）...")
    else:
        print(f"\n📄 [5/5] 保存数据并生成报告...")

    report_paths = {}

    if "guba" in results:
        results["guba"]["watchlist_metrics"] = watchlist_guba_metrics
        save_daily_data(results["guba"], source="guba")
        save_raw_posts_json(guba_with_sent, source="guba")
        if watchlist_guba_sent:
            save_raw_posts_json(watchlist_guba_sent, source="guba_wl")
        try:
            save_raw_posts_csv(guba_with_sent, source="guba")
        except PermissionError:
            print(f"  ⚠ CSV 写入被拒，跳过（不影响 JSON 数据）")
        save_history_snapshot(results["guba"], source="guba")
        if not collect_only:
            report_paths["guba"] = generate_report(results["guba"], data_source="guba")

    if "xueqiu" in results:
        results["xueqiu"]["watchlist_metrics"] = watchlist_xq_metrics
        save_daily_data(results["xueqiu"], source="xueqiu")
        save_raw_posts_json(xueqiu_with_sent, source="xueqiu")
        if watchlist_xq_sent:
            save_raw_posts_json(watchlist_xq_sent, source="xueqiu_wl")
        try:
            save_raw_posts_csv(xueqiu_with_sent, source="xueqiu")
        except PermissionError:
            print(f"  ⚠ CSV 写入被拒，跳过（不影响 JSON 数据）")
        save_history_snapshot(results["xueqiu"], source="xueqiu")
        if not collect_only:
            report_paths["xueqiu"] = generate_report(results["xueqiu"], data_source="xueqiu")

    # 耗时
    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"✅ 分析完成！总耗时: {elapsed:.1f} 秒")
    for src, path in report_paths.items():
        src_name = {"guba": "股吧", "xueqiu": "雪球"}.get(src, src)
        print(f"📄 {src_name}报告: {path}")
    print(f"{'=' * 60}")

    return results


def generate_reports_only(date_str: str = None, skip_guba: bool = False, skip_xueqiu: bool = False):
    """仅从已保存的数据生成报告（不重新抓取）"""
    from storage.data_store import load_daily_data
    from report.report_generator import generate_report
    from analysis.metrics import calculate_stock_metrics
    from analysis.cycle_model import determine_cycle_stage

    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    print(f"\n📄 从已保存数据生成报告 (日期: {date_str})")
    print(f"{'=' * 60}")

    watchlist = _load_watchlist()
    report_paths = {}

    def _ensure_watchlist(data, source):
        """确保数据中包含完整的自选股列表（即使没有帖子）"""
        if not watchlist:
            return
        wl_metrics = data.get("watchlist_metrics", []) or []
        existing_codes = {m.get("stock_code") for m in wl_metrics}
        for stock in watchlist:
            code = stock["code"]
            if code in existing_codes:
                continue
            # 生成空记录
            metrics = calculate_stock_metrics(
                [], code, stock["name"],
                xueqiu_heat=0, guba_rank=0,
                weighted=(source == "xueqiu"),
                follow_count=0,
            )
            metrics["cycle_stage"] = determine_cycle_stage(metrics, history=None)
            metrics["guba_posts"] = 0 if source == "xueqiu" else 0
            metrics["xueqiu_posts"] = 0 if source == "guba" else 0
            metrics["stock_price"] = 0
            metrics["change_percent"] = 0
            wl_metrics.append(metrics)
        data["watchlist_metrics"] = wl_metrics

    if not skip_guba:
        guba_data = load_daily_data(date_str, source="guba")
        if guba_data:
            _ensure_watchlist(guba_data, "guba")
            save_history_snapshot(guba_data, source="guba")
            report_paths["guba"] = generate_report(guba_data, data_source="guba")
            print(f"  ✓ 股吧报告: {report_paths['guba']}")
        else:
            print(f"  ✗ 未找到股吧数据: guba_data_{date_str}.json")

    if not skip_xueqiu:
        xq_data = load_daily_data(date_str, source="xueqiu")
        if xq_data:
            _ensure_watchlist(xq_data, "xueqiu")
            save_history_snapshot(xq_data, source="xueqiu")
            report_paths["xueqiu"] = generate_report(xq_data, data_source="xueqiu")
            print(f"  ✓ 雪球报告: {report_paths['xueqiu']}")
        else:
            print(f"  ✗ 未找到雪球数据: xueqiu_data_{date_str}.json")

    print(f"\n{'=' * 60}")
    print(f"✅ 报告生成完成！")
    for src, path in report_paths.items():
        src_name = {"guba": "股吧", "xueqiu": "雪球"}.get(src, src)
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
                        help=f"雪球抓取数量 (默认: {XUEQIU_POST_COUNT})")
    parser.add_argument("--skip-guba", action="store_true",
                        help="跳过股吧采集")
    parser.add_argument("--skip-xueqiu", action="store_true",
                        help="跳过雪球采集")
    parser.add_argument("--collect-only", action="store_true",
                        help="仅采集数据，不生成报告")
    parser.add_argument("--report-only", action="store_true",
                        help="仅从已保存数据生成报告（不抓取）")
    parser.add_argument("--date", type=str, default=None,
                        help="指定日期 (YYYYMMDD)，用于 --report-only")

    args = parser.parse_args()

    if args.report_only:
        generate_reports_only(
            date_str=args.date,
            skip_guba=args.skip_guba,
            skip_xueqiu=args.skip_xueqiu,
        )
    else:
        run_sentiment_analysis(
            stock_count=args.count,
            guba_pages=args.guba_pages,
            xueqiu_count=args.xueqiu_count,
            skip_guba=args.skip_guba,
            skip_xueqiu=args.skip_xueqiu,
            collect_only=args.collect_only,
        )


if __name__ == "__main__":
    main()
