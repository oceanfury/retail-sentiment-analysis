# -*- coding: utf-8 -*-
"""
从腾讯财经API获取股票实时价格
用于补充自选股缺失的股价信息
"""
import re
from typing import Dict, Tuple

try:
    import requests
except ImportError:
    requests = None


def _get_market_prefix(code: str) -> str:
    """根据股票代码生成腾讯API前缀"""
    code = code.replace("SH", "").replace("SZ", "").strip()
    if code.startswith("6"):
        return "sh"
    return "sz"


def fetch_stock_prices(codes: list) -> Dict[str, Tuple[float, float]]:
    """
    批量获取股票价格和涨跌幅

    Args:
        codes: 股票代码列表（纯数字或带SH/SZ前缀）

    Returns:
        dict: {code: (price, change_percent)}
    """
    results = {}
    if not codes or requests is None:
        return results

    headers = {"User-Agent": "Mozilla/5.0"}

    for code in codes:
        clean_code = code.replace("SH", "").replace("SZ", "").strip()
        prefix = _get_market_prefix(clean_code)
        url = f"https://qt.gtimg.cn/q={prefix}{clean_code}"

        try:
            resp = requests.get(url, headers=headers, timeout=5)
            text = resp.text

            match = re.search(r'"(.+?)"', text)
            if not match:
                continue

            fields = match.group(1).split("~")
            if len(fields) < 35:
                continue

            name = fields[1]
            current_price = float(fields[3]) if fields[3] else 0
            yesterday_close = float(fields[4]) if fields[4] else 0
            change_amount = float(fields[31]) if fields[31] else 0
            change_percent = float(fields[32]) if fields[32] else 0

            if current_price > 0:
                results[clean_code] = (round(current_price, 2), round(change_percent, 2))
        except Exception:
            continue

    return results
