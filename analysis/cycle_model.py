# -*- coding: utf-8 -*-
"""
多维状态描述模型
基于情绪位置、热度位置、分歧度等维度，描述当前市场状态
不强制归类到固定阶段，而是描述各维度的实际状态
"""
from typing import Dict, List


def _sentiment_level(val: float) -> str:
    if val >= 80:
        return "极度乐观"
    elif val >= 55:
        return "偏多"
    elif val >= 45:
        return "中性"
    elif val >= 20:
        return "偏空"
    else:
        return "极度悲观"


def _heat_level(val: float) -> str:
    if val >= 75:
        return "过热"
    elif val >= 50:
        return "活跃"
    elif val >= 25:
        return "适中"
    else:
        return "低迷"


def _divergence_level(val: float) -> str:
    if val >= 0.65:
        return "分歧大"
    elif val >= 0.35:
        return "适中"
    else:
        return "一致"


def _trend_arrow(trend: float) -> str:
    if trend > 0.05:
        return "↑"
    elif trend < -0.05:
        return "↓"
    else:
        return "—"


def _match_pattern(sentiment, heat, divergence, sent_trend=0, heat_trend=0):
    """根据各维度组合匹配状态模式，返回 (pattern_name, emoji, color, css_class, signal)"""
    s_level = _sentiment_level(sentiment)
    h_level = _heat_level(heat)
    d_level = _divergence_level(divergence)

    sent_up = sent_trend > 0.05
    sent_down = sent_trend < -0.05
    heat_up = heat_trend > 0.05
    heat_down = heat_trend < -0.05

    if sentiment < 20 and heat < 25 and divergence < 0.35:
        return ("冰点期", "❄️", "#722ed1", 5,
                "一致性悲观，关注度低迷，市场情绪降至冰点")

    if sentiment > 80 and heat > 75 and divergence < 0.35:
        return ("过热期", "🔥", "#f5222d", 5,
                "一致性乐观，热度爆表，注意回调风险")

    if sentiment > 55 and heat > 50 and 0.35 <= divergence < 0.65:
        if sent_up and heat_up:
            return ("加速期", "🚀", "#1890ff", 2,
                    "情绪偏多且上升，热度活跃且上升，多头氛围浓厚")
        if sent_down and heat_down:
            return ("退热期", "📉", "#fa8c16", 4,
                    "情绪仍偏多但下降，热度开始回落，炒作可能接近尾声")
        return ("活跃期", "📊", "#1890ff", 2,
                "情绪偏多，讨论活跃，多空仍在博弈")

    if sentiment < 45 and heat > 50 and divergence > 0.5:
        return ("恐慌期", "😨", "#fa8c16", 4,
                "情绪偏空但讨论激烈，疑似恐慌蔓延")

    if 45 <= sentiment <= 55 and heat < 30 and divergence > 0.6:
        return ("蓄势期", "🌱", "#52c41a", 1,
                "情绪中性，热度低迷，多空分歧大，可能在蓄势")

    if divergence > 0.65:
        return ("分歧期", "⚔️", "#fa8c16", 4,
                "多空分歧极大，方向不明，需要等待信号确认")

    if sentiment > 55 and heat < 30:
        return ("存量期", "🧊", "#722ed1", 5,
                "情绪偏多但热度低迷，可能是存量博弈或底部缓慢吸筹")

    if sentiment < 45 and heat < 25:
        return ("低迷期", "🥶", "#722ed1", 5,
                "情绪偏空，热度低迷，市场关注度低")

    return ("震荡期", "📊", "#8c8c8c", 2,
            f"情绪{s_level}，热度{h_level}，分歧{d_level}")


def determine_cycle_stage(metrics: Dict, history: List[Dict] = None) -> Dict:
    """
    多维状态描述

    Args:
        metrics: 当前情绪指标
        history: 历史指标列表（用于判断趋势方向）

    Returns:
        dict: 状态描述，兼容旧字段名
    """
    sentiment = metrics.get("sentiment_index", 50)
    heat = metrics.get("heat_score", 0)
    divergence = metrics.get("divergence", 0.5)

    sent_trend = _calculate_trend(metrics, history, "sentiment_index")
    heat_trend = _calculate_trend(metrics, history, "heat_score")

    s_level = _sentiment_level(sentiment)
    h_level = _heat_level(heat)
    d_level = _divergence_level(divergence)

    pattern_name, emoji, color, css_class, signal = _match_pattern(
        sentiment, heat, divergence, sent_trend, heat_trend)

    desc_parts = []
    desc_parts.append(f"情绪{s_level}")
    if history:
        desc_parts.append(_trend_arrow(sent_trend))
    desc_parts.append(f"，热度{h_level}")
    if history:
        desc_parts.append(_trend_arrow(heat_trend))
    desc_parts.append(f"，分歧{d_level}")
    description = "".join(desc_parts)

    confidence = _calc_confidence(sentiment, heat, divergence, sent_trend, heat_trend)

    return {
        "stage": css_class,
        "stage_name": pattern_name,
        "stage_emoji": emoji,
        "stage_color": color,
        "confidence": confidence,
        "description": description,
        "signal": signal,
        "sentiment_state": s_level,
        "sentiment_direction": _trend_arrow(sent_trend) if history else "—",
        "heat_state": h_level,
        "heat_direction": _trend_arrow(heat_trend) if history else "—",
        "divergence_state": d_level,
        "cycle_score": round(sentiment / 20, 1),
        "score_details": {
            "sentiment_position": round(sentiment, 1),
            "heat_position": round(heat, 1),
            "divergence": round(divergence, 2),
            "sentiment_trend": round(sent_trend, 3),
            "heat_trend": round(heat_trend, 3),
        },
    }


def _calc_confidence(sentiment, heat, divergence, sent_trend, heat_trend):
    """置信度：状态越极端或趋势越明确，置信度越高"""
    sent_extremity = abs(sentiment - 50) / 50
    heat_extremity = heat / 100
    trend_strength = (abs(sent_trend) + abs(heat_trend)) / 2
    div_factor = 1 - divergence

    confidence = (sent_extremity * 0.3 + heat_extremity * 0.3 +
                  trend_strength * 0.2 + div_factor * 0.2)
    return max(0.1, min(0.95, confidence))


def _calculate_trend(current: Dict, history: List[Dict], key: str) -> float:
    """计算指标变化趋势"""
    if not history:
        return 0.0
    recent = history[-min(3, len(history)):]
    avg_history = sum(h.get(key, 0) for h in recent) / len(recent)
    if avg_history == 0:
        return 0.0
    return (current.get(key, 0) - avg_history) / avg_history


def determine_market_cycle(overview: Dict, theme_metrics: List[Dict],
                           stock_metrics: List[Dict] = None) -> Dict:
    """整体市场状态描述"""
    if stock_metrics:
        bullish = sum(1 for m in stock_metrics if m.get("sentiment_index", 50) > 50)
        bearish = sum(1 for m in stock_metrics if m.get("sentiment_index", 50) <= 50)
        total = bullish + bearish
        if total > 0:
            bullish_ratio = bullish / total
            market_divergence = 1 - abs(bullish_ratio - 0.5) * 2
        else:
            market_divergence = 0.5
    else:
        market_divergence = 0.5

    pseudo_metrics = {
        "sentiment_index": overview.get("overall_sentiment", 50),
        "heat_score": overview.get("overall_heat", 0),
        "divergence": market_divergence,
        "positive_ratio": overview.get("bullish_ratio", 0.5),
    }

    result = determine_cycle_stage(pseudo_metrics, history=None)
    result["is_market_wide"] = True

    stage_distribution = {}
    for tm in theme_metrics:
        tc = determine_cycle_stage(tm, history=None)
        name = tc["stage_name"]
        stage_distribution[name] = stage_distribution.get(name, 0) + 1
    result["theme_stage_distribution"] = stage_distribution

    return result
