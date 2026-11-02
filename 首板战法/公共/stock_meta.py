"""
股票基础属性判定：板块（创业板 / 主板 / 科创板 / 北交所）。

**唯一事实来源** —— 交易记录、UI 展示、选股脚本都从这里取，
禁止在别处另写一份前缀判断表（否则口径会分叉）。

判定规则（按 6 位股票代码前缀，纯本地计算，零外部依赖）：
  - 创业板：300、301
  - 主板  ：000、001、002、003、600、601、603、605
  - 科创板：688、689
  - 北交所：8xx、4xx、920

铁律（2026-10-26 用户要求）：
  - 交易记录里每条都必须有 `board` 字段，历史数据要补齐，新数据必须写入。
  - 板块由**代码**判定，不信任手工填写的值（防止手改污染）。
"""
from __future__ import annotations

# 板块取值常量
BOARD_CHUANGYEBAN = "创业板"
BOARD_ZHUBAN = "主板"
BOARD_KECHUANGBAN = "科创板"
BOARD_BEIJINGSUO = "北交所"

ALL_BOARDS = (BOARD_CHUANGYEBAN, BOARD_ZHUBAN, BOARD_KECHUANGBAN, BOARD_BEIJINGSUO)

# 代码前缀 → 板块。前缀越长越优先匹配。
_PREFIX_BOARD: tuple[tuple[str, str], ...] = (
    ("688", BOARD_KECHUANGBAN),
    ("689", BOARD_KECHUANGBAN),
    ("920", BOARD_BEIJINGSUO),
    ("300", BOARD_CHUANGYEBAN),
    ("301", BOARD_CHUANGYEBAN),
    ("000", BOARD_ZHUBAN),
    ("001", BOARD_ZHUBAN),
    ("002", BOARD_ZHUBAN),
    ("003", BOARD_ZHUBAN),
    ("600", BOARD_ZHUBAN),
    ("601", BOARD_ZHUBAN),
    ("603", BOARD_ZHUBAN),
    ("605", BOARD_ZHUBAN),
)


def board_of_code(stock_code) -> str:
    """
    由股票代码判定板块。

    返回 "创业板" / "主板" / "科创板" / "北交所"；
    代码缺失或无法识别时返回空串（**不要臆造**，留空比写错好）。
    """
    code = str(stock_code or "").strip()
    # 兼容 300123.SZ / sh.600000 / sz.000001 这类带市场前后缀的写法
    if "." in code:
        head, _, tail = code.partition(".")
        # 形如 300123.SZ → 取数字段；形如 sz.000001 → 取后段
        if head.isdigit() or head == "":
            code = head
        elif tail.isdigit() or tail == "":
            code = tail
        else:
            code = head
    code = code.strip()
    # 兼容 SH600000 / SZ000001 / BJ430139 前缀
    if len(code) > 2 and code[:2].upper() in ("SH", "SZ", "BJ") and code[2:].isdigit():
        code = code[2:]
    if not code.isdigit() or len(code) < 6:
        return ""
    code = code.zfill(6)
    for prefix, board in _PREFIX_BOARD:
        if code.startswith(prefix):
            return board
    # 北交所：8 开头（87/43/83…）与 4 开头（43…）
    if code[0] in ("4", "8"):
        return BOARD_BEIJINGSUO
    return ""


def board_of_name(stock_name) -> str:
    """只给了名称、没给代码时返回空串（不臆造板块）。"""
    return ""
