#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性校准工具：找出哪些雪球端点真的能区分「登录」与「未登录」。

背景
----
check_xueqiu_token.py 靠差分探测判断令牌死活，前提是至少有一个
**鉴权门控**的探针——即登录与未登录的返回不同。我原先凭推断选了
「自选股组合」接口（匿名返回 error_code=60201），实测发现它带着
有效令牌也是 60201，两边一样，根本不区分。于是工具恒定报「已失效」。

这个脚本不猜了：拿一个**确定有效**的令牌，把一批候选端点各自在
「带令牌」和「匿名」两个上下文里打一遍，直接列出哪些端点有差异。
有差异的那些，才有资格当探针。

顺带做两个诊断：
  1. 首页加载后 xq_a_token 还在不在、长度有没有变 ——
     服务端若拒绝该令牌，会把它清掉或换成新的，这是最直接的证据。
  2. cookie 名字集合的差异。

令牌只从交互输入 / 环境变量 XQ_TOKEN 读取，不写磁盘、不打印。

用法
----
    ./venv/bin/python xq_probe_scan.py                 # 交互输入有效令牌
    ./venv/bin/python xq_probe_scan.py --uid 12345     # 同时注入 u cookie
"""

import argparse
import importlib.util
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("xq", BASE_DIR / "check_xueqiu_token.py")
xq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(xq)

CANDS = [
    # ---- 个人数据（理论上该区分登录） ----
    ("自选股组合 pid=-1",
     "https://stock.xueqiu.com/v5/stock/portfolio/stock/list.json?size=5&category=1&pid=-1"),
    ("自选股组合 裸请求",
     "https://stock.xueqiu.com/v5/stock/portfolio/stock/list.json"),
    ("关注分组",
     "https://www.xueqiu.com/friendships/groups.json"),
    ("消息提醒",
     "https://www.xueqiu.com/statuses/remind/list.json?count=10"),
    ("我的关注列表",
     "https://www.xueqiu.com/query/v1/user/follow/list.json?page=1&count=10"),
    ("首页关注流",
     "https://www.xueqiu.com/v4/statuses/home_timeline.json?page=1"),
    ("组合调仓记录",
     "https://www.xueqiu.com/cubes/rebalancing/history.json?count=10"),
    ("我的提醒数",
     "https://www.xueqiu.com/statuses/remind/count.json"),
    # ---- 公开内容（预期不区分，作为对照组） ----
    ("[对照]爬虫帖子接口",
     "https://www.xueqiu.com/query/v1/symbol/search/status.json"
     "?count=10&comment=0&symbol=SH600519&hl=0&source=all&sort=time&page=1&q=&type=11"),
    ("[对照]热股榜",
     "https://stock.xueqiu.com/v5/stock/hot_stock/list.json"
     "?page=1&size=5&_type=22&type=22&include=1"),
    ("[对照]关注流热帖",
     "https://www.xueqiu.com/statuses/hot/listV2.json?since_id=-1&max_id=-1&size=10"),
]


def run(label, token, uid):
    """
    跑一个上下文。额外做两件事：
      1. 记录注入的令牌有没有活过首页导航（在内存里比对值，不打印内容）
      2. 导航后重新注入一次，否则服务端换发的匿名令牌会覆盖掉我们要测的那把，
         导致所有探针其实都是在用匿名令牌请求 —— 有效令牌也会看起来像失效
    """
    from playwright.sync_api import sync_playwright

    def _cookie_payload():
        ck = []
        if token:
            ck.append({"name": "xq_a_token", "value": token,
                       "domain": ".xueqiu.com", "path": "/"})
        if uid:
            ck.append({"name": "u", "value": uid,
                       "domain": ".xueqiu.com", "path": "/"})
        return ck

    info = {"cookies": [], "survived": None, "anon_token_len": None,
            "results": {}}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=xq.UA,
                                  viewport={"width": 1280, "height": 800},
                                  locale="zh-CN")
        ctx.add_init_script(xq.INIT_SCRIPT)
        ctx.add_cookies(_cookie_payload())

        page = ctx.new_page()
        try:
            page.goto("https://xueqiu.com/", wait_until="domcontentloaded",
                      timeout=xq.PAGE_TIMEOUT_MS)
            page.wait_for_timeout(2500)
        except Exception as e:
            print(f"  [{label}] 首页异常: {type(e).__name__}")

        # 诊断 1：导航后 cookie 里的 xq_a_token 还是我们注入的那个吗？
        for c in ctx.cookies():
            if c.get("name") == "xq_a_token":
                val = c.get("value") or ""
                info["anon_token_len"] = len(val)
                info["survived"] = (val == token) if token else None
        info["cookies"] = sorted({c.get("name") for c in ctx.cookies()})

        # 关键：重新注入，盖掉服务端换发的匿名令牌
        ctx.add_cookies(_cookie_payload())

        for name, url in CANDS:
            try:
                res = page.evaluate(xq.FETCH_JS, [url, xq.PAGE_TIMEOUT_MS])
            except Exception as e:
                res = {"status": 0, "text": f"EVAL_ERROR: {e}", "finalUrl": ""}
            info["results"][name] = res
            time.sleep(2.0)

        browser.close()
    return info


def main():
    ap = argparse.ArgumentParser(description="扫描哪些雪球端点能区分登录状态")
    ap.add_argument("--uid", help="cookie 里的 u，注入了才能测需要它的接口")
    args = ap.parse_args()

    token = xq.read_secret("粘贴一个【确定有效】的 xq_a_token（输入不回显）: ")
    if not token:
        print("未提供令牌。可用环境变量 XQ_TOKEN 或交互输入。")
        return 2

    print()
    print(f"有效令牌长度: {len(token)}   uid: {args.uid or '<未提供>'}")
    print()
    print("① 带令牌上下文")
    a = run("有效", token, args.uid)
    print()
    print("② 匿名上下文（作为对照）")
    b = run("匿名", None, args.uid)

    # ---- 诊断：令牌是否被服务端接受 ----
    print()
    print("=" * 78)
    print("诊断 1：注入的 xq_a_token 有没有活过首页导航（只比对，不打印内容）")
    print(f"  带令牌上下文   传入 {len(token)} 字符")
    if a["survived"] is None:
        print("                 加载后 cookie 已被服务端清除      ⚠ 令牌不被接受")
    elif a["survived"]:
        print("                 加载后原值保留                    ✓ 服务端接受了这把令牌")
    else:
        print(f"                 加载后已被替换为另一个 {a['anon_token_len']} 字符的值  "
              f"⚠ 令牌不被接受")
    print("  匿名上下文     未传入")
    print(f"                 加载后服务端主动发了一个 {b['anon_token_len']} 字符的匿名令牌")
    print()
    print("  注意：匿名令牌和登录令牌长度可能一样，光看长度分不出来。")

    print()
    print("诊断 2：cookie 名字集合差异")
    only_a = [n for n in a["cookies"] if n not in b["cookies"]]
    only_b = [n for n in b["cookies"] if n not in a["cookies"]]
    print(f"  带令牌上下文有 {len(a['cookies'])} 个: {', '.join(a['cookies'])}")
    print(f"  匿名上下文有   {len(b['cookies'])} 个: {', '.join(b['cookies'])}")
    print(f"  只有带令牌侧有: {only_a or '<无>'}")
    print(f"  只有匿名侧有:   {only_b or '<无>'}")

    # ---- 逐端点对比 ----
    print()
    print("=" * 78)
    print(f"{'端点':<22} {'带令牌':<34} {'匿名':<34} 区分?")
    print("-" * 100)
    discriminators = []
    for name, _ in CANDS:
        ra, rb = a["results"][name], b["results"][name]
        eq = xq.compare(ra, rb)
        mark = {True: "否", False: "★ 是", None: "无法判定"}[eq]
        if eq is False:
            discriminators.append(name)
        print(f"{name:<22} {xq.describe(ra):<34} {xq.describe(rb):<34} {mark}")

    print()
    print("=" * 78)
    if discriminators:
        print("能区分登录状态的端点：")
        for n in discriminators:
            print(f"  ★ {n}")
        print()
        print("把这些填进 check_xueqiu_token.py 的 build_probes()，第三个字段设 True。")
    else:
        print("没有任何端点表现出差异。")
        print("要么这批候选里没有门控接口，要么令牌本身就没生效（见诊断 1）。")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
