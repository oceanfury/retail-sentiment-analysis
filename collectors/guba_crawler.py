# -*- coding: utf-8 -*-
"""
东方财富股吧数据采集 - Playwright 版本
使用 Playwright 渲染页面，绕过反爬机制
"""
import time
import re
import json
from typing import List, Dict
from datetime import datetime
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
from config.settings import GUBA_PAGE_COUNT, GUBA_REQUEST_DELAY

_playwright_available = None
_playwright_instance = None
_browser_instance = None
_context_instance = None
_page_instance = None


def _check_playwright() -> bool:
    """检查 Playwright 是否可用"""
    global _playwright_available
    if _playwright_available is not None:
        return _playwright_available
    try:
        from playwright.sync_api import sync_playwright
        _playwright_available = True
    except ImportError:
        _playwright_available = False
    return _playwright_available


def _get_browser_and_page():
    """获取浏览器和页面实例（懒加载，共享 context 和 page）"""
    global _playwright_instance, _browser_instance, _context_instance, _page_instance
    if _page_instance is not None:
        return _page_instance

    from playwright.sync_api import sync_playwright
    _playwright_instance = sync_playwright().start()
    _browser_instance = _playwright_instance.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ]
    )
    _context_instance = _browser_instance.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
        locale="zh-CN",
    )
    # 注入 stealth 脚本
    _context_instance.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
        window.chrome = { runtime: {} };
    """)

    _page_instance = _context_instance.new_page()

    # 先访问首页，获取必要的 cookie
    try:
        _page_instance.goto("https://guba.eastmoney.com/", wait_until="domcontentloaded", timeout=30000)
        _page_instance.wait_for_timeout(2000)
    except Exception:
        pass

    return _page_instance


def close_browser():
    """关闭浏览器和 Playwright（彻底释放 asyncio 事件循环）"""
    global _playwright_instance, _browser_instance, _context_instance, _page_instance
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


def _fetch_stock_quote(stock_code: str) -> tuple:
    """通过东方财富行情 API 获取实时股价和涨跌幅"""
    try:
        from curl_cffi import requests as cffi_req
        if stock_code.startswith('6') or stock_code.startswith('9'):
            secid = f"1.{stock_code}"
        else:
            secid = f"0.{stock_code}"
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f43,f170"
        resp = cffi_req.get(url, timeout=5, impersonate="chrome120",
                            headers={"Referer": "https://quote.eastmoney.com/"})
        data = resp.json().get("data", {})
        price = data.get("f43", 0)
        chg = data.get("f170", 0)
        if price:
            price = price / 100
        if chg:
            chg = chg / 100
        return (round(price, 2) if price else 0, round(chg, 2) if chg else 0)
    except Exception:
        return (0, 0)


def fetch_guba_posts(stock_code: str, stock_name: str = "",
                     page_count: int = None) -> List[Dict]:
    """
    抓取东方财富股吧帖子列表

    Args:
        stock_code: 股票代码（纯数字，如 600519）
        stock_name: 股票名称
        page_count: 抓取页数，默认使用配置

    Returns:
        list of dict: 帖子列表
    """
    if not _check_playwright():
        print(f"  [WARN] Playwright 未安装，跳过股吧 {stock_name}({stock_code})")
        print("         安装命令: pip install playwright && playwright install chromium")
        return []

    if page_count is None:
        page_count = GUBA_PAGE_COUNT

    all_posts = []
    stock_price = 0
    change_percent = 0

    try:
        page = _get_browser_and_page()

        for p in range(1, page_count + 1):
            url = f"https://guba.eastmoney.com/list,{stock_code}_{p}.html"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # 等待列表加载
                try:
                    page.wait_for_selector("tr.listitem", timeout=10000)
                except:
                    pass
                page.wait_for_timeout(1500)

                # 第一页时通过行情API获取股价和涨跌幅
                if p == 1 and not stock_price:
                    stock_price, change_percent = _fetch_stock_quote(stock_code)

                html_content = page.content()

                # 解析页面内容
                posts = _parse_page_content(html_content, stock_code, stock_name)

                # 过滤：只保留当天帖子
                today_str = datetime.now().strftime("%Y-%m-%d")
                today_posts = []
                page_today_count = 0
                for post in posts:
                    pt = post.get("publish_time", "")
                    if not pt:
                        # 时间未知，保留
                        today_posts.append(post)
                    elif pt[:10] == today_str:
                        today_posts.append(post)
                        page_today_count += 1
                old_count = len(posts) - len(today_posts)
                if old_count > 0:
                    print(f"    (过滤 {old_count} 条非当日帖子)")
                posts = today_posts
                all_posts.extend(posts)

                print(f"  第{p}页: 获取 {len(posts)} 条当日帖子")

                # 当页无当日帖子时停止翻页（第1页除外）
                if page_today_count == 0 and p > 1:
                    print(f"  当日帖子采集完毕，停止翻页")
                    break

            except Exception as e:
                print(f"  [ERROR] 第{p}页抓取失败: {e}")
                continue

            time.sleep(GUBA_REQUEST_DELAY)

    except Exception as e:
        print(f"  [ERROR] 股吧爬虫异常: {e}")
        import traceback
        traceback.print_exc()

    # 采集完帖子后，访问排名页面获取真实人气排名
    rank = _fetch_rank_for_stock(page, stock_code)
    if rank:
        print(f"  股吧人气排名: 第{rank}名")

    # 将股价写入每条帖子
    for p in all_posts:
        p["stock_price"] = stock_price
        p["change_percent"] = change_percent

    if stock_price:
        print(f"  股吧 {stock_name} 股价: {stock_price} ({'+' if change_percent >= 0 else ''}{change_percent:.2f}%)")
    print(f"  ✓ 股吧 {stock_name}({stock_code}): 共 {len(all_posts)} 条帖子")
    return all_posts


_stock_ranks = {}


def _fetch_rank_for_stock(page, stock_code: str):
    """访问股吧人气排名页面，提取该股票的真实排名"""
    try:
        url = f"https://guba.eastmoney.com/rank/stock?code={stock_code}"
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1000)
        rank_elem = page.query_selector("span.ranknum")
        if rank_elem:
            rank_text = rank_elem.inner_text().strip()
            rank = int(rank_text)
            _stock_ranks[stock_code] = rank
            return rank
    except Exception:
        pass
    return 0


def get_stock_rank(stock_code: str) -> int:
    return _stock_ranks.get(stock_code, 0)


_SYSTEM_AUTHOR_SUFFIXES = ["资讯", "新闻", "公告", "研报", "数据宝", "导读"]
_SYSTEM_AUTHOR_EXACT = {
    "东方财富网", "股吧导读", "同花顺", "新浪财经", "证券时报",
    "中国证券报", "上海证券报", "证券日报", "经济日报",
    "财联社", "界面新闻", "21世纪经济报道", "第一财经",
}


def _is_system_author(author: str, stock_name: str = "") -> bool:
    """检测是否为系统/新闻机器人用户"""
    if not author or author == "匿名":
        return False
    if author in _SYSTEM_AUTHOR_EXACT:
        return True
    for suffix in _SYSTEM_AUTHOR_SUFFIXES:
        if author.endswith(suffix):
            return True
    return False


def _parse_page_content(html: str, stock_code: str, stock_name: str) -> List[Dict]:
    """解析页面内容提取帖子"""
    from bs4 import BeautifulSoup

    posts = []
    filtered_count = 0
    soup = BeautifulSoup(html, "lxml")

    selectors = [
        "tr.listitem",
        "div.articleh",
        "div.listitem",
        "tr.list_item",
        "div.post_list div.post_item",
        "div#articlelist div.articleh",
    ]

    items = []
    for sel in selectors:
        items = soup.select(sel)
        if items:
            break

    if not items:
        items = soup.find_all("tr")
        if len(items) > 20:
            items = []

    for item in items:
        try:
            post = _parse_post_item(item, stock_code, stock_name)
            if post and post.get("title"):
                if _is_system_author(post.get("author", ""), stock_name):
                    filtered_count += 1
                    continue
                posts.append(post)
        except Exception:
            continue

    if filtered_count > 0:
        print(f"    (已过滤 {filtered_count} 条系统/新闻帖子)")

    return posts


def _parse_post_item(item, stock_code: str, stock_name: str) -> Dict:
    """解析单条帖子"""
    post = {
        "source": "guba",
        "stock_code": stock_code,
        "stock_name": stock_name,
    }

    # 标题和链接（优先 div.title a，再找 /news/ 链接）
    title_elem = item.select_one("div.title a") or item.select_one("span.l3 a") or item.select_one("a.note") or item.select_one("a.title")
    if not title_elem:
        # 尝试所有 a 标签
        all_a = item.find_all("a", href=True)
        for a in all_a:
            href = a.get("href", "")
            if ("/news," in href or "/news/" in href) and a.get_text(strip=True):
                # 排除财富号链接（caifuhao）
                if "caifuhao" not in href:
                    title_elem = a
                    break

    if title_elem:
        post["title"] = title_elem.get_text(strip=True)
        href = title_elem.get("href", "")
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = f"https://guba.eastmoney.com{href}"
        post["url"] = href
        # 提取帖子ID
        match = re.search(r"/news,(\w+)\.html", href)
        if match:
            post["post_id"] = match.group(1)
    else:
        return None

    # 阅读量
    read_elem = item.select_one("div.read") or item.select_one("span.l1") or item.select_one("span.read") or item.find("span", class_=re.compile(r"read|view|l1"))
    if read_elem:
        post["read_count"] = _parse_number(read_elem.get_text(strip=True))
    else:
        post["read_count"] = 0

    # 评论数
    comment_elem = item.select_one("div.reply") or item.select_one("span.l2") or item.select_one("span.reply") or item.find("span", class_=re.compile(r"reply|comment|l2"))
    if comment_elem:
        post["comment_count"] = _parse_number(comment_elem.get_text(strip=True))
    else:
        post["comment_count"] = 0

    # 作者
    author_elem = item.select_one("div.author a") or item.select_one("span.l4 a") or item.select_one("a.author") or item.find("a", class_=re.compile(r"author|user|l4"))
    if author_elem:
        post["author"] = author_elem.get_text(strip=True)
    else:
        # 从用户主页链接提取
        all_a = item.find_all("a", href=True)
        for a in all_a:
            href = a.get("href", "")
            if "i.eastmoney.com" in href or "/u/" in href:
                post["author"] = a.get_text(strip=True)
                break
        else:
            post["author"] = "匿名"

    # 发布时间
    time_elem = item.select_one("div.update") or item.select_one("div.mod_time") or item.select_one("span.l5") or item.select_one("span.time")
    if time_elem:
        time_text = time_elem.get_text(strip=True)
        post["publish_time"] = _normalize_time(time_text)
        post["publish_time_str"] = time_text
    else:
        post["publish_time"] = ""
        post["publish_time_str"] = ""

    # 股吧帖子列表页没有正文，内容用标题代替（后续可以加详情页抓取）
    post["content"] = post["title"]

    return post


def _parse_number(text: str) -> int:
    """解析数字，支持万、亿等单位"""
    if not text:
        return 0
    text = text.strip().replace(",", "")
    try:
        if "万" in text:
            return int(float(text.replace("万", "")) * 10000)
        elif "亿" in text:
            return int(float(text.replace("亿", "")) * 100000000)
        else:
            return int(float(text))
    except (ValueError, TypeError):
        return 0


def _normalize_time(time_text: str) -> str:
    """规范化时间格式"""
    now = datetime.now()

    # 格式: 08-31 14:30
    match = re.match(r"(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", time_text)
    if match:
        month, day, hour, minute = match.groups()
        year = now.year
        if int(month) > now.month:
            year -= 1
        return f"{year}-{month}-{day} {hour}:{minute}:00"

    if "今天" in time_text:
        match = re.search(r"(\d{2}):(\d{2})", time_text)
        if match:
            hour, minute = match.groups()
            return now.strftime(f"%Y-%m-%d {hour}:{minute}:00")

    if "昨天" in time_text:
        match = re.search(r"(\d{2}):(\d{2})", time_text)
        if match:
            hour, minute = match.groups()
            from datetime import timedelta
            yesterday = now - timedelta(days=1)
            return yesterday.strftime(f"%Y-%m-%d {hour}:{minute}:00")

    return time_text


if __name__ == "__main__":
    print("测试股吧爬虫 (Playwright 版本)...")
    if not _check_playwright():
        print("Playwright 不可用，跳过测试")
        print("请先安装: pip install playwright && playwright install chromium")
    else:
        posts = fetch_guba_posts("600519", "贵州茅台", page_count=2)
        for i, p in enumerate(posts[:5], 1):
            print(f"\n{i}. {p.get('title', '')[:60]}")
            print(f"   作者: {p.get('author', '')}  阅读: {p.get('read_count', 0)}  评论: {p.get('comment_count', 0)}")
        close_browser()
