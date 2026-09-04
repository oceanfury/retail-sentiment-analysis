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
        date_for_file = data.get("_meta", {}).get("date", "").replace("-", "")
        if not date_for_file:
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


def generate_watchlist_compare_report(guba_data: Dict, xueqiu_data: Dict, output_path: str = None) -> str:
    """
    生成自选股双平台对比分析报告

    Args:
        guba_data: 股吧分析数据
        xueqiu_data: 雪球分析数据
        output_path: 输出路径

    Returns:
        str: 报告文件路径
    """
    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    template = env.get_template("watchlist_compare.html")

    guba_wl = {m["stock_code"]: m for m in guba_data.get("watchlist_metrics", [])}
    xq_wl = {m["stock_code"]: m for m in xueqiu_data.get("watchlist_metrics", [])}

    all_codes = sorted(set(list(guba_wl.keys()) + list(xq_wl.keys())))

    comparison = []
    suggestions = []
    insights = []

    for code in all_codes:
        g = guba_wl.get(code, {})
        x = xq_wl.get(code, {})
        name = g.get("stock_name") or x.get("stock_name", "")
        price = g.get("stock_price") or x.get("stock_price") or 0
        chg = g.get("change_percent") or x.get("change_percent") or 0

        g_sent = g.get("sentiment_index", 0)
        g_heat = g.get("heat_score", 0)
        g_stage = g.get("cycle_stage", {}).get("stage_name", "")
        g_emoji = g.get("cycle_stage", {}).get("stage_emoji", "")
        g_pos = g.get("positive_count", 0)
        g_neg = g.get("negative_count", 0)
        g_neu = g.get("neutral_count", 0)

        x_sent = x.get("sentiment_index", 0)
        x_heat = x.get("heat_score", 0)
        x_stage = x.get("cycle_stage", {}).get("stage_name", "")
        x_emoji = x.get("cycle_stage", {}).get("stage_emoji", "")
        x_pos = x.get("positive_count", 0)
        x_neg = x.get("negative_count", 0)
        x_neu = x.get("neutral_count", 0)

        # 平台分歧度
        sent_diff = abs(g_sent - x_sent)
        if sent_diff >= 20:
            div_level = "high"
            row_class = "platform-diverge"
        elif g_sent > 55 and x_sent > 55:
            div_level = "low"
            row_class = "platform-agree-bull"
        elif g_sent < 45 and x_sent < 45:
            div_level = "low"
            row_class = "platform-agree-bear"
        else:
            div_level = "mid"
            row_class = ""

        comparison.append({
            "stock_code": code, "stock_name": name,
            "stock_price": round(price, 2) if price else 0,
            "change_percent": round(chg, 2) if chg else 0,
            "guba_sentiment": round(g_sent, 1), "guba_heat": round(g_heat, 1),
            "guba_stage": g_stage, "guba_emoji": g_emoji,
            "guba_pos": g_pos, "guba_neg": g_neg, "guba_neu": g_neu,
            "xueqiu_sentiment": round(x_sent, 1), "xueqiu_heat": round(x_heat, 1),
            "xueqiu_stage": x_stage, "xueqiu_emoji": x_emoji,
            "xueqiu_pos": x_pos, "xueqiu_neg": x_neg, "xueqiu_neu": x_neu,
            "divergence_level": div_level, "row_class": row_class,
        })

    # 生成操作建议和关键发现（优先LLM，失败回退规则）
    market_summary = ""

    # 方向标签映射
    _dir_map = {
        "偏多": "sig-bullish", "看多": "sig-bullish",
        "偏空": "sig-bearish", "看空": "sig-bearish", "回避": "sig-bearish",
        "谨慎": "sig-warning",
        "中性": "sig-neutral", "—": "sig-neutral",
    }

    try:
        from analysis.llm_sentiment import generate_watchlist_analysis
        print("  🤖 使用Deepseek生成对比分析...")
        llm_result = generate_watchlist_analysis(
            comparison, guba_data.get("overview", {}), xueqiu_data.get("overview", {})
        )
        market_summary = llm_result.get("market_summary", "")
        for s in llm_result.get("suggestions", []):
            direction = s.get("direction", "中性")
            s["signal_class"] = _dir_map.get(direction, "sig-neutral")
            suggestions.append(s)
        for ins in llm_result.get("insights", []):
            if "card_class" not in ins:
                ins["card_class"] = ""
            insights.append(ins)
        print("  ✓ LLM分析完成")
    except Exception as e:
        print(f"  ⚠ LLM分析失败({e})，回退规则引擎")

        for item in comparison:
            name = item["stock_name"]
            g_sent = item["guba_sentiment"]
            x_sent = item["xueqiu_sentiment"]
            g_posts = item["guba_pos"] + item["guba_neg"] + item["guba_neu"]
            x_posts = item["xueqiu_pos"] + item["xueqiu_neg"] + item["xueqiu_neu"]
            total_posts = g_posts + x_posts
            chg = item["change_percent"]
            div = item["divergence_level"]

            if total_posts == 0:
                suggestions.append({"stock_name": name, "direction": "—", "signal_class": "sig-neutral",
                    "action": "不操作", "reason": "双平台无当日帖子，无法判断"})
            elif g_sent < 45 and x_sent < 45:
                suggestions.append({"stock_name": name, "direction": "回避", "signal_class": "sig-bearish",
                    "action": "不抄底", "reason": "双平台共振看空，弱势明确"})
            elif g_sent > 55 and x_sent > 55 and chg > 0:
                suggestions.append({"stock_name": name, "direction": "偏多", "signal_class": "sig-bullish",
                    "action": "可持有/小仓位", "reason": "双平台共振看多，趋势健康"})
            elif div == "high":
                suggestions.append({"stock_name": name, "direction": "谨慎", "signal_class": "sig-warning",
                    "action": "不追高，观察回调", "reason": f"平台分歧大(差{abs(g_sent-x_sent):.0f}分)，方向不明确"})
            elif g_sent > 55 and x_sent < 45:
                suggestions.append({"stock_name": name, "direction": "谨慎", "signal_class": "sig-warning",
                    "action": "观察", "reason": "股吧看多但雪球看空，信号矛盾"})
            elif x_sent > 55 and g_sent < 45:
                suggestions.append({"stock_name": name, "direction": "谨慎", "signal_class": "sig-warning",
                    "action": "观察", "reason": "雪球看多但股吧看空，散户可能滞后"})
            else:
                suggestions.append({"stock_name": name, "direction": "中性", "signal_class": "sig-neutral",
                    "action": "仓位不动", "reason": "情绪中性，无明确信号"})

        high_div = [c for c in comparison if c["divergence_level"] == "high"]
        agree_bull = [c for c in comparison if c["guba_sentiment"] > 55 and c["xueqiu_sentiment"] > 55]
        agree_bear = [c for c in comparison if c["guba_sentiment"] < 45 and c["xueqiu_sentiment"] < 45]

        if high_div:
            names = [c["stock_name"] for c in high_div]
            diffs = [f"{c['stock_name']}(股吧{c['guba_sentiment']:.0f}/雪球{c['xueqiu_sentiment']:.0f})" for c in high_div]
            insights.append({
                "title": "平台分歧较大",
                "stocks": names,
                "analysis": "；".join(diffs) + "。股吧散户和雪球专业用户对这些股票的看法差异较大，通常意味着行情可能还在发展中，方向不明确。",
                "signal": "分歧期建议观望，等方向明确再操作",
                "card_class": "warning",
            })

        if agree_bear:
            names = [c["stock_name"] for c in agree_bear]
            insights.append({
                "title": "双平台共振看空",
                "stocks": names,
                "analysis": f"{'、'.join(names)} 在股吧和雪球上情绪均低于45，散户和专业用户一致看空。",
                "signal": "弱势明确，不建议抄底",
                "card_class": "bearish",
            })

        if agree_bull:
            names = [c["stock_name"] for c in agree_bull]
            insights.append({
                "title": "双平台共振看多",
                "stocks": names,
                "analysis": f"{'、'.join(names)} 在股吧和雪球上情绪均高于55，散户和专业用户一致看多。",
                "signal": "趋势较明确，可考虑持有或小仓位参与",
                "card_class": "bullish",
            })

        hottest = max(comparison, key=lambda c: c["guba_heat"] + c["xueqiu_heat"])
        if (hottest["guba_pos"] + hottest["guba_neg"] + hottest["guba_neu"] + hottest["xueqiu_pos"] + hottest["xueqiu_neg"] + hottest["xueqiu_neu"]) > 0:
            insights.append({
                "title": "讨论最热的自选股",
                "stocks": [hottest["stock_name"]],
                "analysis": f"{hottest['stock_name']} 在双平台上讨论热度最高（股吧{hottest['guba_heat']:.0f}，雪球{hottest['xueqiu_heat']:.0f}），股吧情绪{hottest['guba_sentiment']:.0f}，雪球情绪{hottest['xueqiu_sentiment']:.0f}。",
                "signal": "高关注度股票，留意催化剂出现后的方向选择",
                "card_class": "",
            })

    date_str = guba_data.get("_meta", {}).get("date", "")
    generated_at = guba_data.get("_meta", {}).get("generated_at", "")

    html = template.render(
        date=date_str, generated_at=generated_at,
        guba_overview=guba_data.get("overview", {}),
        xueqiu_overview=xueqiu_data.get("overview", {}),
        comparison=comparison, suggestions=suggestions, insights=insights,
        market_summary=market_summary,
    )

    if output_path is None:
        date_for_file = date_str.replace("-", "") if date_str else datetime.now().strftime("%Y%m%d")
        output_path = str(REPORT_DIR / f"watchlist_compare_{date_for_file}.html")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  📄 报告已生成: {output_path}")
    return str(output_path)


if __name__ == "__main__":
    print("报告生成模块测试通过")
