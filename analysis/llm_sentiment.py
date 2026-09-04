# -*- coding: utf-8 -*-
"""
Deepseek 大模型情绪分析模块
用大模型替代词典法，对自选股帖子进行情绪分类
"""
import json
import time
import re
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
from config.settings import (
    DEEPSEEK_API_KEY, DEEPSEEK_API_URL, DEEPSEEK_MODEL,
    LLM_BATCH_SIZE, LLM_MAX_RETRIES, LLM_REQUEST_DELAY, LLM_CONCURRENCY,
)

try:
    import requests
except ImportError:
    requests = None


def _build_prompt(posts: List[Dict]) -> str:
    """构建批量分析的 prompt"""
    lines = []
    for i, post in enumerate(posts):
        stock = post.get("stock_name", "")
        title = (post.get("title", "") or "")[:80]
        content = (post.get("content", "") or "")[:120]
        text = f"{title} {content}".strip()
        lines.append(f"{i+1}. [{stock}] {text}")

    return f"""你是A股散户情绪分析专家。请判断以下每条股吧/雪球帖子作者对该股票的看法是看多、看空还是中性。

判断规则：
- positive: 作者明确看好该股票（看涨、买入、加仓、利好、机会、抄底买入等）
- negative: 作者明确看空该股票（看跌、卖出、减仓、利空、风险、垃圾、套牢、砸盘等）
- neutral: 中性/观望/事实陈述/不确定（纯信息分享、提问、无明显倾向、多空信号混合）

注意：
- "跌到XX我买"是看多（逢低买入）
- "逢跌就买"是看多
- "清仓了，后面会涨"是看多（预测涨）
- "赚麻了"是看多
- 纯数据/财报分析无明显倾向的判中性

帖子列表：
{chr(10).join(lines)}

请只返回JSON数组，不要多余文字：
[{{"id": 1, "sentiment": "positive", "confidence": 0.8}}, ...]"""


def _call_deepseek(prompt: str) -> str:
    """调用 Deepseek API"""
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY 未配置")

    if requests is None:
        raise ImportError("requests 库未安装")

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
    }

    resp = requests.post(
        DEEPSEEK_API_URL, headers=headers, json=payload, timeout=30
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _parse_response(text: str, batch_size: int) -> List[Dict]:
    """解析大模型返回的JSON"""
    text = text.strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("data", data.get("results", [data]))
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # 尝试提取 JSON 数组
    match = re.search(r'\[.*?\]', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    return [{"id": i+1, "sentiment": "neutral", "confidence": 0.0} for i in range(batch_size)]


def _map_sentiment(label: str, confidence: float) -> Dict:
    """将大模型结果映射为与词典法兼容的格式"""
    label = (label or "neutral").lower().strip()
    if label in ("positive", "bullish", "看多", "多"):
        sentiment = "positive"
        score = 0.5 + confidence * 0.5
    elif label in ("negative", "bearish", "看空", "空"):
        sentiment = "negative"
        score = -(0.5 + confidence * 0.5)
    else:
        sentiment = "neutral"
        score = 0.0

    return {
        "sentiment": sentiment,
        "score": round(score, 3),
        "confidence": round(confidence, 3),
        "positive_count": 1 if sentiment == "positive" else 0,
        "negative_count": 1 if sentiment == "negative" else 0,
        "positive_words": [],
        "negative_words": [],
    }


def _process_batch(posts: List[Dict], batch_idx: int) -> List[Dict]:
    """处理一批帖子"""
    prompt = _build_prompt(posts)
    last_error = None

    for attempt in range(LLM_MAX_RETRIES):
        try:
            response_text = _call_deepseek(prompt)
            results = _parse_response(response_text, len(posts))

            enriched = []
            for i, post in enumerate(posts):
                result = results[i] if i < len(results) else {}
                label = result.get("sentiment", "neutral")
                confidence = float(result.get("confidence", 0.5))
                sentiment_data = _map_sentiment(label, confidence)
                enriched.append({**post, **sentiment_data})

            return enriched
        except Exception as e:
            last_error = e
            if attempt < LLM_MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue

    # 全部失败，回退到中性
    print(f"  ⚠ 批次{batch_idx+1} LLM调用失败({last_error})，回退中性")
    return [{**post, **_map_sentiment("neutral", 0.0)} for post in posts]


def analyze_posts_with_llm(posts: List[Dict]) -> List[Dict]:
    """
    用 Deepseek 大模型批量分析帖子情绪

    Args:
        posts: 帖子列表

    Returns:
        带情绪字段的帖子列表
    """
    if not posts:
        return []

    if not DEEPSEEK_API_KEY:
        print("  ⚠ DEEPSEEK_API_KEY 未配置，回退到词典法")
        from analysis.sentiment import get_sentiment_analyzer
        analyzer = get_sentiment_analyzer()
        return [{**p, **analyzer.analyze_post(p)} for p in posts]

    # 分批
    batches = [
        posts[i:i + LLM_BATCH_SIZE]
        for i in range(0, len(posts), LLM_BATCH_SIZE)
    ]
    print(f"  🤖 LLM分析: {len(posts)}条帖子，分{len(batches)}批，{LLM_CONCURRENCY}路并发")

    results = [None] * len(posts)

    with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as executor:
        futures = {}
        for batch_idx, batch in enumerate(batches):
            future = executor.submit(_process_batch, batch, batch_idx)
            futures[future] = (batch_idx, batch)

        completed = 0
        for future in as_completed(futures):
            batch_idx, batch = futures[future]
            try:
                batch_results = future.result()
                start_idx = batch_idx * LLM_BATCH_SIZE
                for i, r in enumerate(batch_results):
                    results[start_idx + i] = r
                completed += 1
                print(f"  ✓ 批次{batch_idx+1}/{len(batches)} 完成")
            except Exception as e:
                print(f"  ✗ 批次{batch_idx+1} 失败: {e}")
                start_idx = batch_idx * LLM_BATCH_SIZE
                for i, post in enumerate(batch):
                    results[start_idx + i] = {**post, **_map_sentiment("neutral", 0.0)}

            time.sleep(LLM_REQUEST_DELAY)

    return results


def _build_analysis_prompt(comparison_data: List[Dict], guba_overview: Dict, xueqiu_overview: Dict) -> str:
    """构建自选股对比分析的prompt"""
    # 市场概览
    g_sent = guba_overview.get("sentiment_index", 0)
    g_heat = guba_overview.get("overall_heat", 0)
    g_stage = guba_overview.get("cycle_stage", {}).get("stage_name", "")
    g_bull = guba_overview.get("bullish_count", 0)
    g_bear = guba_overview.get("bearish_count", 0)

    x_sent = xueqiu_overview.get("sentiment_index", 0)
    x_heat = xueqiu_overview.get("overall_heat", 0)
    x_stage = xueqiu_overview.get("cycle_stage", {}).get("stage_name", "")
    x_bull = xueqiu_overview.get("bullish_count", 0)
    x_bear = xueqiu_overview.get("bearish_count", 0)

    # 自选股数据表
    lines = []
    for c in comparison_data:
        name = c["stock_name"]
        price = c.get("stock_price", 0)
        chg = c.get("change_percent", 0)
        g_s = c.get("guba_sentiment", 0)
        g_h = c.get("guba_heat", 0)
        g_st = c.get("guba_stage", "")
        g_posts = f"{c['guba_pos']}/{c['guba_neg']}/{c['guba_neu']}"
        x_s = c.get("xueqiu_sentiment", 0)
        x_h = c.get("xueqiu_heat", 0)
        x_st = c.get("xueqiu_stage", "")
        x_posts = f"{c['xueqiu_pos']}/{c['xueqiu_neg']}/{c['xueqiu_neu']}"

        lines.append(
            f"{name} {price:.2f} {chg:+.2f}% | "
            f"股吧:情绪{g_s:.1f} 热度{g_h:.1f} {g_st} 多空{g_posts} | "
            f"雪球:情绪{x_s:.1f} 热度{x_h:.1f} {x_st} 多空{x_posts}"
        )

    return f"""你是A股散户情绪分析专家。请根据以下双平台（股吧/雪球）数据，对每只自选股给出明日操作建议，并总结关键发现。

情绪阈值：>70偏多，<30偏空，45-55中性。热度>75为高，<40为低。多/空/中为帖子数。

== 市场概览 ==
股吧: 情绪{g_sent:.1f} 热度{g_heat:.1f} {g_stage} 看多{g_bull}/看空{g_bear}
雪球: 情绪{x_sent:.1f} 热度{x_heat:.1f} {x_stage} 看多{x_bull}/看空{x_bear}

== 自选股数据 ==
{chr(10).join(lines)}

请返回JSON，包含三部分：

1. market_summary: 50字以内的市场整体总结
2. suggestions: 每只股票一条，包含 stock_name, direction(偏多/偏空/谨慎/中性/回避), action(具体建议), reason(30字以内理由)
3. insights: 2-4条关键发现，包含 title(10字以内), stocks(相关股票名列表), analysis(50字分析), signal(一句话建议), card_class(bullish/bearish/warning/空字符串)

只返回JSON，不要多余文字：
{{"market_summary": "...", "suggestions": [...], "insights": [...]}}"""


def generate_watchlist_analysis(
    comparison_data: List[Dict],
    guba_overview: Dict,
    xueqiu_overview: Dict,
) -> Dict:
    """
    用Deepseek大模型生成自选股对比分析

    Args:
        comparison_data: 自选股对比数据列表
        guba_overview: 股吧市场概览
        xueqiu_overview: 雪球市场概览

    Returns:
        dict: {market_summary, suggestions, insights}
    """
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY 未配置")

    prompt = _build_analysis_prompt(comparison_data, guba_overview, xueqiu_overview)

    for attempt in range(LLM_MAX_RETRIES):
        try:
            response_text = _call_deepseek(prompt)
            data = json.loads(response_text)
            if isinstance(data, dict):
                return data
            elif isinstance(data, list) and len(data) > 0:
                return data[0]
        except Exception as e:
            last_error = e
            if attempt < LLM_MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue

    raise RuntimeError(f"LLM分析失败: {last_error}")


if __name__ == "__main__":
    # 测试
    test_posts = [
        {"stock_name": "恒力石化", "title": "难得领涨一回，希望能坚持到收盘！", "content": ""},
        {"stock_name": "紫金矿业", "title": "跌到25我买点，快点下来吧", "content": ""},
        {"stock_name": "安琪酵母", "title": "阴极铜副产品硫酸赚麻了", "content": ""},
        {"stock_name": "重庆啤酒", "title": "垃圾", "content": ""},
        {"stock_name": "兴业银行", "title": "今天清仓了，听专家说明天涨5%起步", "content": ""},
    ]
    results = analyze_posts_with_llm(test_posts)
    for r in results:
        print(f"  {r['stock_name']}: {r['sentiment']} (score={r['score']}, conf={r['confidence']})")
