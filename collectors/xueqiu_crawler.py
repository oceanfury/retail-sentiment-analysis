# -*- coding: utf-8 -*-
"""
雪球数据采集
使用 Playwright 浏览器注入 Cookie，拦截 API 响应获取讨论数据
"""
import time
import re
import json
from typing import List, Dict
from datetime import datetime
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
from config.settings import XUEQIU_COOKIE, XUEQIU_POST_COUNT, XUEQIU_REQUEST_DELAY


_playwright_available = None
_playwright_instance = None
_browser_instance = None
_context_instance = None
_page_instance = None


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


def _get_browser_and_page():
    """获取浏览器页面（懒加载，注入雪球 Cookie）"""
    global _playwright_instance, _browser_instance, _context_instance, _page_instance
    if _page_instance is not None:
        return _page_instance

    from playwright.sync_api import sync_playwright
    _playwright_instance = sync_playwright().start()
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

    # 注入雪球 Cookie
    xq_token = XUEQIU_COOKIE.get("xq_a_token", "")
    u_val = XUEQIU_COOKIE.get("u", "")
    if xq_token:
        _context_instance.add_cookies([
            {"name": "xq_a_token", "value": xq_token, "domain": ".xueqiu.com", "path": "/"},
            {"name": "u", "value": u_val, "domain": ".xueqiu.com", "path": "/"},
        ])

    _page_instance = _context_instance.new_page()

    # 先访问首页获取额外 Cookie
    try:
        _page_instance.goto("https://xueqiu.com/", wait_until="domcontentloaded", timeout=15000)
        _page_instance.wait_for_timeout(2000)
    except Exception:
        pass

    return _page_instance


def close_browser():
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


def get_hot_stocks_from_xueqiu(count: int = 30) -> List[Dict]:
    """
    从雪球热股榜获取沪深24小时最热股票

    Args:
        count: 获取数量

    Returns:
        list of dict: 股票列表（与 hot_stocks.py 格式一致）
    """
    if not XUEQIU_COOKIE.get("xq_a_token"):
        print("  [WARN] 雪球 Cookie 未配置，无法获取热股榜")
        return []

    if not _check_playwright():
        print("  [WARN] Playwright 未安装")
        return []

    try:
        page = _get_browser_and_page()
        api_url = f"https://stock.xueqiu.com/v5/stock/hot_stock/list.json?page=1&size={count}&_type=22&type=22&include=1"

        resp = page.request.get(api_url, headers={
            "Referer": "https://xueqiu.com/",
            "Accept": "application/json, text/plain, */*",
        }, timeout=15000)

        if resp.status != 200:
            print(f"  雪球热股 API 返回 {resp.status}")
            return []

        data = resp.json()
        items = data.get("data", {}).get("items", [])

        stocks = []
        for item in items:
            code = item.get("code", "")
            name = item.get("name", "")
            if not code or not name:
                continue

            # 雪球返回的 code 已经是带前缀的格式（如 SZ300413）
            symbol = code[2:] if len(code) > 2 else code
            exchange = "sh" if code.startswith("SH") else "sz"

            stocks.append({
                "code": code,
                "symbol": symbol,
                "name": name,
                "price": float(item.get("current", 0) or 0),
                "change_percent": float(item.get("percent", 0) or 0),
                "change_amount": float(item.get("chg", 0) or 0),
                "volume": 0,
                "amount": 0,
                "turnover_rate": 0,
                "pe_ratio": 0,
                "market_cap": 0,
                "exchange": exchange,
                "xueqiu_heat": float(item.get("value", 0) or 0),
            })

        print(f"  ✓ 获取雪球热股榜 TOP{len(stocks)}")
        return stocks

    except Exception as e:
        print(f"  [ERROR] 雪球热股榜获取失败: {e}")
        return []


def fetch_xueqiu_posts(stock_code: str, stock_name: str = "",
                       count: int = None, return_stock_info: bool = False):
    """
    抓取雪球个股讨论

    Args:
        stock_code: 股票代码（带市场前缀，如 SH600519）
        stock_name: 股票名称
        count: 抓取数量，默认使用配置
        return_stock_info: 是否同时返回股票基本信息（关注量、股价等）

    Returns:
        list of dict: 帖子列表（return_stock_info=False 时）
        tuple: (posts, stock_info) 元组（return_stock_info=True 时）
    """
    if count is None:
        count = XUEQIU_POST_COUNT

    if not XUEQIU_COOKIE.get("xq_a_token"):
        print(f"  [WARN] 雪球 Cookie 未配置，跳过 {stock_name}({stock_code})")
        if return_stock_info:
            return [], {"stock_code": stock_code, "stock_name": stock_name,
                         "stock_followers": 0, "stock_price": 0, "change_percent": 0}
        return []

    if not _check_playwright():
        print(f"  [WARN] Playwright 未安装，跳过雪球 {stock_name}({stock_code})")
        if return_stock_info:
            return [], {"stock_code": stock_code, "stock_name": stock_name,
                         "stock_followers": 0, "stock_price": 0, "change_percent": 0}
        return []

    all_posts = []
    stock_followers = 0
    stock_price = 0
    change_percent = 0
    xq_symbol = stock_code.upper()
    stock_url = f"https://xueqiu.com/S/{xq_symbol}"

    try:
        page = _get_browser_and_page()

        # 用 response 监听拦截 API 响应
        api_responses = []

        def handle_response(response):
            url = response.url
            if "statuses/search" in url or "symbol/search" in url:
                try:
                    api_responses.append(response.json())
                except:
                    pass

        page.on("response", handle_response)

        # 访问个股页面
        page.goto(stock_url, wait_until="domcontentloaded", timeout=20000)
        # 等待页面加载和 API 调用
        page.wait_for_timeout(5000)

        # 从页面 DOM 提取关注量和股价
        stock_price = 0
        change_percent = 0
        if not stock_followers:
            try:
                vals = page.evaluate("""() => {
                    let followers = 0, price = 0, chg = 0;
                    // 关注量
                    const els = document.querySelectorAll('*');
                    for (const el of els) {
                        const text = el.textContent || '';
                        const m = text.match(/(\\d+)人关注了该股票/);
                        if (m) { followers = parseInt(m[1]); break; }
                    }
                    if (!followers) {
                        const scripts = document.querySelectorAll('script');
                        for (const s of scripts) {
                            const text = s.textContent || '';
                            const m = text.match(/"followerText"\\s*:\\s*"(\\d[\\d.]+)\\s*万/);
                            if (m) { followers = Math.round(parseFloat(m[1]) * 10000); break; }
                        }
                    }
                    // 股价
                    const priceEl = document.querySelector('.stock-current');
                    if (priceEl) {
                        price = parseFloat(priceEl.textContent.replace(/[^0-9.]/g, '')) || 0;
                    }
                    if (!price) {
                        const scripts2 = document.querySelectorAll('script');
                        for (const s of scripts2) {
                            const text = s.textContent || '';
                            const m = text.match(/"current"\\s*:\\s*(\\d+\\.?\\d*)/);
                            if (m) { price = parseFloat(m[1]); break; }
                        }
                    }
                    // 涨跌幅
                    const chgEl = document.querySelector('.stock-change');
                    if (chgEl) {
                        const t = chgEl.textContent || '';
                        const m = t.match(/(-?\\d+\\.?\\d*)\\s*%/);
                        if (m) chg = parseFloat(m[1]);
                    }
                    if (!chg) {
                        const scripts3 = document.querySelectorAll('script');
                        for (const s of scripts3) {
                            const text = s.textContent || '';
                            const m = text.match(/"percent"\\s*:\\s*(-?\\d+\\.?\\d*)/);
                            if (m) { chg = parseFloat(m[1]); break; }
                        }
                    }
                    return {followers, price, chg};
                }""")
                stock_followers = vals.get("followers", 0)
                stock_price = vals.get("price", 0)
                change_percent = vals.get("chg", 0)
            except:
                pass

        if stock_followers:
            print(f"  雪球 {stock_name} 关注量: {stock_followers}")
        if stock_price:
            print(f"  雪球 {stock_name} 股价: {stock_price} ({'+' if change_percent >= 0 else ''}{change_percent:.2f}%)")

        page.remove_listener("response", handle_response)

        # 解析拦截到的数据
        for data in api_responses:
            posts_raw = data.get("list") or data.get("statuses") or []
            if posts_raw:
                posts = _normalize_posts(posts_raw, stock_code, stock_name)
                all_posts.extend(posts)

        # 将关注量和股价写入每条帖子
        for p in all_posts:
            p["stock_followers"] = stock_followers
            p["stock_price"] = stock_price
            p["change_percent"] = change_percent

        if all_posts:
            print(f"  ✓ 雪球 {stock_name}({stock_code}): {len(all_posts)} 条讨论")
        else:
            # 备选方案：解析页面 HTML
            print(f"  雪球: API 无数据，尝试解析页面")
            posts = _parse_page_html(page.content(), stock_code, stock_name)
            if posts:
                all_posts.extend(posts)
                print(f"  ✓ 雪球 {stock_name}({stock_code}): {len(posts)} 条（页面解析）")
            else:
                print(f"  雪球 {stock_name}({stock_code}): 0 条讨论")

    except Exception as e:
        print(f"  [ERROR] 雪球抓取失败: {e}")

    # 过滤：只保留当天帖子
    today_str = datetime.now().strftime("%Y-%m-%d")
    before_count = len(all_posts)
    all_posts = [p for p in all_posts if not p.get("publish_time") or p["publish_time"][:10] == today_str]
    filtered_count = before_count - len(all_posts)
    if filtered_count > 0:
        print(f"  (过滤 {filtered_count} 条非当日帖子，剩余 {len(all_posts)} 条)")

    if return_stock_info:
        stock_info = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "stock_followers": stock_followers,
            "stock_price": stock_price,
            "change_percent": change_percent,
        }
        return all_posts, stock_info
    return all_posts


def _parse_page_html(html: str, stock_code: str, stock_name: str) -> List[Dict]:
    """从页面 HTML 解析帖子（备选方案）"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    posts = []

    # 雪球讨论列表
    items = soup.select("div.timeline-item, div.status-item, article.status")
    for item in items:
        try:
            # 正文
            content_div = item.select_one("div.status-content, div.text, p.text")
            content = content_div.get_text(strip=True) if content_div else ""

            # 作者
            author_div = item.select_one("a.user-name, span.name")
            author = author_div.get_text(strip=True) if author_div else "匿名"

            # 过滤新闻媒体和公司公告
            if _is_media_or_announcement(author, stock_name):
                continue

            # 时间
            time_div = item.select_one("span.time, time")
            time_str = time_div.get_text(strip=True) if time_div else ""

            posts.append({
                "source": "xueqiu",
                "stock_code": stock_code,
                "stock_name": stock_name,
                "post_id": "",
                "title": content[:50] if content else "",
                "content": content,
                "author": author,
                "author_followers": 0,
                "publish_time": time_str,
                "publish_timestamp": 0,
                "read_count": 0,
                "comment_count": 0,
                "like_count": 0,
                "retweet_count": 0,
                "url": "",
                "source_platform": "xueqiu_web",
            })
        except:
            continue

    return posts


# 新闻媒体账号名单
_XUEQIU_MEDIA_ACCOUNTS = {
    "证券日报", "中国证券报", "上海证券报", "证券时报", "每日经济新闻",
    "新浪财经", "东方财富网", "财联社", "界面新闻", "21世纪经济报道",
    "第一财经", "经济日报", "证券市场红周刊", "金融界", "同花顺",
    "雪球公告", "雪球活动", "雪球问问", "雪球路演", "雪球访谈",
    "经济观察报", "华夏时报", "证券之星", "和讯网", "格隆汇",
}

# 媒体账号后缀
_MEDIA_SUFFIXES = ["资讯", "新闻", "快报", "观察", "时报", "日报", "周报", "财经", "金融"]


def _is_media_or_announcement(author: str, stock_name: str = "") -> bool:
    """判断是否为新闻媒体账号或公司公告号"""
    if not author or author == "匿名用户":
        return False

    # 精确匹配媒体名单
    if author in _XUEQIU_MEDIA_ACCOUNTS:
        return True

    # 后缀匹配
    for suffix in _MEDIA_SUFFIXES:
        if author.endswith(suffix) and len(author) >= 4:
            return True

    # 公司公告号：用户名是"公司名(交易所代码)"格式，例如 "中创智领(SH601717)"
    # 或用户名与股票名高度相关且包含括号代码
    if re.search(r"\([A-Z]{2}\d{6}\)", author):
        # 检查是否包含股票名或类似公司名称
        if stock_name and stock_name in author:
            return True
        # 纯"名称(SH/SZ+6位数字)"格式，基本都是官方账号
        if re.match(r"^.{2,15}\([A-Z]{2}\d{6}\)$", author):
            return True

    # 包含"公告"关键词
    if "公告" in author:
        return True

    return False


def _normalize_posts(posts_raw: List[Dict], stock_code: str, stock_name: str) -> List[Dict]:
    """标准化雪球帖子数据"""
    normalized = []
    filtered_media = 0

    for post in posts_raw:
        try:
            user = post.get("user", {}) or {}
            author = user.get("screen_name", "匿名用户")

            # 过滤新闻媒体和公司公告
            if _is_media_or_announcement(author, stock_name):
                filtered_media += 1
                continue

            html_text = post.get("text", "") or post.get("description", "")
            plain_text = _strip_html(html_text)

            title = post.get("title", "") or ""
            title = _strip_html(title)

            author_followers = user.get("followers_count", 0)

            created_at = post.get("created_at", 0)
            time_str = _format_timestamp(created_at)

            view_count = post.get("view_count", 0) or post.get("views_count", 0)
            like_count = post.get("like_count", 0) or post.get("like_num", 0)
            reply_count = post.get("reply_count", 0) or post.get("reply_num", 0)
            retweet_count = post.get("retweet_count", 0) or post.get("retweet_num", 0)

            normalized.append({
                "source": "xueqiu",
                "stock_code": stock_code,
                "stock_name": stock_name,
                "post_id": str(post.get("id", "")),
                "title": title if title else plain_text[:50],
                "content": plain_text,
                "author": author,
                "author_followers": author_followers,
                "publish_time": time_str,
                "publish_timestamp": created_at,
                "read_count": view_count,
                "comment_count": reply_count,
                "like_count": like_count,
                "retweet_count": retweet_count,
                "url": f"https://xueqiu.com/{user.get('id', '')}/{post.get('id', '')}",
                "source_platform": post.get("source", ""),
            })
        except Exception as e:
            continue

    if filtered_media > 0:
        print(f"    (过滤 {filtered_media} 条媒体/公告帖子)")

    return normalized


def _strip_html(text: str) -> str:
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", "", text)
    clean = clean.replace("&nbsp;", " ").replace("&amp;", "&")
    clean = clean.replace("&lt;", "<").replace("&gt;", ">")
    clean = clean.replace("&quot;", '"').replace("&#39;", "'")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _format_timestamp(timestamp: int) -> str:
    if not timestamp:
        return ""
    try:
        if timestamp > 10000000000:
            timestamp = timestamp / 1000
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""
