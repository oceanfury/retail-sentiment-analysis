# -*- coding: utf-8 -*-
"""
重新用LLM分析已有的自选股帖子数据（修复旧词典法结果）
用法: python reanalyze_wl.py [YYYYMMDD]
"""
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from analysis.llm_sentiment import analyze_posts_with_llm
from analysis.metrics import calculate_stock_metrics
from analysis.cycle_model import determine_cycle_stage
from config.settings import RAW_DATA_DIR


def reanalyze(date_str: str):
    """重新分析指定日期的自选股帖子"""
    sources = ["guba_wl", "xueqiu_wl"]
    results = {}

    for source in sources:
        filepath = RAW_DATA_DIR / f"{source}_raw_posts_{date_str}.json"
        if not filepath.exists():
            print(f"  跳过 {source}: 文件不存在")
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            posts = json.load(f)

        if not posts:
            print(f"  跳过 {source}: 无帖子")
            continue

        # 统计旧分析方式
        dict_count = sum(1 for p in posts if p.get("positive_words") or p.get("negative_words"))
        llm_count = len(posts) - dict_count
        print(f"\n  {source}: {len(posts)}帖 (词典法{dict_count}, LLM{llm_count})")

        # 用LLM重新分析全部帖子
        print(f"  🤖 重新用LLM分析中...")
        reanalyzed = analyze_posts_with_llm(posts)
        print(f"  ✓ 完成")

        # 保存回文件
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(reanalyzed, f, ensure_ascii=False, indent=2)
        print(f"  💾 已保存: {filepath.name}")

        # 统计新结果
        pos = sum(1 for p in reanalyzed if p.get("sentiment") == "positive")
        neg = sum(1 for p in reanalyzed if p.get("sentiment") == "negative")
        neu = sum(1 for p in reanalyzed if p.get("sentiment") == "neutral")
        print(f"  新结果: 多{pos}/空{neg}/中{neu}")

        results[source] = reanalyzed

    return results


def rebuild_aggregated_data(date_str: str, reanalyzed_posts: dict):
    """根据重新分析的帖子重建聚合数据"""
    from collectors.guba_crawler import get_stock_rank
    from storage.data_store import load_daily_data, save_daily_data, save_history_snapshot

    # 加载现有的聚合数据
    guba_data = load_daily_data(date_str, source="guba")
    xq_data = load_daily_data(date_str, source="xueqiu")

    # 加载自选股配置
    watchlist_path = BASE_DIR / "config" / "watchlist.json"
    with open(watchlist_path, "r", encoding="utf-8") as f:
        watchlist = json.load(f)

    # 重建股吧自选股指标
    if "guba_wl" in reanalyzed_posts and guba_data:
        posts = reanalyzed_posts["guba_wl"]
        wl_metrics = []
        for stock in watchlist:
            code = stock["code"]
            stock_posts = [p for p in posts if p.get("stock_code") == code]
            if not stock_posts:
                continue
            guba_rank = 0
            try:
                guba_rank = get_stock_rank(stock.get("symbol", ""))
            except Exception:
                pass
            metrics = calculate_stock_metrics(
                stock_posts, code, stock["name"],
                xueqiu_heat=0, guba_rank=guba_rank,
                weighted=False,
            )
            metrics["cycle_stage"] = determine_cycle_stage(metrics, history=None)
            metrics["guba_posts"] = len(stock_posts)
            metrics["xueqiu_posts"] = 0
            metrics["stock_price"] = stock_posts[0].get("stock_price", 0)
            metrics["change_percent"] = stock_posts[0].get("change_percent", 0)
            wl_metrics.append(metrics)
            print(f"  股吧 {stock['name']}: {metrics['total_posts']}帖, "
                  f"情绪{metrics['sentiment_index']:.1f}, "
                  f"多{metrics['positive_count']}/空{metrics['negative_count']}/中{metrics['neutral_count']}")

        guba_data["watchlist_metrics"] = wl_metrics
        save_daily_data(guba_data, source="guba")

    # 重建雪球自选股指标
    if "xueqiu_wl" in reanalyzed_posts and xq_data:
        posts = reanalyzed_posts["xueqiu_wl"]
        wl_metrics = []
        for stock in watchlist:
            code = stock["code"]
            xq_code = ("SH" + code) if code.startswith("6") else ("SZ" + code)
            stock_posts = [p for p in posts if p.get("stock_code") == xq_code or p.get("stock_code") == code]
            if not stock_posts:
                continue
            follow_count = stock_posts[0].get("stock_followers", 0)
            stock_price = stock_posts[0].get("stock_price", 0)
            change_percent = stock_posts[0].get("change_percent", 0)
            metrics = calculate_stock_metrics(
                stock_posts, code, stock["name"],
                xueqiu_heat=0, guba_rank=0,
                weighted=True, follow_count=follow_count,
            )
            metrics["cycle_stage"] = determine_cycle_stage(metrics, history=None)
            metrics["guba_posts"] = 0
            metrics["xueqiu_posts"] = len(stock_posts)
            metrics["stock_price"] = stock_price
            metrics["change_percent"] = change_percent
            wl_metrics.append(metrics)
            print(f"  雪球 {stock['name']}: {metrics['total_posts']}帖, "
                  f"情绪{metrics['sentiment_index']:.1f}, "
                  f"多{metrics['positive_count']}/空{metrics['negative_count']}/中{metrics['neutral_count']}")

        xq_data["watchlist_metrics"] = wl_metrics
        save_daily_data(xq_data, source="xueqiu")

    return guba_data, xq_data


if __name__ == "__main__":
    from datetime import datetime
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    print(f"\n{'='*60}")
    print(f"🔄 重新分析自选股帖子 (日期: {date_str})")
    print(f"{'='*60}")

    reanalyzed = reanalyze(date_str)

    if reanalyzed:
        print(f"\n📐 重建聚合数据...")
        guba_data, xq_data = rebuild_aggregated_data(date_str, reanalyzed)

        print(f"\n📄 重新生成报告...")
        from report.report_generator import generate_report, generate_watchlist_compare_report

        if guba_data:
            generate_report(guba_data, data_source="guba")
        if xq_data:
            generate_report(xq_data, data_source="xueqiu")
        if guba_data and xq_data:
            generate_watchlist_compare_report(guba_data, xq_data)

        print(f"\n✅ 完成！")
