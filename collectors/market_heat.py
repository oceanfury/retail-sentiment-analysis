# -*- coding: utf-8 -*-
"""
市场热度采集器
从 apppc.com 获取东方财富网 APP 的 UV 指数，作为股吧大盘热度的外部基准

数据特点：
- UV 指数：独立访客数（万），反映东方财富APP整体活跃度
- 数据滞后约 6 天
- 提供 90 天历史数据（通过 getLastDatas 接口一次获取）
"""
import os
import sys
import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    MARKET_HEAT_APP_ID,
    MARKET_HEAT_UV_LOW,
    MARKET_HEAT_UV_HIGH,
    MARKET_HEAT_LAG_DAYS,
)


_playwright_available = None
_page_instance = None
_browser_instance = None
_playwright_instance = None
_context_instance = None


def _check_playwright() -> bool:
    global _playwright_available
    if _playwright_available is not None:
        return _playwright_available
    try:
        from playwright.sync_api import sync_playwright
        _playwright_available = True
    except ImportError:
        _playwright_available = False
    return _playwright_available


def _get_page():
    """获取浏览器页面（懒加载）"""
    global _page_instance, _playwright_instance, _browser_instance, _context_instance
    if _page_instance is not None:
        return _page_instance

    if not _check_playwright():
        return None

    from playwright.sync_api import sync_playwright
    _playwright_instance = sync_playwright().start()

    browsers_dir = Path(__file__).parent.parent / "browsers"
    if browsers_dir.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers_dir))

    _browser_instance = _playwright_instance.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
    )
    _context_instance = _browser_instance.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
        locale="zh-CN",
    )
    _context_instance.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        window.chrome = { runtime: {} };
    """)

    _page_instance = _context_instance.new_page()

    # 先访问详情页获取 Cookie
    try:
        _page_instance.goto(
            f"https://www.apppc.com/appdetail.html?id={MARKET_HEAT_APP_ID}",
            wait_until="domcontentloaded",
            timeout=15000
        )
        _page_instance.wait_for_timeout(2000)
    except Exception:
        pass

    return _page_instance


def close_browser():
    """关闭浏览器"""
    global _page_instance, _browser_instance, _context_instance, _playwright_instance
    if _page_instance is not None:
        try:
            _page_instance.close()
        except:
            pass
        _page_instance = None
    if _context_instance is not None:
        try:
            _context_instance.close()
        except:
            pass
        _context_instance = None
    if _browser_instance is not None:
        try:
            _browser_instance.close()
        except:
            pass
        _browser_instance = None
    if _playwright_instance is not None:
        try:
            _playwright_instance.stop()
        except:
            pass
        _playwright_instance = None


def fetch_uv_history(days: int = 90) -> List[Dict]:
    """
    获取 UV 指数历史数据（90 天）

    Args:
        days: 获取天数（最多约 197 天）

    Returns:
        list of dict: [{"date": "2026-08-28", "uv_index": 7640.57}, ...]
            按日期升序排列
    """
    page = _get_page()
    if page is None:
        print("  [WARN] Playwright 不可用，无法获取市场热度数据")
        return []

    # getLastDatas 接口的 dataTypeName 即天数（1/7/30/90/180/360）
    type_param = str(days) if days <= 360 else "360"
    url = (
        f"https://www.apppc.com/index.php?m=content&c=index&a=getLastDatas"
        f"&id={MARKET_HEAT_APP_ID}&dataTypeName={type_param}"
    )

    try:
        resp = page.request.get(url, timeout=10000)
        if resp.status != 200:
            print(f"  [WARN] 市场热度接口返回 {resp.status}")
            return []

        data = resp.json()
        xdata = data.get("xdata", [])
        ydata = data.get("ydata", [])

        result = []
        for date_str, uv_str in zip(xdata, ydata):
            try:
                uv_index = float(uv_str)
                result.append({
                    "date": date_str,
                    "uv_index": uv_index,
                })
            except (ValueError, TypeError):
                continue

        return result

    except Exception as e:
        print(f"  [ERROR] 获取市场热度数据失败: {e}")
        return []


def uv_to_heat_score(uv_index: float) -> float:
    """
    UV 指数归一化为 0-100 分热度

    Args:
        uv_index: UV 指数（万）

    Returns:
        float: 0-100 分的热度值
    """
    if uv_index <= MARKET_HEAT_UV_LOW:
        return 0.0
    if uv_index >= MARKET_HEAT_UV_HIGH:
        return 100.0
    ratio = (uv_index - MARKET_HEAT_UV_LOW) / (MARKET_HEAT_UV_HIGH - MARKET_HEAT_UV_LOW)
    return round(ratio * 100, 1)


def build_market_heat_data(uv_history: List[Dict], target_date: str = None) -> Dict[str, Dict]:
    """
    基于 UV 历史数据，构建从历史到今天每天的市场热度数据
    对于最新数据日期之后的日子，用最近一周平均值作为临时值填充

    Args:
        uv_history: UV 历史数据（升序）
        target_date: 目标日期（"YYYY-MM-DD"），默认今天

    Returns:
        dict: {"YYYY-MM-DD": {"uv_index": float, "heat_score": float, "is_provisional": bool, "source": str}, ...}
    """
    if not uv_history:
        return {}

    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    result = {}

    # 1. 历史真实数据
    for item in uv_history:
        d = item["date"]
        uv = item["uv_index"]
        result[d] = {
            "uv_index": uv,
            "heat_score": uv_to_heat_score(uv),
            "is_provisional": False,
            "source": "apppc_daily",
        }

    # 2. 计算最新一周平均值
    last_7 = uv_history[-7:] if len(uv_history) >= 7 else uv_history
    if last_7:
        week_avg = sum(item["uv_index"] for item in last_7) / len(last_7)
        week_avg_heat = uv_to_heat_score(week_avg)
    else:
        return result

    # 3. 填充从最新数据日期后一天到 target_date 的临时值
    latest_date_str = uv_history[-1]["date"]
    latest_date = datetime.strptime(latest_date_str, "%Y-%m-%d").date()
    target_dt = datetime.strptime(target_date, "%Y-%m-%d").date()

    current = latest_date + timedelta(days=1)
    while current <= target_dt:
        d_str = current.strftime("%Y-%m-%d")
        result[d_str] = {
            "uv_index": round(week_avg, 2),
            "heat_score": week_avg_heat,
            "is_provisional": True,
            "source": "week_avg",
        }
        current += timedelta(days=1)

    return result


def get_market_heat_for_date(market_heat_data: Dict[str, Dict], date_str: str) -> Dict:
    """
    从市场热度数据中获取指定日期的热度

    Args:
        market_heat_data: build_market_heat_data 返回的 dict
        date_str: 日期 "YYYY-MM-DD"

    Returns:
        dict: {"uv_index": float, "heat_score": float, "is_provisional": bool, "source": str}
            如果没有数据返回默认值
    """
    if date_str in market_heat_data:
        return market_heat_data[date_str]

    # 兜底：返回中性值
    mid_uv = (MARKET_HEAT_UV_LOW + MARKET_HEAT_UV_HIGH) / 2
    return {
        "uv_index": mid_uv,
        "heat_score": 50.0,
        "is_provisional": True,
        "source": "default",
    }


if __name__ == "__main__":
    # 测试
    print("测试市场热度采集...")
    history = fetch_uv_history(90)
    print(f"  获取到 {len(history)} 天数据")
    if history:
        print(f"  最新: {history[-1]['date']} UV={history[-1]['uv_index']:.2f}万")
        print(f"  最早: {history[0]['date']} UV={history[0]['uv_index']:.2f}万")

        today = datetime.now().strftime("%Y-%m-%d")
        data = build_market_heat_data(history, today)
        print(f"\n  今日({today}):")
        today_data = data.get(today, {})
        print(f"    UV指数: {today_data.get('uv_index', 0):.2f}万")
        print(f"    热度: {today_data.get('heat_score', 0):.1f}分")
        print(f"    临时值: {'是' if today_data.get('is_provisional') else '否'}")
        print(f"    来源: {today_data.get('source', '')}")

    close_browser()
