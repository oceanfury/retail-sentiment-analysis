# -*- coding: utf-8 -*-
"""
雪球数据采集
使用 Playwright 浏览器注入 Cookie，在页面上下文内调用 API 获取讨论数据

采集策略：
- 雪球按 IP 限流，实测约 4 次请求 / 30 秒，超限返回 400 或 JS 挑战页
- 因此所有请求统一走 _wait_for_slot() 排队，页面导航自身发出的 XHR 也计入账本
- 每只股票主动翻页直到覆盖当日，而不是只被动拦截页面首屏那 10 条
"""
import time
import re
import json
import random
from collections import deque
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))
from config import settings as _settings
from config.settings import XUEQIU_COOKIE, XUEQIU_POST_COUNT

# 新增配置项用 getattr 兜底，兼容未更新的本地 settings.py
XUEQIU_PAGE_SIZE = getattr(_settings, "XUEQIU_PAGE_SIZE", 20)          # 服务端单页硬上限
XUEQIU_MAX_PAGES = getattr(_settings, "XUEQIU_MAX_PAGES", 2)           # 自选股翻页上限
XUEQIU_HOT_MAX_PAGES = getattr(_settings, "XUEQIU_HOT_MAX_PAGES", 1)   # 热股翻页上限
XUEQIU_BACKFILL_MAX_PAGES = getattr(_settings, "XUEQIU_BACKFILL_MAX_PAGES", 6)  # 按日期补采的翻页上限
XUEQIU_RATE_LIMIT_MAX_REQUESTS = getattr(_settings, "XUEQIU_RATE_LIMIT_MAX_REQUESTS", 4)
XUEQIU_RATE_LIMIT_WINDOW = getattr(_settings, "XUEQIU_RATE_LIMIT_WINDOW", 30.0)
XUEQIU_MIN_REQUEST_INTERVAL = getattr(_settings, "XUEQIU_MIN_REQUEST_INTERVAL", 8.0)
XUEQIU_REQUEST_JITTER = getattr(_settings, "XUEQIU_REQUEST_JITTER", 1.5)
XUEQIU_RATE_LIMIT_RETRY_WAIT = getattr(_settings, "XUEQIU_RATE_LIMIT_RETRY_WAIT", 35.0)
XUEQIU_MAX_RATE_LIMIT_RETRIES = getattr(_settings, "XUEQIU_MAX_RATE_LIMIT_RETRIES", 2)
XUEQIU_RATE_LIMIT_SLOWDOWN = getattr(_settings, "XUEQIU_RATE_LIMIT_SLOWDOWN", 1.5)
XUEQIU_MAX_REQUEST_INTERVAL = getattr(_settings, "XUEQIU_MAX_REQUEST_INTERVAL", 20.0)
XUEQIU_COLLECT_TIME_BUDGET = getattr(_settings, "XUEQIU_COLLECT_TIME_BUDGET", 1200.0)
XUEQIU_PAGE_RENDER_WAIT = getattr(_settings, "XUEQIU_PAGE_RENDER_WAIT", 5.0)


_playwright_available = None
_playwright_instance = None
_browser_instance = None
_context_instance = None
_page_instance = None


# ============== 采集状态 ==============
STATUS_OK = "ok"                          # 正常（0 条 = 当日确实没人讨论）
STATUS_PARTIAL = "partial"                # 部分成功（被限流截断）
STATUS_RATE_LIMITED = "rate_limited"      # 被限流，未采到数据
STATUS_ERROR = "error"                    # 挑战页 / 解析失败 / 异常
STATUS_BUDGET = "budget_limited"          # 时间预算耗尽
STATUS_NO_COOKIE = "no_cookie"
STATUS_NO_PLAYWRIGHT = "no_playwright"

_STATUS_TEXT = {
    STATUS_OK: "采集正常",
    STATUS_PARTIAL: "部分成功（被限流截断）",
    STATUS_RATE_LIMITED: "被限流，未采到数据",
    STATUS_ERROR: "采集出错",
    STATUS_BUDGET: "时间预算耗尽",
    STATUS_NO_COOKIE: "Cookie 未配置",
    STATUS_NO_PLAYWRIGHT: "Playwright 不可用",
}


# ============== 请求节流 ==============
_rate_window = deque(maxlen=64)     # 最近请求时间戳
_current_interval = XUEQIU_MIN_REQUEST_INTERVAL
_collect_deadline = None            # 本轮采集的墙钟截止时间
_collect_records = {}               # stock_code -> 采集记录


def _ensure_run_state():
    """懒初始化本轮采集状态"""
    global _collect_deadline
    if _collect_deadline is None:
        _collect_deadline = time.time() + XUEQIU_COLLECT_TIME_BUDGET


def _time_budget_left() -> float:
    """本轮采集剩余预算秒数"""
    if _collect_deadline is None:
        return XUEQIU_COLLECT_TIME_BUDGET
    return _collect_deadline - time.time()


def _wait_for_slot():
    """
    发请求前排队，返回时保证现在可以发。

    两道闸：滑动窗口计数（挡住页面自身 XHR 的突发）+ 最小间隔。
    """
    global _current_interval
    while True:
        now = time.time()
        recent = [t for t in _rate_window if now - t < XUEQIU_RATE_LIMIT_WINDOW]

        # 窗口内次数达上限 -> 睡到最早一次滑出窗口
        if len(recent) >= XUEQIU_RATE_LIMIT_MAX_REQUESTS:
            wait = XUEQIU_RATE_LIMIT_WINDOW - (now - recent[0]) + 0.5
            print(f"    [节流] {int(XUEQIU_RATE_LIMIT_WINDOW)}秒窗口内已 {len(recent)} 次请求，等待 {wait:.1f}s")
            time.sleep(max(wait, 0.5))
            continue

        # 距上次请求不足最小间隔 -> 补足并加抖动
        if recent:
            gap = now - recent[-1]
            if gap < _current_interval:
                time.sleep(_current_interval - gap + random.uniform(0, XUEQIU_REQUEST_JITTER))
                continue

        break
    _rate_window.append(time.time())


def _note_rate_limited():
    """命中限流：全局放慢，避免后续请求继续撞墙"""
    global _current_interval
    old = _current_interval
    _current_interval = min(_current_interval * XUEQIU_RATE_LIMIT_SLOWDOWN,
                            XUEQIU_MAX_REQUEST_INTERVAL)
    if _current_interval > old:
        print(f"    [限流] 已触发限流，请求间隔 {old:.1f}s -> {_current_interval:.1f}s")


def get_collect_records() -> Dict[str, Dict]:
    """返回本轮雪球采集的逐股记录（副本）"""
    return {k: dict(v) for k, v in _collect_records.items()}


def reset_collect_records():
    """清空采集记录与时间预算"""
    global _collect_deadline, _current_interval
    _collect_records.clear()
    _collect_deadline = None
    _current_interval = XUEQIU_MIN_REQUEST_INTERVAL
    _rate_window.clear()


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

    # 先访问首页获取额外 Cookie（也占用一个限流配额，需记账）
    try:
        _rate_window.append(time.time())
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

    # 本轮采集结束，复位节流状态与时间预算（保留记录供调用方读取）
    global _collect_deadline, _current_interval
    _collect_deadline = None
    _current_interval = XUEQIU_MIN_REQUEST_INTERVAL


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

        # 热股榜走 stock.xueqiu.com，与行情接口共用同一 IP 配额，需计入账本
        _wait_for_slot()
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


def _build_page_url(symbol: str, page: int, page_size: int) -> str:
    """
    构造讨论列表 API 的 URL（实测无需任何签名参数）

    必须用 www.xueqiu.com：不带 www 的 xueqiu.com 会被阿里云 WAF 无条件拦截，
    返回 200 + 挑战页而不是 JSON，且与限流无关。
    """
    return (
        "https://www.xueqiu.com/query/v1/symbol/search/status.json"
        f"?count={page_size}&comment=0&symbol={symbol}&hl=0"
        f"&source=all&sort=time&page={page}&q=&type=11"
    )


def _fetch_api_in_page(page, url: str, timeout: float = 20.0):
    """
    在页面上下文内用 fetch 取 JSON。

    直连 page.request.get() 会被雪球 WAF 挡（返回挑战页而非 JSON），
    必须走页面自身的会话。返回 (status, text)。
    """
    js = """
        async ([url, timeoutMs]) => {
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), timeoutMs);
            try {
                const r = await fetch(url, {
                    credentials: 'include',
                    headers: {'Accept': 'application/json, text/plain, */*'},
                    signal: ctrl.signal,
                });
                const text = await r.text();
                return {status: r.status, text: text};
            } catch (e) {
                return {status: 0, text: 'FETCH_ERROR: ' + e};
            } finally {
                clearTimeout(timer);
            }
        }
    """
    try:
        res = page.evaluate(js, [url, int(timeout * 1000)])
        return res.get("status", 0), res.get("text", "")
    except Exception as e:
        return 0, f"EVALUATE_ERROR: {e}"


def _is_rate_limited(status: int, text: str) -> bool:
    """判定响应是否为限流（400 或 WAF 挑战页）"""
    if status == 400:
        return True
    if status != 200:
        return False
    head = (text or "")[:200]
    return "_waf_" in head or head.lstrip().startswith("<")


def _dedupe_posts(posts: List[Dict]) -> List[Dict]:
    """按 post_id 去重（翻页结果与导航兜底数据会重叠）"""
    seen = set()
    out = []
    for p in posts:
        key = str(p.get("post_id") or "") or str(p.get("url") or "")
        if not key:
            key = f"{p.get('title', '')}|{p.get('publish_time', '')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _raw_post_date(post: Dict) -> Optional[str]:
    """原始帖子的发布日期（created_at 为毫秒时间戳），解析不出返回 None"""
    ts = post.get("created_at")
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    except (ValueError, OSError, TypeError):
        return None


def _raw_date_count(posts_raw: List[Dict], iso_date: str) -> int:
    """统计原始帖子中属于指定日期的条数"""
    return sum(1 for p in posts_raw if _raw_post_date(p) == iso_date)


def _raw_max_date(posts_raw: List[Dict]) -> Optional[str]:
    """原始帖子中最新的一条日期；列表按时间倒序，即本页第一条的日期"""
    dates = [d for d in (_raw_post_date(p) for p in posts_raw) if d]
    return max(dates) if dates else None


def _record_collect(stock_code: str, stock_name: str, status: str, **extra):
    """写入一只股票的采集记录"""
    rec = {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "status": status,
        "status_text": _STATUS_TEXT.get(status, status),
    }
    rec.update(extra)
    _collect_records[stock_code] = rec


def fetch_xueqiu_posts(stock_code: str, stock_name: str = "",
                       count: int = None, return_stock_info: bool = False,
                       max_pages: int = None, target_date: str = None):
    """
    抓取雪球个股讨论

    Args:
        stock_code: 股票代码（带市场前缀，如 SH600519）
        stock_name: 股票名称
        count: 目标帖子条数上限，默认使用配置
        return_stock_info: 是否同时返回股票基本信息（关注量、股价等）
        max_pages: 翻页上限，默认取 XUEQIU_MAX_PAGES
        target_date: 目标日期（YYYY-MM-DD）。默认 None 表示「今天」；
            补采历史日期时由调用方传入，用于过滤、停止翻页与目标条数判据

    Returns:
        list of dict: 帖子列表（return_stock_info=False 时）
        tuple: (posts, stock_info) 元组（return_stock_info=True 时）
    """
    if count is None:
        count = XUEQIU_POST_COUNT
    if max_pages is None:
        max_pages = XUEQIU_MAX_PAGES

    target_iso = target_date or datetime.now().strftime("%Y-%m-%d")
    is_backfill = target_iso != datetime.now().strftime("%Y-%m-%d")
    if is_backfill:
        # 默认 max_pages 是按「今天」的条数估出来的（自选股通常只翻 2 页），
        # 历史日期要往后翻得多得多，这里放宽到补采专用上限。
        max_pages = max(max_pages, XUEQIU_BACKFILL_MAX_PAGES)

    def _empty_info(status: str):
        return {
            "stock_code": stock_code, "stock_name": stock_name,
            "stock_followers": 0, "stock_price": 0, "change_percent": 0,
            "collect_status": status, "collect_status_text": _STATUS_TEXT.get(status, status),
            "collect_pages": 0,
        }

    if not XUEQIU_COOKIE.get("xq_a_token"):
        print(f"  [WARN] 雪球 Cookie 未配置，跳过 {stock_name}({stock_code})")
        _record_collect(stock_code, stock_name, STATUS_NO_COOKIE)
        if return_stock_info:
            return [], _empty_info(STATUS_NO_COOKIE)
        return []

    if not _check_playwright():
        print(f"  [WARN] Playwright 未安装，跳过雪球 {stock_name}({stock_code})")
        _record_collect(stock_code, stock_name, STATUS_NO_PLAYWRIGHT)
        if return_stock_info:
            return [], _empty_info(STATUS_NO_PLAYWRIGHT)
        return []

    _ensure_run_state()
    started_at = time.time()
    all_posts = []
    stock_followers = 0
    stock_price = 0
    change_percent = 0
    pages_ok = 0
    pages_rate_limited = 0
    pages_requested = 0
    rate_limit_events = 0
    budget_limited = False
    status = STATUS_OK
    xq_symbol = stock_code.upper()
    stock_url = f"https://xueqiu.com/S/{xq_symbol}"
    if is_backfill:
        # count 是「目标日条数」上限，不是总条数：列表顶部还压着目标日之后的新帖，
        # 按 ceil(count/页大小) 估页数会远远不够，直接用 max_pages。
        target_pages = max(1, max_pages)
    else:
        target_pages = max(1, min(max_pages, -(-count // XUEQIU_PAGE_SIZE)))

    try:
        page = _get_browser_and_page()

        # 用 response 监听拦截页面自身发出的 API 响应，作为限流时的兜底数据
        api_responses = []

        def handle_response(response):
            url = response.url
            if "statuses/search" in url or "symbol/search" in url:
                # 页面自身发出的 XHR 同样消耗限流配额，需计入账本
                _rate_window.append(time.time())
                try:
                    api_responses.append(response.json())
                except:
                    pass

        page.on("response", handle_response)

        # 访问个股页面（导航本身占一个配额槽）
        try:
            _wait_for_slot()
            page.goto(stock_url, wait_until="domcontentloaded", timeout=20000)
            # 等待页面加载和 API 调用
            page.wait_for_timeout(int(XUEQIU_PAGE_RENDER_WAIT * 1000))
        finally:
            # goto 抛异常时也必须摘掉监听，否则跨股票累积
            page.remove_listener("response", handle_response)

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

        # 导航拦截到的数据仅作限流兜底：页面首屏是 count=10，与翻页的页码偏移
        # 对不上，不能当作第 1 页用（会漏掉第 11-20 条）
        fallback_raw = []
        for data in api_responses:
            fallback_raw.extend(data.get("list") or data.get("statuses") or [])

        # 主动翻页：每页 count=20，直到覆盖目标日期或触顶
        today_str = target_iso
        page_raw = []
        for page_no in range(1, target_pages + 1):
            if _time_budget_left() <= 0:
                budget_limited = True
                print(f"    [预算] 雪球采集时间预算耗尽，停止翻页")
                break

            # 重试循环只负责把本页拿到手；raw is None 表示本页彻底失败
            raw = None
            for attempt in range(XUEQIU_MAX_RATE_LIMIT_RETRIES + 1):
                _wait_for_slot()
                pages_requested += 1
                st, text = _fetch_api_in_page(
                    page, _build_page_url(xq_symbol, page_no, XUEQIU_PAGE_SIZE))

                if _is_rate_limited(st, text):
                    pages_rate_limited += 1
                    rate_limit_events += 1
                    _note_rate_limited()
                    if (attempt < XUEQIU_MAX_RATE_LIMIT_RETRIES
                            and _time_budget_left() > XUEQIU_RATE_LIMIT_RETRY_WAIT):
                        print(f"    [限流] 第{page_no}页命中限流，"
                              f"等待 {XUEQIU_RATE_LIMIT_RETRY_WAIT:.0f}s 后重试")
                        time.sleep(XUEQIU_RATE_LIMIT_RETRY_WAIT)
                        continue
                    break

                if st != 200:
                    print(f"    [WARN] 第{page_no}页返回 HTTP {st}")
                    break

                try:
                    data = json.loads(text)
                except Exception:
                    print(f"    [WARN] 第{page_no}页响应非 JSON")
                    break

                raw = data.get("list") or data.get("statuses") or []
                break                                    # 本页成功，跳出重试循环

            if raw is None:                              # 本页彻底失败，停止翻页
                break

            pages_ok += 1
            if not raw:
                break

            page_raw.extend(raw)

            # 列表按时间倒序。当日采集时，本页已无当日帖子就说明当日已采完，
            # 后面只会更旧。补采时则不能这么判：列表顶部混着目标日之后的帖子，
            # 得等整页都早于目标日才说明已经翻过去了。
            # 不用「本页条数不足一页」判断结束：实测服务端会返回少于请求条数的页
            # （金杯电工 page1 仅 19 条却横跨 40 天），那个判据会提前截断当日数据。
            if is_backfill:
                max_date = _raw_max_date(raw)
                if max_date and max_date < target_iso:
                    print(f"    已翻过 {target_iso}，停止翻页")
                    break
                if _raw_date_count(page_raw, target_iso) >= count:
                    break                                # 目标日条数已够
            else:
                if _raw_date_count(raw, today_str) == 0:
                    break
                if len(page_raw) >= count:               # 已达目标条数
                    break

        # 翻页全失败时回退到导航兜底数据（至少还有最新若干条）
        merged_raw = page_raw if page_raw else fallback_raw

        # 判定采集状态。budget_limited 必须最先判断：预算耗尽恰好发生在
        # 一页都没翻成的时候，若先判 not pages_ok 会被误报成 error。
        if budget_limited:
            status = STATUS_BUDGET
        elif not pages_ok:
            status = STATUS_RATE_LIMITED if rate_limit_events else STATUS_ERROR
        elif rate_limit_events:
            status = STATUS_PARTIAL
        else:
            status = STATUS_OK

        if merged_raw:
            all_posts = _dedupe_posts(_normalize_posts(merged_raw, stock_code, stock_name))
        elif pages_ok or rate_limit_events:
            # 确实连一条都没拿到，再试一次页面 HTML 解析
            print(f"  雪球: API 无数据，尝试解析页面")
            all_posts = _parse_page_html(page.content(), stock_code, stock_name)

        # 将关注量和股价写入每条帖子
        for p in all_posts:
            p["stock_followers"] = stock_followers
            p["stock_price"] = stock_price
            p["change_percent"] = change_percent

        if status in (STATUS_OK, STATUS_PARTIAL):
            print(f"  ✓ 雪球 {stock_name}({stock_code}): {len(all_posts)} 条讨论"
                  f"（{pages_ok} 页）")
        else:
            print(f"  ⚠ 雪球 {stock_name}({stock_code}): {_STATUS_TEXT[status]}"
                  f"，仅 {len(all_posts)} 条")

    except Exception as e:
        print(f"  [ERROR] 雪球抓取失败: {e}")
        status = STATUS_ERROR

    # 过滤：只保留目标日期的帖子
    before_count = len(all_posts)
    all_posts = [p for p in all_posts if not p.get("publish_time") or p["publish_time"][:10] == target_iso]
    filtered_count = before_count - len(all_posts)
    if filtered_count > 0:
        print(f"  (过滤 {filtered_count} 条非目标日帖子，剩余 {len(all_posts)} 条)")

    _record_collect(
        stock_code, stock_name, status,
        pages_ok=pages_ok, pages_requested=pages_requested,
        pages_rate_limited=pages_rate_limited,
        raw_posts=len(merged_raw) if status != STATUS_ERROR else 0,
        today_posts=len(all_posts),
        rate_limit_events=rate_limit_events,
        budget_limited=budget_limited,
        elapsed_seconds=round(time.time() - started_at, 1),
    )

    if return_stock_info:
        stock_info = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "stock_followers": stock_followers,
            "stock_price": stock_price,
            "change_percent": change_percent,
            "collect_status": status,
            "collect_status_text": _STATUS_TEXT.get(status, status),
            "collect_pages": pages_ok,
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
