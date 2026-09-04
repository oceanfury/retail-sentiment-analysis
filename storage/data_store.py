# -*- coding: utf-8 -*-
"""
数据存储模块
使用 JSON 文件存储每日聚合指标，CSV 存储原始帖子
"""
import json
import csv
import os
from datetime import datetime
from typing import List, Dict
from pathlib import Path
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
from config.settings import RAW_DATA_DIR, MARKET_HEAT_FILE


def save_daily_data(data: Dict, date_str: str = None, source: str = None) -> str:
    """
    保存每日数据到 JSON 文件

    Args:
        data: 数据字典
        date_str: 日期字符串，默认今天
        source: 数据来源（"xueqiu" / "guba" / None 综合）

    Returns:
        str: 保存的文件路径
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    if source == "xueqiu":
        filename = f"xueqiu_data_{date_str}.json"
    elif source == "guba":
        filename = f"guba_data_{date_str}.json"
    else:
        filename = f"sentiment_data_{date_str}.json"
    filepath = RAW_DATA_DIR / filename

    # 补充元数据
    data["_meta"] = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": date_str,
        "version": "1.0",
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    print(f"  💾 数据已保存: {filepath}")
    return str(filepath)


def save_raw_posts_csv(posts: List[Dict], date_str: str = None, source: str = None) -> str:
    """
    保存原始帖子到 CSV 文件（可用 Excel 打开）

    Args:
        posts: 帖子列表（含情绪分析结果）
        date_str: 日期字符串，默认今天
        source: 数据来源（"xueqiu" / "guba" / None 综合）

    Returns:
        str: 保存的文件路径
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    if source == "xueqiu":
        filename = f"xueqiu_posts_{date_str}.csv"
    elif source == "guba":
        filename = f"guba_posts_{date_str}.csv"
    else:
        filename = f"raw_posts_{date_str}.csv"
    filepath = RAW_DATA_DIR / filename

    # CSV 列定义
    fieldnames = [
        "stock_code", "stock_name", "source",
        "title", "content", "author",
        "publish_time", "read_count", "comment_count", "like_count",
        "stock_followers",
        "sentiment", "score", "confidence",
        "url",
    ]

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for post in posts:
            writer.writerow(post)

    print(f"  📋 原始帖子已保存: {filepath}")
    return str(filepath)


def save_raw_posts_json(posts: List[Dict], date_str: str = None, source: str = None) -> str:
    """保存原始帖子到 JSON（含全部字段，用于增量合并）"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    if source:
        filename = f"{source}_raw_posts_{date_str}.json"
    else:
        filename = f"raw_posts_{date_str}.json"
    filepath = RAW_DATA_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, default=str)

    return str(filepath)


def load_raw_posts_json(date_str: str = None, source: str = None) -> List[Dict]:
    """加载原始帖子 JSON（用于增量合并）"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    if source:
        filename = f"{source}_raw_posts_{date_str}.json"
    else:
        filename = f"raw_posts_{date_str}.json"
    filepath = RAW_DATA_DIR / filename

    if not filepath.exists():
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def load_daily_data(date_str: str = None, source: str = None) -> Dict:
    """
    加载每日数据

    Args:
        date_str: 日期字符串，默认今天
        source: 数据来源（"xueqiu" / "guba" / None 综合）

    Returns:
        dict: 数据字典
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")

    if source == "xueqiu":
        filename = f"xueqiu_data_{date_str}.json"
    elif source == "guba":
        filename = f"guba_data_{date_str}.json"
    else:
        filename = f"sentiment_data_{date_str}.json"
    filepath = RAW_DATA_DIR / filename

    if not filepath.exists():
        return {}

    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def get_history_data(days: int = 7) -> List[Dict]:
    """
    获取最近 N 天的历史数据

    Args:
        days: 天数

    Returns:
        list of dict: 按日期升序排列的历史数据列表
    """
    from datetime import timedelta

    history = []
    today = datetime.now().date()

    for i in range(days, 0, -1):
        date = today - timedelta(days=i)
        date_str = date.strftime("%Y%m%d")
        data = load_daily_data(date_str)
        if data:
            history.append(data)

    return history


def get_stock_history(stock_code: str, days: int = 7) -> List[Dict]:
    """
    获取某只股票的历史情绪指标

    Args:
        stock_code: 股票代码
        days: 天数

    Returns:
        list of dict: 该股票每日的情绪指标
    """
    history = get_history_data(days)
    stock_history = []

    for data in history:
        stock_metrics = data.get("stock_metrics", [])
        for sm in stock_metrics:
            if sm.get("stock_code") == stock_code or sm.get("code") == stock_code:
                entry = {
                    "date": data.get("_meta", {}).get("date", ""),
                    **sm
                }
                stock_history.append(entry)
                break

    return stock_history


HISTORY_FILE = RAW_DATA_DIR / "history.json"


def save_history_snapshot(data: Dict, source: str):
    """
    将当天市场级指标追加到历史文件

    Args:
        data: 完整分析数据
        source: "xueqiu" / "guba"
    """
    date_str = data.get("_meta", {}).get("date") or datetime.now().strftime("%Y%m%d")
    overview = data.get("overview", {})
    market_cycle = data.get("market_cycle", {})

    snapshot = {
        "date": date_str,
        "sentiment": overview.get("overall_sentiment", 0),
        "heat": overview.get("overall_heat", 0),
        "divergence": market_cycle.get("score_details", {}).get("divergence", 0),
        "stage_name": market_cycle.get("stage_name", ""),
    }

    history = {}
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)

    key = source or "default"
    entries = history.get(key, [])
    entries = [e for e in entries if e.get("date") != date_str]
    entries.append(snapshot)
    entries.sort(key=lambda e: e.get("date", ""))
    entries = entries[-90:]
    history[key] = entries

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"  📈 历史快照已保存: {source} ({len(entries)} days)")


def load_history(source: str, days: int = 30) -> List[Dict]:
    """
    加载某平台的历史快照

    Args:
        source: "xueqiu" / "guba"
        days: 最多加载天数

    Returns:
        按日期升序排列的历史快照列表
    """
    if not HISTORY_FILE.exists():
        return []

    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)

    entries = history.get(source or "default", [])
    return entries[-days:]


WATCHLIST_HISTORY_FILE = RAW_DATA_DIR / "watchlist_history.json"


def save_watchlist_history(stock_code: str, date_str: str,
                           sentiment: float, heat: float, divergence: float,
                           stage_name: str = "", post_count: int = 0,
                           source: str = ""):
    """保存自选股每日指标到历史文件（按平台分开存储）"""
    key = f"{stock_code}_{source}" if source else stock_code
    if not WATCHLIST_HISTORY_FILE.exists():
        history = {}
    else:
        with open(WATCHLIST_HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)

    entries = history.get(key, [])
    entries = [e for e in entries if e.get("date") != date_str]
    entries.append({
        "date": date_str,
        "sentiment": sentiment,
        "heat": heat,
        "divergence": divergence,
        "stage_name": stage_name,
        "post_count": post_count,
        "source": source,
    })
    entries.sort(key=lambda e: e.get("date", ""))
    entries = entries[-90:]
    history[key] = entries

    with open(WATCHLIST_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def load_watchlist_history(stock_code: str, days: int = 30,
                           source: str = "") -> List[Dict]:
    """加载自选股历史数据（按平台分开加载）"""
    key = f"{stock_code}_{source}" if source else stock_code
    if not WATCHLIST_HISTORY_FILE.exists():
        return []
    with open(WATCHLIST_HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
    return history.get(key, [])[-days:]


# ============== 市场热度（大盘UV指数）存储 ==============

def save_market_heat(heat_data: Dict[str, Dict]) -> str:
    """
    保存市场热度数据到文件
    新数据会合并进已有数据，临时值会被真实值覆盖

    Args:
        heat_data: {"YYYY-MM-DD": {"uv_index": float, "heat_score": float, "is_provisional": bool, "source": str}, ...}

    Returns:
        str: 文件路径
    """
    existing = {}
    if MARKET_HEAT_FILE.exists():
        with open(MARKET_HEAT_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)

    # 合并：新数据优先，但如果旧数据是真实值、新数据是临时值，则保留真实值
    for date_str, entry in heat_data.items():
        old_entry = existing.get(date_str)
        if old_entry and old_entry.get("is_provisional") is False and entry.get("is_provisional") is True:
            # 旧数据是真实值，新数据是临时值，保留真实值
            continue
        existing[date_str] = entry

    # 只保留最近 180 天的数据
    sorted_dates = sorted(existing.keys())
    if len(sorted_dates) > 180:
        for d in sorted_dates[:-180]:
            del existing[d]

    MARKET_HEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MARKET_HEAT_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"  📈 市场热度数据已保存: {len(existing)} 天")
    return str(MARKET_HEAT_FILE)


def load_market_heat() -> Dict[str, Dict]:
    """
    加载市场热度数据

    Returns:
        dict: {"YYYY-MM-DD": {"uv_index": float, "heat_score": float, "is_provisional": bool, "source": str}, ...}
    """
    if not MARKET_HEAT_FILE.exists():
        return {}
    with open(MARKET_HEAT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_market_heat_by_date(date_str: str) -> Dict:
    """
    获取指定日期的市场热度

    Args:
        date_str: "YYYY-MM-DD"

    Returns:
        dict: 热度数据，如果没有返回空dict
    """
    all_data = load_market_heat()
    return all_data.get(date_str, {})


if __name__ == "__main__":
    print("数据存储模块测试通过")
