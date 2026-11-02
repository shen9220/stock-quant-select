"""
PushPlus 推送通知脚本。
通过 GitHub Actions 定时触发，将各程序（stock/slow/up）当日的选股结果推送到微信。

早盘流程（up/slow）：
  1) 从 D0 推荐 md 同步基础记录（code/name/rank，不含价格）
  2) up 缺 md 时 fast_gen 自动生成
  3) 发送推送
  4) 15:00 由 daily_close_job 补全买卖价与盈亏

用法:
  python3 send_push_notify.py --program up --token YOUR_PUSHPLUS_TOKEN
  python3 send_push_notify.py --program slow --sync-only   # 仅同步基础记录
"""
from __future__ import annotations

import os
import sys

_JOBS_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_JOBS_DIR)
_LIB_DIR = os.path.join(_BASE_DIR, "lib")
for _p in (_BASE_DIR, _LIB_DIR, _JOBS_DIR, os.path.dirname(_BASE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from runtime_paths import BASE_DIR, ROOT_DIR, DATA_DIR, ensure_sys_path  # noqa: E402
ensure_sys_path()

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(ROOT_DIR, "up", "scripts"))
import paths  # noqa: E402

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RANK_RE = re.compile(r"排名\s+\d+:\s+(\d{6})\s+-\s+(.+)")
VALID_PROGRAMS = paths.PROGRAMS

PUSHPLUS_URL = "http://www.pushplus.plus/send"  # 兼容旧引用；实际发送走 pushplus_notify
PROGRAM_DISPLAY = {
    "stock": "📈 Stock 强度首板",
    "slow": "🐢 Slow 低价一进二",
    "up": "🚀 Up 强度首板",
    "stock_updata": "📉 Stock水下(绿开)",
}


def _prev_trading_day(date_str: str) -> str | None:
    try:
        import price_service as ps
        return ps.prev_trading_day(date_str)
    except Exception:
        pass
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dt -= timedelta(days=1)
    while dt.weekday() >= 5:
        dt -= timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def _parse_md_stocks(d0: str, program: str) -> list[dict]:
    path = paths.recommendation_md(program, d0)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    head = text.split("=== 其他未入选", 1)[0]
    stocks = []
    for m in RANK_RE.finditer(head):
        stocks.append({"stock_code": m.group(1), "stock_name": m.group(2).strip()})
        if len(stocks) >= 2:
            break
    return stocks


def sync_before_notify(program: str) -> dict:
    """推送前同步基础买入记录（up 严格对齐 up.py 通知结果）。"""
    if program == "up":
        try:
            from daily_close_job import _import_up_record_sync_module
            up_mod = _import_up_record_sync_module()
            return up_mod.sync_up_trades(force=True)
        except Exception as e:
            print(f"[push] up 同步失败: {e}")
            return {"error": str(e)}
    if program == "slow":
        try:
            from daily_close_job import sync_morning_records
            return sync_morning_records(program="slow")
        except Exception as e:
            print(f"[push] 早盘同步失败: {e}")
            return {"error": str(e)}
    return {"skipped": True}


def load_today_trades(program: str) -> list[dict]:
    """读取今日买入记录；up 优先 up.py JSON。"""
    today = datetime.now().strftime("%Y-%m-%d")

    if program == "up":
        try:
            from daily_close_job import _import_up_record_sync_module
            up_mod = _import_up_record_sync_module()
            stocks, source, _ = up_mod.resolve_up_top2(today)
            if stocks:
                trades_dir = paths.program_trades_dir(program)
                path = os.path.join(trades_dir, f"{today}.json")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data:
                            return data
                return [
                    {
                        "date": today,
                        "stock_code": s["stock_code"],
                        "stock_name": s["stock_name"],
                        "rank": s["rank"],
                        "buy_price": None,
                    }
                    for s in stocks
                ]
        except Exception as e:
            print(f"[push] up 读取权威源失败: {e}")

    trades_dir = paths.program_trades_dir(program)

    if os.path.isdir(trades_dir):
        path = os.path.join(trades_dir, f"{today}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data:
                    return data

    d0 = _prev_trading_day(today)
    if d0:
        stocks = _parse_md_stocks(d0, program)
        if stocks:
            return [
                {
                    "date": today,
                    "stock_code": s["stock_code"],
                    "stock_name": s["stock_name"],
                    "rank": i,
                    "buy_price": None,
                }
                for i, s in enumerate(stocks, 1)
            ]

    prev = _prev_trading_day(today)
    if prev and os.path.isdir(trades_dir):
        path = os.path.join(trades_dir, f"{prev}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    return []


def format_trade_summary(program: str, trades: list[dict]) -> str:
    name = PROGRAM_DISPLAY.get(program, program)
    today = datetime.now().strftime("%Y-%m-%d")

    if not trades:
        return f"{name}\n{today} 暂无买入记录"

    lines = [f"{name} — {today} 选股推送", ""]
    morning_only = all(t.get("buy_price") in (None, "") for t in trades)

    for t in sorted(trades, key=lambda x: int(x.get("rank") or 0)):
        rank = t.get("rank", "?")
        code = t.get("stock_code", "???")
        sname = t.get("stock_name", "???")
        buy = t.get("buy_price")
        sell_935 = t.get("sell_price_935")
        sell_1315 = t.get("sell_price_1315")
        pnl_batch = t.get("pnl_batch_total")

        medal = "🥇" if rank == 1 else "🥈"
        lines.append(f"{medal} 排名 {rank}: {code} {sname}")
        if buy is not None and buy != "":
            lines.append(f"   买入价: {buy}")
        elif morning_only:
            lines.append(f"   买入: 今日开盘（15:00 后补全价格）")
        if sell_935 is not None:
            lines.append(f"   早盘卖出: {sell_935}")
        if sell_1315 is not None:
            lines.append(f"   午盘卖出: {sell_1315}")
        if pnl_batch is not None:
            emoji = "🟢" if pnl_batch > 0 else ("🔴" if pnl_batch < 0 else "⚪")
            lines.append(f"   分批总盈亏: {emoji} {pnl_batch:+.2f}%")
        skip = t.get("skip_reason", "")
        if skip and "买不进" in str(skip):
            lines.append(f"   ⚠️ 一字板未买入")

    completed = [t for t in trades if t.get("sell_price_935") is not None or t.get("sell_price_1315") is not None]
    pnls = [t.get("pnl_batch_total") for t in trades if t.get("pnl_batch_total") is not None]
    lines.append("")
    lines.append(f"📊 统计: {len(trades)} 笔 | 已卖出 {len(completed)} 笔")
    if pnls:
        avg_pnl = sum(pnls) / len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        lines.append(f"📈 平均盈亏: {avg_pnl:+.2f}% | 胜率: {wins}/{len(pnls)} ({wins/len(pnls)*100:.1f}%)")
    if morning_only:
        lines.append("")
        lines.append("⏰ 价格将在 15:00 收盘后自动补全")

    lines.append("")
    lines.append(f"🔗 查看详情: stock_UI.py → {program} Tab")
    return "\n".join(lines)


def send_pushplus(
    token: str,
    title: str,
    content: str,
    topic: str = "",
    to: str = "",
    kind: str = "",
) -> bool:
    """委托 pushplus_notify：HTTPS+certifi 优先，传输失败降级 HTTP。"""
    from pushplus_notify import send_pushplus as _send

    return bool(_send(title, content, token=token, topic=topic, to=to, kind=kind))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PushPlus 推送通知")
    parser.add_argument("--program", required=True, choices=VALID_PROGRAMS)
    parser.add_argument("--token", default="")
    parser.add_argument("--topic", default="", help="群组编码（一对多）")
    parser.add_argument("--to", default="", help="好友令牌（发给指定好友）")
    parser.add_argument(
        "--kind",
        default="",
        help="通知类别（决定是否转发给好友）。留空=只发本人",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sync-only", action="store_true", help="仅同步基础记录，不推送")
    args = parser.parse_args()

    token = args.token or os.environ.get("PUSHPLUS_TOKEN", "")
    topic = args.topic or os.environ.get("PUSHPLUS_TOPIC", "")
    to = args.to or os.environ.get("PUSHPLUS_TO", "")
    kind = args.kind or os.environ.get("PUSHPLUS_KIND", "")

    # 暂停清单权威：01_量化模型/通知规范/PushPlus通知规范.md §3
    try:
        from pushplus_notify import PAUSED_NOTIFY_PROGRAMS
    except Exception:
        PAUSED_NOTIFY_PROGRAMS = frozenset({"slow", "up"})

    if args.program in ("slow", "up"):
        sync_result = sync_before_notify(args.program)
        print(f"[push] 早盘同步: {sync_result}")

    if args.sync_only:
        trades = load_today_trades(args.program)
        print(f"[push] 同步完成，今日 {len(trades)} 条记录")
        sys.exit(0)

    if args.program in PAUSED_NOTIFY_PROGRAMS:
        print(
            f"[push] {args.program} 已暂停 PushPlus（规范 §3），本次不发送",
            flush=True,
        )
        sys.exit(0)

    if not token and not args.dry_run:
        print("错误: 请设置 PUSHPLUS_TOKEN 环境变量或使用 --token 参数")
        sys.exit(1)

    trades = load_today_trades(args.program)
    message = format_trade_summary(args.program, trades)
    title = f"{args.program.upper()} 选股推送 {datetime.now().strftime('%m/%d')}"

    print(message)
    print(f"\n--- 推送标题: {title} ---")

    if args.dry_run:
        print("[DRY-RUN] 未实际发送")
    else:
        success = send_pushplus(token, title, message, topic, to, kind=kind)
        sys.exit(0 if success else 1)
