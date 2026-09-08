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

    # 补充缺失的股票名称和价格（页面JS动态加载，初始HTML可能为空）
    missing = [s["symbol"] for s in all_stocks if not s.get("name") or not s.get("price")]
    if missing:
        print(f"  📡 从API补充 {len(missing)} 只股票的名称和价格...")
        api_data = _fetch_stock_info_batch(missing)
        for s in all_stocks:
            info = api_data.get(s["symbol"])
            if info:
                if not s.get("name"):
                    s["name"] = info["name"]
                if not s.get("price"):
                    s["price"] = info["price"]
                if not s.get("change_percent"):
                    s["change_percent"] = info["change_percent"]
        filled = sum(1 for s in all_stocks if s.get("name"))
        print(f"  ✓ 名称补充完成: {filled}/{len(all_stocks)}")

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

            # 列4: 股票名称（JS动态填充，可能为空）
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


def _fetch_stock_info_batch(symbols: List[str]) -> Dict:
    """通过腾讯财经API批量获取股票名称、价格、涨跌幅"""
    if not symbols:
        return {}

    try:
        import requests as req
    except ImportError:
        return {}

    result = {}
    headers = {"User-Agent": "Mozilla/5.0"}

    for symbol in symbols:
        if symbol.startswith("6") or symbol.startswith("9") or symbol.startswith("5"):
            prefix = "sh"
        else:
            prefix = "sz"

        try:
            url = f"https://qt.gtimg.cn/q={prefix}{symbol}"
            resp = req.get(url, headers=headers, timeout=5)
            text = resp.text

            import re
            match = re.search(r'"(.+?)"', text)
            if not match:
                result[symbol] = {"name": "", "price": 0.0, "change_percent": 0.0}
                continue

            fields = match.group(1).split("~")
            if len(fields) < 35:
                result[symbol] = {"name": "", "price": 0.0, "change_percent": 0.0}
                continue

            name = fields[1]
            price = float(fields[3]) if fields[3] else 0
            change_percent = float(fields[32]) if fields[32] else 0

            result[symbol] = {
                "name": name if name else "",
                "price": round(price, 2) if price else 0.0,
                "change_percent": round(change_percent, 2) if change_percent else 0.0,
            }
            time.sleep(0.05)
        except Exception:
            result[symbol] = {"name": "", "price": 0.0, "change_percent": 0.0}

    return result


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
