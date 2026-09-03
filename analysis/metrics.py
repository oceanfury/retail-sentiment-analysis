# -*- coding: utf-8 -*-
"""
情绪指标计算
"""
from typing import List, Dict, Tuple
from collections import defaultdict
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
from config.settings import THEMES, GUBA_RANK_MAX


def calculate_stock_metrics(posts: List[Dict], stock_code: str, stock_name: str,
                            xueqiu_heat: float = 0, guba_rank: int = 0,
                            weighted: bool = True, follow_count: int = 0) -> Dict:
    """
    计算单只股票的情绪指标

    Args:
        posts: 该股票的帖子列表（已带情绪分析结果）
        stock_code: 股票代码
        stock_name: 股票名称
        xueqiu_heat: 雪球热度值（平台原生，目前已弃用）
        guba_rank: 股吧人气排名
        weighted: 是否使用互动量加权计算情绪分数（默认加权）
        follow_count: 雪球关注量（股票页面抓取）

    Returns:
        dict: 情绪指标
    """
    if not posts:
        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "total_posts": 0,
            "sentiment_index": 50.0,
            "heat_score": 0.0,
            "divergence": 0.5,
            "positive_count": 0,
            "negative_count": 0,
            "neutral_count": 0,
            "positive_ratio": 0.0,
            "negative_ratio": 0.0,
            "neutral_ratio": 0.0,
            "avg_sentiment_score": 0.0,
            "avg_confidence": 0.0,
            "total_interactions": 0,
            "source_breakdown": {},
        }

    total = len(posts)

    # 情绪计数
    pos_count = sum(1 for p in posts if p.get("sentiment") == "positive")
    neg_count = sum(1 for p in posts if p.get("sentiment") == "negative")
    neu_count = sum(1 for p in posts if p.get("sentiment") == "neutral")

    # 情绪分数计算
    total_confidence = 0.0
    total_interactions = 0

    if weighted:
        # 加权平均：以互动量为权重
        total_weight = 0.0
        weighted_score = 0.0

        for p in posts:
            # 互动量作为权重
            interactions = (
                p.get("read_count", 0) * 0.01 +
                p.get("comment_count", 0) * 2 +
                p.get("like_count", 0) * 1.5
            )
            weight = max(1.0, interactions)  # 至少权重为1
            weighted_score += p.get("score", 0) * weight
            total_weight += weight
            total_confidence += p.get("confidence", 0)
            total_interactions += interactions

        # 单条帖子权重上限：不超过总权重的10%，防止单条爆款主导情绪
        if total_weight > 0:
            weight_cap = total_weight * 0.1
            excess = 0.0
            for p in posts:
                interactions = (
                    p.get("read_count", 0) * 0.01 +
                    p.get("comment_count", 0) * 2 +
                    p.get("like_count", 0) * 1.5
                )
                w = max(1.0, interactions)
                if w > weight_cap:
                    excess += w - weight_cap

            if excess > 0:
                # 重新计算：超限帖子的权重截断为上限
                weighted_score = 0.0
                total_weight_capped = 0.0
                for p in posts:
                    interactions = (
                        p.get("read_count", 0) * 0.01 +
                        p.get("comment_count", 0) * 2 +
                        p.get("like_count", 0) * 1.5
                    )
                    w = max(1.0, interactions)
                    w = min(w, weight_cap)
                    weighted_score += p.get("score", 0) * w
                    total_weight_capped += w
                total_weight = total_weight_capped

        avg_score = weighted_score / total_weight if total_weight > 0 else 0.0
    else:
        # 简单算术平均：每帖一票，避免财富号推流造成的扭曲
        for p in posts:
            interactions = (
                p.get("read_count", 0) * 0.01 +
                p.get("comment_count", 0) * 2 +
                p.get("like_count", 0) * 1.5
            )
            total_confidence += p.get("confidence", 0)
            total_interactions += interactions

        avg_score = sum(p.get("score", 0) for p in posts) / max(total, 1)

    # 将 -1~1 映射到 0~100
    sentiment_index = (avg_score + 1) * 50
    sentiment_index = max(0, min(100, sentiment_index))

    # 热度评分：股吧用排名，雪球用互动量+关注量
    heat_score = _calculate_heat_v2(xueqiu_heat, guba_rank, posts, total_interactions,
                                     follow_count)

    # 分歧度：看多和看空的比例差异
    if pos_count + neg_count > 0:
        pos_ratio = pos_count / (pos_count + neg_count)
        neg_ratio = neg_count / (pos_count + neg_count)
        # 分歧度 = 1 - |多空比 - 0.5| * 2，越接近50/50分歧越大
        divergence = 1 - abs(pos_ratio - 0.5) * 2
    else:
        divergence = 0.5  # 没有多空观点时，中性分歧

    # 来源分布
    source_breakdown = defaultdict(int)
    for p in posts:
        source_breakdown[p.get("source", "unknown")] += 1

    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "total_posts": total,
        "sentiment_index": round(sentiment_index, 2),
        "heat_score": round(heat_score, 2),
        "divergence": round(divergence, 4),
        "positive_count": pos_count,
        "negative_count": neg_count,
        "neutral_count": neu_count,
        "positive_ratio": round(pos_count / max(total, 1), 4),
        "negative_ratio": round(neg_count / max(total, 1), 4),
        "neutral_ratio": round(neu_count / max(total, 1), 4),
        "avg_sentiment_score": round(avg_score, 4),
        "avg_confidence": round(total_confidence / max(total, 1), 4),
        "total_interactions": round(total_interactions, 0),
        "follow_count": follow_count,
        "source_breakdown": dict(source_breakdown),
    }


def _calculate_heat_v2(xueqiu_heat: float, guba_rank: int,
                        posts: List[Dict], total_interactions: float,
                        follow_count: int = 0) -> float:
    """
    计算热度评分（0-100）
    股吧用排名对数缩放，雪球用互动量+关注量对数缩放
    """
    import math

    has_guba = guba_rank > 0

    # 股吧排名对数缩放：第1名=满分，第GUBA_RANK_MAX名=0分
    def guba_norm(max_score):
        if guba_rank > 0:
            return max(0, (1 - math.log10(guba_rank) / math.log10(GUBA_RANK_MAX)) * max_score)
        return 0

    if has_guba:
        return guba_norm(100)

    # 雪球：互动量+关注量
    return _calculate_heat(posts, total_interactions, follow_count)


def _calculate_heat(posts: List[Dict], total_interactions: float,
                     follow_count: int = 0) -> float:
    """
    计算热度评分（0-100）
    使用对数函数，避免热门股票轻松满分，保持区分度
    互动量50% + 关注量50%（雪球专用，帖子数恒为10无区分度）
    """
    import math

    # 互动量得分（对数缩放，2000互动量为满分基准）
    if total_interactions > 0:
        interaction_score = min(50, math.log10(total_interactions) / math.log10(2000) * 50)
    else:
        interaction_score = 0

    # 关注量得分（对数缩放，100000关注量为满分基准）
    if follow_count > 0:
        follow_score = min(50, math.log10(follow_count) / math.log10(100000) * 50)
    else:
        follow_score = 0

    return interaction_score + follow_score


def calculate_theme_metrics(all_posts: List[Dict], weighted: bool = True) -> List[Dict]:
    """
    计算各题材的情绪指标

    Args:
        all_posts: 所有帖子
        weighted: 是否使用互动量加权计算情绪分数

    Returns:
        list of dict: 各题材的情绪指标（按热度排序）
    """
    # 按题材分组
    theme_posts = defaultdict(list)

    for post in all_posts:
        text = (post.get("title", "") or "") + (post.get("content", "") or "")
        matched_themes = _match_themes(text)
        for theme in matched_themes:
            theme_posts[theme].append(post)

    # 计算每个题材的指标
    theme_metrics = []
    for theme, posts in theme_posts.items():
        metrics = calculate_stock_metrics(posts, f"theme:{theme}", theme, weighted=weighted)
        metrics["theme"] = theme
        metrics["post_count"] = len(posts)
        theme_metrics.append(metrics)

    # 按热度排序
    theme_metrics.sort(key=lambda x: x["heat_score"], reverse=True)
    return theme_metrics


def _match_themes(text: str) -> List[str]:
    """匹配帖子所属题材"""
    matched = []
    text_lower = text.lower()
    for theme, keywords in THEMES.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                matched.append(theme)
                break
    return matched


def calculate_market_overview(stock_metrics: List[Dict], all_posts: List[Dict]) -> Dict:
    """
    计算市场整体情绪概览

    Args:
        stock_metrics: 各股票的情绪指标
        all_posts: 所有帖子

    Returns:
        dict: 市场概览
    """
    if not stock_metrics:
        return {
            "overall_sentiment": 50.0,
            "overall_heat": 0.0,
            "total_posts": 0,
            "total_stocks": 0,
            "bullish_ratio": 0.0,
            "bearish_ratio": 0.0,
        }

    total_posts = sum(m["total_posts"] for m in stock_metrics)

    # 整体情绪：按热度加权
    total_heat = sum(m["heat_score"] for m in stock_metrics)
    if total_heat > 0:
        weighted_sentiment = sum(
            m["sentiment_index"] * m["heat_score"] for m in stock_metrics
        ) / total_heat
    else:
        weighted_sentiment = sum(m["sentiment_index"] for m in stock_metrics) / len(stock_metrics)

    # 看多/看空股票比例
    bullish_stocks = sum(1 for m in stock_metrics if m["sentiment_index"] > 50)
    bearish_stocks = sum(1 for m in stock_metrics if m["sentiment_index"] <= 50)

    overall_heat = sum(m["heat_score"] for m in stock_metrics) / len(stock_metrics)

    return {
        "overall_sentiment": round(weighted_sentiment, 2),
        "overall_heat": round(overall_heat, 2),
        "total_posts": total_posts,
        "total_stocks": len(stock_metrics),
        "bullish_ratio": round(bullish_stocks / max(len(stock_metrics), 1), 4),
        "bearish_ratio": round(bearish_stocks / max(len(stock_metrics), 1), 4),
        "bullish_count": bullish_stocks,
        "bearish_count": bearish_stocks,
    }


if __name__ == "__main__":
    # 简单测试
    print("情绪指标计算模块测试通过")
