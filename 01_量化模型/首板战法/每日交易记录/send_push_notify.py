"""
PushPlus 推送通知脚本。
通过 GitHub Actions 定时触发，将各程序（stock/slow/up）当日的选股结果推送到微信。

用法:
  python3 send_push_notify.py --program stock --token YOUR_PUSHPLUS_TOKEN
  python3 send_push_notify.py --program slow --token YOUR_PUSHPLUS_TOKEN
  python3 send_push_notify.py --program up --token YOUR_PUSHPLUS_TOKEN

环境变量:
  PUSHPLUS_TOKEN: PushPlus 推送 token（必填）
  PUSHPLUS_TOPIC: PushPlus 群组推送 topic（可选）
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRADES_BASE = os.path.join(BASE_DIR, "trades")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VALID_PROGRAMS = ("stock", "slow", "up")

PUSHPLUS_URL = "http://www.pushplus.plus/send"


def _prev_trading_day(date_str: str) -> str | None:
    """获取前一交易日。"""
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


def load_today_trades(program: str) -> list[dict]:
    """读取今日的买入记录（D1 = today 或最近的交易日）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    trades_dir = os.path.join(TRADES_BASE, program)

    if not os.path.isdir(trades_dir):
        return []

    path = os.path.join(trades_dir, f"{today}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # 尝试前一交易日
    prev = _prev_trading_day(today)
    if prev:
        path = os.path.join(trades_dir, f"{prev}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)

    return []


def format_trade_summary(program: str, trades: list[dict]) -> str:
    """格式化交易摘要为推送消息。"""
    program_names = {"stock": "📈 Stock 强度首板", "slow": "🐢 Slow 低价一进二", "up": "🚀 Up 强度首板"}
    name = program_names.get(program, program)

    if not trades:
        today = datetime.now().strftime("%Y-%m-%d")
        return f"{name}\n{today} 暂无买入记录"

    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"{name} — {today} 选股推送", ""]

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
        if buy is not None:
            lines.append(f"   买入价: {buy}")
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

    # 汇总统计
    completed = [t for t in trades if t.get("sell_price_935") is not None or t.get("sell_price_1315") is not None]
    pnls = [t.get("pnl_batch_total") for t in trades if t.get("pnl_batch_total") is not None]
    lines.append("")
    lines.append(f"📊 统计: {len(trades)} 笔 | 已卖出 {len(completed)} 笔")
    if pnls:
        avg_pnl = sum(pnls) / len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        lines.append(f"📈 平均盈亏: {avg_pnl:+.2f}% | 胜率: {wins}/{len(pnls)} ({wins/len(pnls)*100:.1f}%)")

    # 操作提示
    lines.append("")
    lines.append(f"🔗 查看详情: stock_UI.py → {program} Tab")
    return "\n".join(lines)


def send_pushplus(token: str, title: str, content: str, topic: str = "") -> bool:
    """发送 PushPlus 通知。"""
    body = {
        "token": token,
        "title": title,
        "content": content,
        "template": "txt",
    }
    if topic:
        body["topic"] = topic

    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            PUSHPLUS_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("code") == 200:
                print(f"[pushplus] 发送成功: {title}")
                return True
            else:
                print(f"[pushplus] 发送失败: {result}")
                return False
    except Exception as e:
        print(f"[pushplus] 发送异常: {e}")
        return False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="PushPlus 推送通知")
    parser.add_argument("--program", required=True, choices=VALID_PROGRAMS, help="选股程序")
    parser.add_argument("--token", default="", help="PushPlus Token（也可通过 PUSHPLUS_TOKEN 环境变量设置）")
    parser.add_argument("--topic", default="", help="PushPlus 群组 topic（可选）")
    parser.add_argument("--dry-run", action="store_true", help="仅打印消息，不发送")
    args = parser.parse_args()

    token = args.token or os.environ.get("PUSHPLUS_TOKEN", "")
    topic = args.topic or os.environ.get("PUSHPLUS_TOPIC", "")

    if not token and not args.dry_run:
        print("错误: 请设置 PUSHPLUS_TOKEN 环境变量或使用 --token 参数")
        print("获取 Token: https://www.pushplus.plus/")
        sys.exit(1)

    trades = load_today_trades(args.program)
    message = format_trade_summary(args.program, trades)
    title = f"{args.program.upper()} 选股推送 {datetime.now().strftime('%m/%d')}"

    print(message)
    print(f"\n--- 推送标题: {title} ---")

    if args.dry_run:
        print("[DRY-RUN] 未实际发送")
    else:
        success = send_pushplus(token, title, message, topic)
        sys.exit(0 if success else 1)
