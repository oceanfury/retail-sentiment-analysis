# -*- coding: utf-8 -*-
"""
一次性迁移：把自选股数据从混合文件里拆出来

拆分前 `{source}_data_{date}.json` 一个文件里同时塞市场键
（overview / stock_metrics / market_cycle）和 watchlist_metrics，两条链路互相覆盖。
拆分后各走各的文件：

    {source}_data_{date}.json      只存市场
    {source}_wl_data_{date}.json   只存自选股

本脚本遍历历史文件，凡含非空 watchlist_metrics 的：
  1. 写一份 {source}_wl_data_{date}.json（只含 watchlist_metrics + _meta）
  2. 从市场文件里删掉 watchlist_metrics 键（其余键原样保留，generated_at 不动）

已迁移过的日期会被跳过，可重复执行。

用法: python migrate_split_watchlist.py [--dry-run]
"""
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from config.settings import RAW_DATA_DIR


def _market_keys(data: dict) -> list:
    return [k for k in data.keys() if k != "watchlist_metrics"]


def migrate(dry_run: bool = False) -> dict:
    stats = {"migrated": [], "skipped": [], "untouched": []}

    for source in ("guba", "xueqiu"):
        pattern = f"{source}_data_*.json"
        for market_file in sorted(RAW_DATA_DIR.glob(pattern)):
            date_str = market_file.stem[len(f"{source}_data_"):]
            with open(market_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            wl_metrics = data.get("watchlist_metrics") or []
            wl_file = RAW_DATA_DIR / f"{source}_wl_data_{date_str}.json"

            if not wl_metrics:
                # 没有自选股数据，可能本来就没采，也可能早就迁移过了
                (stats["skipped"] if wl_file.exists() else stats["untouched"]).append(
                    f"{source} {date_str}")
                continue

            if wl_file.exists() and not dry_run:
                # 已有拆分文件：只保证市场文件干净，不覆盖已拆出去的那份
                pass

            if not dry_run:
                if not wl_file.exists():
                    # _meta 里带的是混合文件的生成时间，作为拆分文件的来源时间足够准确
                    wl_data = {
                        "watchlist_metrics": wl_metrics,
                        "_meta": data.get("_meta", {}),
                    }
                    with open(wl_file, "w", encoding="utf-8") as f:
                        json.dump(wl_data, f, ensure_ascii=False, indent=2)

                del data["watchlist_metrics"]
                with open(market_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

            stats["migrated"].append(f"{source} {date_str} ({len(wl_metrics)} 条)")

    return stats


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    print("=" * 60)
    print("🔀 自选股数据拆分迁移" + ("（dry-run，不写盘）" if dry_run else ""))
    print("=" * 60)

    stats = migrate(dry_run=dry_run)

    if stats["migrated"]:
        print(f"\n✅ 已迁移 {len(stats['migrated'])} 个文件:")
        for line in stats["migrated"]:
            print(f"    {line}")
    else:
        print("\n没有需要迁移的文件。")

    if stats["skipped"]:
        print(f"\n↷ 已拆分过、跳过 {len(stats['skipped'])} 天: "
              + "、".join(s.split()[1] for s in stats["skipped"][:10]))
    print(f"\n无自选股数据、未改动 {len(stats['untouched'])} 个市场文件")
    print("=" * 60)
