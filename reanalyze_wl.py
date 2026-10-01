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


def _load_watchlist() -> list:
    """加载自选股配置"""
    with open(BASE_DIR / "config" / "watchlist.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _rebuild_guba_wl(posts: list, watchlist: list, is_backfill: bool) -> list:
    """重建股吧自选股指标"""
    from collectors.guba_crawler import get_stock_rank

    metrics_list = []
    for stock in watchlist:
        code = stock["code"]
        stock_posts = [p for p in posts if p.get("stock_code") == code]
        if not stock_posts:
            continue
        guba_rank = 0
        if not is_backfill:
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
        if is_backfill:
            # 人气排名是实时接口，历史日期拿不到，热度不存在——显式置位，
            # 否则 calculate_stock_metrics 会静默回落到雪球公式产出假热度
            metrics["heat_score"] = 0.0
            metrics["heat_unavailable"] = True
            metrics["backfill"] = True
        metrics_list.append(metrics)
        print(f"  股吧 {stock['name']}: {metrics['total_posts']}帖, "
              f"情绪{metrics['sentiment_index']:.1f}, "
              f"多{metrics['positive_count']}/空{metrics['negative_count']}/中{metrics['neutral_count']}")
    return metrics_list


def _rebuild_xueqiu_wl(posts: list, watchlist: list, is_backfill: bool) -> list:
    """重建雪球自选股指标"""
    metrics_list = []
    for stock in watchlist:
        code = stock["code"]
        xq_code = ("SH" + code) if code.startswith("6") else ("SZ" + code)
        stock_posts = [p for p in posts
                       if p.get("stock_code") in (xq_code, code)]
        if not stock_posts:
            continue
        metrics = calculate_stock_metrics(
            stock_posts, code, stock["name"],
            xueqiu_heat=0, guba_rank=0,
            weighted=True,
            follow_count=stock_posts[0].get("stock_followers", 0),
        )
        metrics["cycle_stage"] = determine_cycle_stage(metrics, history=None)
        metrics["guba_posts"] = 0
        metrics["xueqiu_posts"] = len(stock_posts)
        metrics["stock_price"] = stock_posts[0].get("stock_price", 0)
        metrics["change_percent"] = stock_posts[0].get("change_percent", 0)
        if is_backfill:
            metrics["backfill"] = True
        metrics_list.append(metrics)
        print(f"  雪球 {stock['name']}: {metrics['total_posts']}帖, "
              f"情绪{metrics['sentiment_index']:.1f}, "
              f"多{metrics['positive_count']}/空{metrics['negative_count']}/中{metrics['neutral_count']}")
    return metrics_list


def rebuild_aggregated_data(date_str: str, reanalyzed_posts: dict,
                            is_backfill: bool = False) -> dict:
    """
    根据重新分析的帖子重建**自选股**聚合数据

    读写都走 {source}_wl_data_{date}.json：市场数据与自选股已经拆成两套文件，
    这里不再碰市场键（overview / stock_metrics / market_cycle），也不再写 history.json
    ——自选股重跑没有市场数据，写进去只会污染趋势曲线。

    Returns:
        dict: {"guba": {...}} / {"xueqiu": {...}}，未重建的来源不出现
    """
    from storage.data_store import (
        load_watchlist_data, save_daily_data, save_watchlist_history,
    )

    watchlist = _load_watchlist()
    out = {}

    for src, key, rebuild in (
        ("guba", "guba_wl", _rebuild_guba_wl),
        ("xueqiu", "xueqiu_wl", _rebuild_xueqiu_wl),
    ):
        if key not in reanalyzed_posts:
            continue

        # 保留原有的 _meta（generated_at 等），只替换指标
        data = load_watchlist_data(date_str, source=f"{src}_wl") or {}
        wl_metrics = rebuild(reanalyzed_posts[key], watchlist, is_backfill)
        if not wl_metrics:
            print(f"  ⚠ {src} 自选股重建后为空，跳过落盘")
            continue

        data["watchlist_metrics"] = wl_metrics
        data["backfill"] = is_backfill
        save_daily_data(data, date_str=date_str, source=f"{src}_wl")

        # 趋势图的数据源：以前这里漏了，导致重跑后历史曲线不更新
        for m in wl_metrics:
            save_watchlist_history(
                m["stock_code"], date_str, m["sentiment_index"],
                m["heat_score"], m["divergence"],
                m["cycle_stage"]["stage_name"],
                m["total_posts"], source=src)

        out[src] = data

    return out


if __name__ == "__main__":
    from datetime import datetime
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    print(f"\n{'='*60}")
    print(f"🔄 重新分析自选股帖子 (日期: {date_str})")
    print(f"{'='*60}")

    reanalyzed = reanalyze(date_str)

    if reanalyzed:
        print(f"\n📐 重建自选股聚合数据...")
        wl_data = rebuild_aggregated_data(
            date_str, reanalyzed,
            is_backfill=(date_str != datetime.now().strftime("%Y%m%d")),
        )

        if wl_data:
            print(f"\n📄 重新生成自选股报告...")
            from report.report_generator import generate_watchlist_report
            generate_watchlist_report(
                wl_data.get("guba") or {"watchlist_metrics": []},
                wl_data.get("xueqiu") or {"watchlist_metrics": []},
            )

        print(f"\n✅ 完成！")
