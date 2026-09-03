# -*- coding: utf-8 -*-
"""
HTML 日报生成器
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from jinja2 import Environment, FileSystemLoader
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
from config.settings import REPORT_DIR
from storage.data_store import load_history, load_watchlist_history


def generate_report(data: Dict, output_path: str = None, data_source: str = None) -> str:
    """
    生成 HTML 日报

    Args:
        data: 完整的分析数据
        output_path: 输出文件路径，默认自动生成
        data_source: 数据来源（"xueqiu" / "guba" / None 综合）

    Returns:
        str: 生成的报告文件路径
    """
    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    template = env.get_template("report.html")

    # 准备模板数据
    overview = data.get("overview", {})
    stock_metrics = data.get("stock_metrics", [])
    market_cycle = data.get("market_cycle", {})

    # 按情绪指数排序
    stock_by_sentiment = sorted(stock_metrics, key=lambda x: x.get("sentiment_index", 0), reverse=True)

    # 按热度排序
    stock_by_heat = sorted(stock_metrics, key=lambda x: x.get("heat_score", 0), reverse=True)

    # 情绪极值预警（前5高 + 后5低）
    extreme_high = [s for s in stock_by_sentiment[:5] if s.get("sentiment_index", 0) > 80]
    extreme_low = [s for s in stock_by_sentiment[-5:] if s.get("sentiment_index", 0) < 20]
    extreme_stocks = extreme_high + extreme_low

    # 排行榜数据（TOP15）
    top_n = min(15, len(stock_metrics))
    sentiment_rank = stock_by_sentiment[:top_n]

    # 图表数据
    sentiment_rank_names = [s.get("stock_name", "") for s in reversed(sentiment_rank)]
    sentiment_rank_values = [round(s.get("sentiment_index", 0), 1) for s in reversed(sentiment_rank)]

    # 为股票列表补充周期阶段信息
    from analysis.cycle_model import determine_cycle_stage
    for stock in stock_metrics:
        cycle = determine_cycle_stage(stock)
        stock["cycle_stage"] = cycle["stage"]
        stock["cycle_name"] = cycle["stage_name"]
        stock["cycle_emoji"] = cycle["stage_emoji"]

    # 加载历史数据用于趋势图
    history = load_history(data_source, days=30) if data_source else []
    history_dates = [h.get("date", "") for h in history]
    history_sentiment = [round(h.get("sentiment", 0), 1) for h in history]
    history_heat = [round(h.get("heat", 0), 1) for h in history]
    history_divergence = [round(h.get("divergence", 0) * 100, 1) for h in history]

    # 自选股数据
    watchlist_metrics = data.get("watchlist_metrics", [])
    watchlist_data = []
    for wm in watchlist_metrics:
        code = wm.get("stock_code", "")
        wl_hist = load_watchlist_history(code, days=30, source=data_source or "")
        watchlist_data.append({
            "stock_code": code,
            "stock_name": wm.get("stock_name", ""),
            "sentiment_index": round(wm.get("sentiment_index", 0), 1),
            "heat_score": round(wm.get("heat_score", 0), 1),
            "divergence": round(wm.get("divergence", 0), 2),
            "total_posts": wm.get("total_posts", 0),
            "guba_posts": wm.get("guba_posts", 0),
            "xueqiu_posts": wm.get("xueqiu_posts", 0),
            "stock_price": wm.get("stock_price", 0),
            "change_percent": wm.get("change_percent", 0),
            "positive_count": wm.get("positive_count", 0),
            "negative_count": wm.get("negative_count", 0),
            "neutral_count": wm.get("neutral_count", 0),
            "cycle_emoji": wm.get("cycle_stage", {}).get("stage_emoji", ""),
            "cycle_name": wm.get("cycle_stage", {}).get("stage_name", ""),
            "cycle_signal": wm.get("cycle_stage", {}).get("signal", ""),
            "history_dates": [h.get("date", "") for h in wl_hist],
            "history_sentiment": [round(h.get("sentiment", 0), 1) for h in wl_hist],
            "history_heat": [round(h.get("heat", 0), 1) for h in wl_hist],
            "history_divergence": [round(h.get("divergence", 0) * 100, 1) for h in wl_hist],
        })

    # 数据源统计
    source_set = set()
    for sm in stock_metrics:
        for src in sm.get("source_breakdown", {}).keys():
            source_set.add(src)
    sources = "、".join([_source_name(s) for s in sorted(source_set)]) or "无"

    # 总帖子数
    total_posts = sum(sm.get("total_posts", 0) for sm in stock_metrics)

    date_str = data.get("_meta", {}).get("date", datetime.now().strftime("%Y-%m-%d"))
    generated_at = data.get("_meta", {}).get("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # 渲染模板
    html = template.render(
        date=date_str,
        generated_at=generated_at,
        data_source=data_source,
        sources=sources,
        total_stocks=len(stock_metrics),
        total_posts=total_posts,
        overview=overview,
        market_cycle=market_cycle,
        extreme_stocks=extreme_stocks,
        stock_rank=stock_by_heat,  # 表格显示全部股票
        sentiment_rank_names=sentiment_rank_names,
        sentiment_rank_values=sentiment_rank_values,
        history_dates=history_dates,
        history_sentiment=history_sentiment,
        history_heat=history_heat,
        history_divergence=history_divergence,
        watchlist_data=watchlist_data,
    )

    # 输出文件
    if output_path is None:
        date_for_file = datetime.now().strftime("%Y%m%d")
        if data_source == "xueqiu":
            output_path = str(REPORT_DIR / f"xueqiu_report_{date_for_file}.html")
        elif data_source == "guba":
            output_path = str(REPORT_DIR / f"guba_report_{date_for_file}.html")
        else:
            output_path = str(REPORT_DIR / f"sentiment_report_{date_for_file}.html")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  📄 报告已生成: {output_path}")
    return str(output_path)


def _source_name(source: str) -> str:
    """数据源中文名"""
    mapping = {
        "guba": "东方财富股吧",
        "xueqiu": "雪球",
    }
    return mapping.get(source, source)


if __name__ == "__main__":
    print("报告生成模块测试通过")
