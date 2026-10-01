#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校验一个雪球 xq_a_token 是否还有效。

背景
----
config/settings.py 曾经被提交进过公开仓库，里面除了 DEEPSEEK_API_KEY，
还有 XUEQIU_COOKIE["xq_a_token"]。DeepSeek 那边的 key 已经吊销了，
但这个雪球令牌是另一个平台的独立凭证，吊销 DeepSeek key 不会让它失效。
本脚本用来确认它**真的死了**，而不是「我以为它死了」。

做法
----
不猜雪球的鉴权语义，只用**鉴权门控**的接口做方向性判据：
同一个请求，分别放在「带待测令牌」「不带任何令牌」两个上下文里各打一次。

    门控接口返回真实数据          ->  令牌仍然生效
    门控接口返回错误码（400016）  ->  令牌已失效

判据是方向性的，不是「两边像不像」。一把无效令牌会让服务端回
error_code=400016，那也和匿名的 60201/20025/10022 不一样 ——
拿「有差异」当证据会把死令牌判活。

哪几个接口是门控的，是实测出来的（见 xq_probe_scan.py），不是猜的。
帖子接口、热股榜这类匿名也能拿到数据的接口不参与判定。

三个坑，都是踩过才发现的：

  1. **cookie 必须在首页导航之后重新注入**。首页会把 xq_a_token 换成
     服务端新发的值，**有效令牌也一样**（所以这不代表令牌无效）。
     只在导航前注入的话，后面所有探针实际都用匿名令牌在请求，
     有效令牌会被一致地误判成已失效。
  2. **比稳定投影，不比原文**。响应里混着 view_count（阅读数）、
     timeBefore（"11分钟前"）这类每次都在变的内容，逐字比对必然误报；
     时间排序的列表还会因为新帖进来整段漂移。
  3. **域名要带 www.**。不带 www 的 xueqiu.com 会被阿里云 WAF
     无条件拦截，返回 200 + 挑战页（约 110KB HTML）而不是 JSON。

用法
----
    ./venv/bin/python check_xueqiu_token.py
    ./venv/bin/python check_xueqiu_token.py --uid 1234567890
    ./venv/bin/python check_xueqiu_token.py --reference   # 再输入一个确定有效的对照

令牌只从交互输入 / 环境变量 XQ_TOKEN / 文件读取，不写磁盘、不进 shell 历史、
不出现在进程列表里，全程不打印内容，只打印长度。
"""

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 与 main.py 保持一致：项目目录下有 browsers/ 就优先用它
_browsers_dir = BASE_DIR / "browsers"
if _browsers_dir.exists():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_browsers_dir))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 与 collectors/xueqiu_crawler.py 的上下文初始化保持一致，
# 避免因为反爬指纹差异让两次探测的结果不可比。
INIT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
    window.chrome = { runtime: {} };
"""

# 必须在页面上下文内 fetch：直连 page.request.get() 会被雪球 WAF 挡掉，
# 返回挑战页而不是 JSON（见 xueqiu_crawler._fetch_api_in_page 的注释）。
FETCH_JS = """
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
            return {status: r.status, text: text, finalUrl: r.url};
        } catch (e) {
            return {status: 0, text: 'FETCH_ERROR: ' + e, finalUrl: ''};
        } finally {
            clearTimeout(timer);
        }
    }
"""

PAGE_TIMEOUT_MS = 20000
PROBE_DELAY_S = 3.0      # 探针之间留间隔，别把 WAF 惹毛
OVERLAP_THRESHOLD = 0.7  # id 集合重合度达到这个比例就算「实质相同」


def build_probes(uid):
    """
    返回 [(探针名, URL, 是否鉴权门控)]。

    门控与否是实测出来的（见 xq_probe_scan.py）：拿一个确定有效的令牌，
    把候选端点各在「带令牌」和「匿名」两个上下文里打一遍，只有返回不同的
    才算门控。下面三个有效令牌下返回真实数据、匿名下被拒，都标了 True。

    所有域名都要带 www. —— 不带 www 的 xueqiu.com 会被阿里云 WAF 无条件
    拦截，返回 200 + 挑战页（约 110KB HTML）而不是 JSON，实测如此。
    """
    probes = [
        ("自选股组合",
         "https://stock.xueqiu.com/v5/stock/portfolio/stock/list.json"
         "?size=5&category=1&pid=-1",
         True),
        ("关注分组",
         "https://www.xueqiu.com/friendships/groups.json",
         True),
        ("首页关注流",
         "https://www.xueqiu.com/v4/statuses/home_timeline.json?page=1",
         True),
        # 非门控：匿名也能拿到数据，不参与判定，只用来确认爬虫本身还能取数
        ("[对照]爬虫帖子接口 SH600519",
         "https://www.xueqiu.com/query/v1/symbol/search/status.json"
         "?count=10&comment=0&symbol=SH600519&hl=0&source=all&sort=time&page=1&q=&type=11",
         False),
    ]
    if uid:
        probes.insert(0, (
            "账号自己的时间线",
            f"https://www.xueqiu.com/v4/statuses/user_timeline.json"
            f"?user_id={uid}&page=1",
            True,
        ))
    return probes


_LIST_KEYS = ("list", "items", "statuses", "stocks")


def _first_id_list(node, depth=0):
    """在 JSON 里找第一个「元素是带 id 的 dict」的列表，返回其 id 元组。"""
    if depth > 4:
        return None
    if isinstance(node, list):
        ids = [str(it.get("id")) for it in node
               if isinstance(it, dict) and it.get("id") is not None]
        return tuple(sorted(ids)) if ids else None
    if isinstance(node, dict):
        for key in _LIST_KEYS:
            if isinstance(node.get(key), list):
                got = _first_id_list(node[key], depth + 1)
                if got is not None:
                    return got
        if "data" in node:
            return _first_id_list(node["data"], depth + 1)
    return None


def projection(text):
    """
    把响应压成稳定指纹，剔除阅读数、相对时间这类每次请求都在变的内容。
    返回 None 表示无法投影（例如返回了 HTML 挑战页），该探针不参与判定。
    """
    try:
        d = json.loads(text)
    except Exception:
        return None

    # 错误码优先：被拒的响应通常只有这个。0 是成功码，不算错误。
    if isinstance(d, dict) and d.get("error_code") not in (None, 0, "0"):
        return ("error", str(d.get("error_code")))

    # 热股榜这类：data.items 里带 code
    data = d.get("data") if isinstance(d, dict) else None
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        codes = tuple(sorted(
            str(it.get("code") or it.get("symbol") or "")
            for it in data["items"] if isinstance(it, dict)
        ))
        return ("items", len(data["items"]), codes)

    ids = _first_id_list(d)
    if ids is not None:
        return ("ids", len(ids), ids)

    if isinstance(d, dict):
        return ("keys", tuple(sorted(d.keys())))
    if isinstance(d, list):
        return ("list", len(d))
    return None


def is_payload(res):
    """
    该响应是不是「真实数据」，而不是错误码 / 挑战页。

    判活要用的就是它：门控接口在登录态下返回数据，在匿名态下返回错误。
    「和匿名不同」本身不是证据 —— 一把无效令牌会让服务端回 400016
    （令牌无效），那也和匿名不一样，但显然是死的。
    """
    if res.get("status") != 200:
        return False
    proj = projection(res.get("text"))
    return proj is not None and proj[0] in ("ids", "items", "keys", "list")


def error_code_of(res):
    """取出响应里的 error_code 字符串，没有则返回 None。"""
    proj = projection(res.get("text"))
    if proj is not None and proj[0] == "error":
        return proj[1]
    return None


def compare(a, b):
    """
    True=实质相同  False=实质不同  None=无法判定（该探针不参与结论）

    id 集合允许抖动：按时间排序的列表每次请求都可能多一条新帖少一条旧帖，
    逐项严格比对会把这种漂移误判成「令牌生效」。
    """
    pa, pb = projection(a.get("text")), projection(b.get("text"))
    if pa is None or pb is None:
        return None
    if pa[0] != pb[0]:
        return False
    if pa[0] in ("ids", "items"):
        sa, sb = set(pa[2]), set(pb[2])
        if not sa and not sb:
            return True
        if not sa or not sb:
            return False
        return len(sa & sb) / max(len(sa), len(sb)) >= OVERLAP_THRESHOLD
    return pa == pb


def run_context(label, token, uid, probes):
    """在一个浏览器上下文里跑完所有探针。token 为空表示匿名上下文。"""
    from playwright.sync_api import sync_playwright

    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        ctx.add_init_script(INIT_SCRIPT)
        if token:
            cookies = [{"name": "xq_a_token", "value": token,
                        "domain": ".xueqiu.com", "path": "/"}]
            if uid:
                cookies.append({"name": "u", "value": uid,
                                "domain": ".xueqiu.com", "path": "/"})
            ctx.add_cookies(cookies)

        page = ctx.new_page()
        print(f"  [{label}] 打开 xueqiu.com 首页…")
        try:
            page.goto("https://xueqiu.com/", wait_until="domcontentloaded",
                      timeout=PAGE_TIMEOUT_MS)
            page.wait_for_timeout(2500)
        except Exception as e:
            print(f"  [{label}] 首页加载异常（不致命，继续）: {type(e).__name__}")

        # 关键：首页导航会把 xq_a_token 换成服务端新发的值，**有效令牌也一样**
        # （所以这不代表令牌无效）。不在这里重新注入的话，后面所有探针实际都是
        # 在用匿名令牌请求，有效令牌会被一致地误判成「已失效」——初版就是这么错的。
        if token:
            ctx.add_cookies(cookies)

        for name, url, _auth in probes:
            print(f"  [{label}] 探测: {name}")
            try:
                res = page.evaluate(FETCH_JS, [url, PAGE_TIMEOUT_MS])
            except Exception as e:
                res = {"status": 0, "text": f"EVAL_ERROR: {e}", "finalUrl": ""}
            results[name] = res
            time.sleep(PROBE_DELAY_S)

        browser.close()
    return results


def describe(res):
    """把一次探测结果压成一行可读摘要（不含任何令牌内容）。"""
    text = res.get("text") or ""
    bits = [f"HTTP {res.get('status')}", f"{len(text)}B"]

    final = res.get("finalUrl") or ""
    if final and "/passport" in final:
        bits.append("跳转到登录页")

    proj = projection(text)
    if proj is None:
        if text.startswith(("EVAL_ERROR", "FETCH_ERROR")):
            bits.append(text[:60])
        else:
            bits.append("无法投影(HTML 挑战页?)")
    else:
        kind = proj[0]
        if kind == "error":
            bits.append(f"error_code={proj[1]}")
        elif kind == "ids":
            bits.append(f"带 id 的条目 {proj[1]} 个")
        elif kind == "items":
            bits.append(f"条目 {proj[1]} 个")
        elif kind == "list":
            bits.append(f"列表 {proj[1]} 项")
        else:
            bits.append(f"顶层键 {len(proj[1])} 个")

    return "  ".join(bits)


def read_secret(prompt, env_name="XQ_TOKEN"):
    """
    按 环境变量 -> 交互输入 的顺序取值。都不给就返回 None。
    环境变量名可换，方便脚本化：待测用 XQ_TOKEN，对照用 XQ_TOKEN_REF。
    交互输入走 getpass，不进 shell 历史、不在屏幕上回显。
    """
    env = os.environ.get(env_name)
    if env and env.strip():
        return env.strip()
    if not sys.stdin.isatty():
        return None
    try:
        v = getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        return None
    return v.strip() or None


def main():
    ap = argparse.ArgumentParser(
        description="差分探测一个雪球 xq_a_token 是否仍然有效")
    ap.add_argument("--uid", default=os.environ.get("XQ_UID"),
                    help="账号的 user_id（cookie 里的 u）。可从环境变量 XQ_UID 读，"
                         "避免落进 shell 历史。给了才能测需要它的接口")
    ap.add_argument("--token-file", help="从文件读取待测令牌（文件需自行删除）")
    ap.add_argument("--reference", action="store_true",
                    help="额外交互输入一个【确定有效】的对照令牌，判定将是决定性的")
    args = ap.parse_args()

    # ---- 取待测令牌 ----
    if args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    else:
        token = read_secret("粘贴【待测】的 xq_a_token（输入不回显）: ")
    if not token:
        print("未提供待测令牌。可用环境变量 XQ_TOKEN、--token-file，或交互输入。")
        return 2

    reference = None
    if args.reference:
        reference = read_secret("粘贴【确定有效】的对照 xq_a_token（输入不回显）: ",
                                env_name="XQ_TOKEN_REF")
        if not reference:
            print("[WARN] 未提供对照令牌，将退化为「带令牌 vs 匿名」的比较。")

    probes = build_probes(args.uid)
    n_ctx = 3 if reference else 2

    print()
    print(f"待测令牌长度: {len(token)}"
          + (f"   对照令牌长度: {len(reference)}" if reference else ""))
    print(f"探针 {len(probes)} 个 × 上下文 {n_ctx} 个，预计 "
          f"{int(len(probes) * PROBE_DELAY_S * n_ctx) + n_ctx * 8} 秒")
    print()

    print("① 待测令牌的上下文")
    test = run_context("待测", token, args.uid, probes)
    print()
    print("② 匿名上下文（不带任何令牌）")
    anon = run_context("匿名", None, args.uid, probes)
    ref = None
    if reference:
        print()
        print("③ 对照令牌的上下文（确定有效）")
        ref = run_context("对照", reference, args.uid, probes)

    # ---- 逐探针对比 ----
    print()
    print("=" * 78)
    for name, _url, auth in probes:
        t, a = test[name], anon[name]
        mark = "  [门控探针]" if auth else ""
        print(f"\n探针: {name}{mark}")
        print(f"    待测  {describe(t)}")
        print(f"    匿名  {describe(a)}")
        if ref is not None:
            print(f"    对照  {describe(ref[name])}")

        # 判活看的是「有没有拿到真实数据」，不是「和匿名像不像」——
        # 无效令牌会回 400016，那也和匿名不同，但是死的。
        line = f"    是否拿到真实数据 —— 待测: {'是' if is_payload(t) else '否'}"
        if ref is not None:
            line += f"   对照: {'是' if is_payload(ref[name]) else '否'}"
        line += f"   匿名: {'是' if is_payload(a) else '否'}"
        print(line)

    # ---- 判定 ----
    # 判据是**方向性**的，不是「和匿名比差异」：
    #   门控接口在登录态返回真实数据，匿名态返回错误码。
    #   一把无效令牌会让服务端回 400016（令牌类错误），那也和匿名不一样，
    #   但显然是死的 —— 所以只有「拿到真实数据」才算活。
    gated = [n for n, _u, auth in probes if auth]

    print()
    print("=" * 78)

    if ref is not None and not any(is_payload(ref[n]) for n in gated):
        print("  [WARN] 对照令牌在任何门控探针上都没拿到数据 ——")
        print("         它自己可能就是失效的，别拿它的结论当基准。")
        print()

    alive = [n for n in gated if is_payload(test[n]) and not is_payload(anon[n])]
    anon_leak = [n for n in gated if is_payload(anon[n])]

    print(f"门控探针（{len(gated)} 个）: {', '.join(gated)}")
    print()

    if alive:
        print("判定：⚠ 待测令牌【仍然有效】")
        print("      以下门控接口在带该令牌时返回了真实数据，匿名时不返回:")
        for n in alive:
            print(f"          - {n}    {describe(test[n])}")
        print("      -> 该令牌还能以你的身份访问雪球。改密码未必能作废长寿命令牌，")
        print("         可试注销账号 / 联系雪球客服 / 等它自然过期。")
    else:
        expired = [n for n in gated if error_code_of(test[n]) == "400016"]
        print("判定：✓ 待测令牌【已失效】")
        if expired:
            print(f"      服务端在 {len(expired)}/{len(gated)} 个门控接口上回了 "
                  f"error_code=400016（令牌类错误）:")
            for n in expired:
                print(f"          - {n}    {describe(test[n])}")
            print("      -> 这把令牌现在等同于未登录，泄漏风险已消除。")
        else:
            print("      门控接口没有任何一个返回真实数据:")
            for n in gated:
                print(f"          - {n}    待测 {describe(test[n])}")
                print(f"          {' ' * len(n)}     匿名 {describe(anon[n])}")
            print("      这是间接证据（没拿到明确的令牌类错误码）。")
            if ref is None:
                print("      想更确定，可加 --reference 提供一个确定有效的对照令牌重跑。")

    if anon_leak:
        print()
        print(f"  [注意] 匿名上下文在 {'、'.join(anon_leak)} 上竟也拿到了数据 ——")
        print("         说明这些接口当前没在门控，本次判定的可靠性会打折。")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
