# -*- coding: utf-8 -*-
"""
热门股票列表获取
从东方财富股吧人气榜获取讨论活跃度最高的股票
"""
import time
import os
from typing import List, Dict
from bs4 import BeautifulSoup
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
from config.settings import HOT_STOCKS_COUNT, EXCLUDE_CODE_PREFIXES


def get_hot_stocks_from_gainers(count: int = None) -> List[Dict]:
    """
    从东方财富股吧人气榜获取热门股票（按讨论活跃度排序）

    Args:
        count: 获取数量，默认使用配置

    Returns:
        list of dict: 股票列表
    """
    if count is None:
        count = HOT_STOCKS_COUNT

    all_stocks = []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [ERROR] Playwright 未安装，无法获取热门股吧排行")
        return []

    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if not browsers_path:
        project_dir = str(__import__('pathlib').Path(__file__).parent.parent)
        browsers_path = os.path.join(project_dir, "browsers")
        if os.path.exists(browsers_path):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = context.new_page()

        # 先访问股吧首页获取 cookie
        try:
            page.goto("https://guba.eastmoney.com/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
        except Exception:
            pass

        # 计算需要几页（每页20只）
        pages_needed = (count + 19) // 20

        for pg in range(1, pages_needed + 1):
            if len(all_stocks) >= count:
                break

            if pg == 1:
                page.goto("https://guba.eastmoney.com/rank", wait_until="domcontentloaded", timeout=30000)
            else:
                # 点击翻页
                try:
                    page.click(f"text={pg}", timeout=5000)
                except Exception:
                    try:
                        page.click("text=下一页", timeout=5000)
                    except Exception:
                        break

            page.wait_for_timeout(2000)

            stocks = _parse_rank_table(page.content())
            for s in stocks:
                if len(all_stocks) >= count:
                    break
                # 过滤北交所
                if any(s["symbol"].startswith(prefix) for prefix in EXCLUDE_CODE_PREFIXES):
                    continue
                all_stocks.append(s)

            print(f"  人气榜第{pg}页: 获取 {len(stocks)} 只")

        browser.close()

    print(f"  ✓ 获取股吧人气榜 TOP{len(all_stocks)}")
    return all_stocks


def _parse_rank_table(html: str) -> List[Dict]:
    """解析排行榜表格"""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table:
        return []

    stocks = []
    rows = table.find_all("tr")

    for row in rows[1:]:  # 跳过表头
        cells = row.find_all("td")
        if len(cells) < 9:
            continue

        try:
            # 列3: 代码
            code_cell = cells[3]
            symbol = code_cell.get_text(strip=True)
            if not symbol or not symbol[0].isdigit():
                continue

            # 列4: 股票名称
            name_cell = cells[4]
            name = name_cell.get_text(separator="", strip=True)
            name = name.split("#")[0].split("讨论")[0].strip()
            name = name.replace(" ", "").replace("\u3000", "")

            # 列6: 最新价
            price = _safe_float(cells[6].get_text(strip=True))

            # 列7: 涨跌额
            change_amt = _safe_float(cells[7].get_text(strip=True))

            # 列8: 涨跌幅 (如 "10.02%")
            change_pct = _safe_float(cells[8].get_text(strip=True).replace("%", ""))

            exchange = _detect_exchange(symbol)
            full_code = _to_full_code(symbol, exchange)

            stocks.append({
                "code": full_code,
                "symbol": symbol,
                "name": name,
                "price": price,
                "change_percent": change_pct,
                "change_amount": change_amt,
                "volume": 0,
                "amount": 0,
                "turnover_rate": 0,
                "pe_ratio": 0,
                "market_cap": 0,
                "exchange": exchange,
            })
        except Exception:
            continue

    return stocks


def _safe_float(s: str) -> float:
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def _detect_exchange(code: str) -> str:
    if not code:
        return "sh"
    if code.startswith("6") or code.startswith("9") or code.startswith("5"):
        return "sh"
    elif code.startswith("0") or code.startswith("3") or code.startswith("1") or code.startswith("2"):
        return "sz"
    elif code.startswith("4") or code.startswith("8"):
        return "bj"
    return "sh"


def _to_full_code(code: str, exchange: str) -> str:
    prefix_map = {"sh": "SH", "sz": "SZ", "bj": "BJ"}
    return f"{prefix_map.get(exchange, 'SH')}{code}"


if __name__ == "__main__":
    print("正在获取股吧人气榜...")
    stocks = get_hot_stocks_from_gainers(30)
    for i, s in enumerate(stocks[:10], 1):
        print(f"{i:2d}. {s['name']}({s['code']})  价格: {s['price']}  涨幅: {s['change_percent']}%")
