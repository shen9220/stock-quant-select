"""PushPlus 发送：HTTPS(certifi) 优先，传输失败再降级 HTTP。

供 stock / 早盘失败告警 / 其它脚本共用。业务错误（token 无效等）不降级。

推送目标规则（2026-10-27 修订，满足「本人收全部、好友收白名单」）：
  1. **本人【永远】是收件人**（self，即不填 to/topic 的默认投递）。
  2. **好友白名单**：若配置了对外通道（to 好友 / topic 群组），且 kind ∈ FRIEND_KIND_WHITELIST，
     则【额外】转发给好友/群组；其余类别只发本人（不转发对外通道）。
  3. **目标（to / topic / self）**：好友令牌 / 群组编码 / 只发自己。对外通道与本人是两次独立推送
     （PushPlus 单请求里 to/topic 互斥，无法一次既发本人又发好友）。
"""
from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from typing import Optional

_HTTPS_URL = "https://www.pushplus.plus/send"
_HTTP_URL = "http://www.pushplus.plus/send"
_TOKEN_RE = re.compile(r"[a-f0-9]{32}", re.I)
# 网页里复制过来的富文本标签，如 <span style="...">xxx</span>
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# 单次通道超时；两端合计约 ≤16s，适合 9:05 准点窗口
_HTTPS_TIMEOUT = 8
_HTTP_TIMEOUT = 8

# 交易数据记录 GitHub Pages（每条通知必须附带）
TRADE_UI_PAGES_URL = (
    os.environ.get("TRADE_UI_PAGES_URL")
    or "https://shen9220.github.io/stock-quant-select/trade-ui/"
).strip()


def _with_pages_url(content: str) -> str:
    """每条通知正文末尾附带 Pages 地址（去重）。"""
    text = str(content or "")
    url = TRADE_UI_PAGES_URL
    if not url:
        return text
    if url in text:
        return text
    sep = "" if text.endswith("\n") or not text else "\n"
    return f"{text}{sep}\n交易看板: {url}"

# 通知类别常量（唯一事实来源，调用方引用这些名字，不要写裸字符串）
KIND_STOCK_NOTIFY = "stock_notify"      # stock 模型通知（每交易日 09:05）
KIND_STOCK_WEEKLY = "stock_weekly"      # stock 周结果通知（当周最后交易日 15:00）
KIND_STOCK_TOP2 = "stock_top2"          # stock Top2 买入结果（收盘后）
KIND_STOCK_UPDATA = "stock_updata"      # STOCK 水下集合竞价（09:25）
KIND_STOCK_V2 = "stock_v2_notify"       # Stock V2 双路径（09:27–09:29 单次）
KIND_SLOW_NOTIFY = "slow_notify"        # slow 选股通知
KIND_UP_NOTIFY = "up_notify"            # up 选股通知

# 转发给好友的类别白名单（用户 2026-10-27 / 2026-10-28 明确）
# 好友可收：9:05 stock + 9:25 水下 + 15:00 周结果 + StockV2 早盘单次。
# 本人永远收全部；Top2 收盘后买入涨跌幅（KIND_STOCK_TOP2）只发本人，不入白名单。
# 只在这里改，改一处即可调整对外推送范围。
FRIEND_KIND_WHITELIST = {
    KIND_STOCK_NOTIFY,
    KIND_STOCK_UPDATA,
    KIND_STOCK_WEEKLY,
    KIND_STOCK_V2,
}


def load_pushplus_token() -> str:
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if token:
        return token
    token_path = os.path.expanduser("~/new_stock/github/pushplus/token.md")
    if not os.path.isfile(token_path):
        return ""
    with open(token_path, encoding="utf-8") as f:
        text = f.read()
    match = _TOKEN_RE.search(text)
    return match.group(0) if match else ""


def load_pushplus_target() -> tuple[str, str]:
    """
    读取推送目标，返回 (mode, value)。

    PushPlus 有三种推送方式，官方明确「好友消息和群组消息不能同时使用」：
      - 一对一（自己）  → mode="self"，value=""
      - 一对多（群组）  → mode="topic"，value=群组编码（自己定义的短字符串）
      - 好友消息        → mode="to"，value=好友令牌（32 位 hex）

    配置来源（后者覆盖前者）：
      1. 环境变量 PUSHPLUS_TO / PUSHPLUS_TOPIC
      2. ~/new_stock/github/pushplus/topic.md

    文件里每行可用 `KEY=value` 指定类型，也支持裸值自动识别：
      - `TO=87a31c4a...`      → 好友令牌（32 位 hex）→ mode="to"
      - `TOPIC=quant-team`    → 群组编码 → mode="topic"
      - `87a31c4a...`         → 裸值，是 32 位 hex 就当好友令牌
    """
    # 1) 环境变量
    env_to = os.environ.get("PUSHPLUS_TO", "").strip()
    if env_to:
        return "to", _clean_value(env_to)
    env_topic = os.environ.get("PUSHPLUS_TOPIC", "").strip()
    if env_topic:
        return "topic", _clean_value(env_topic)

    # 2) 配置文件
    topic_path = os.path.expanduser("~/new_stock/github/pushplus/topic.md")
    if not os.path.isfile(topic_path):
        return "self", ""
    try:
        with open(topic_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return "self", ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # 显式 TO= / TOPIC= 优先
        m = re.search(r"\b(to|topic)\s*=\s*(.+)$", line, re.I)
        if m:
            key = m.group(1).lower()
            value = _clean_value(m.group(2))
            if not value:
                continue
            return ("to" if key == "to" else "topic"), value
        # 裸值：32 位 hex 视为好友令牌，否则视为群组编码
        value = _clean_value(line)
        if not value:
            continue
        if re.fullmatch(r"[a-f0-9]{32}", value, re.I):
            return "to", value
        return "topic", value
    return "self", ""


def load_pushplus_topic() -> str:
    """兼容旧接口：只返回群组编码（非 topic 模式返回空串）。"""
    mode, value = load_pushplus_target()
    return value if mode == "topic" else ""


def load_pushplus_to() -> str:
    """返回好友令牌（非 to 模式返回空串）。"""
    mode, value = load_pushplus_target()
    return value if mode == "to" else ""


def _clean_value(raw: str) -> str:
    """
    归一化配置值。

    兼容从网页 / 聊天窗口直接粘贴过来的各种形态：
      - 裸值：abc123
      - KEY=abc123（前缀可带说明文字，如「（好友通知）TOPIC=abc123」）
      - 带网页富文本标签：<span style="...">abc123</span>
      - 带引号、带行尾注释
    """
    value = raw.strip()
    # 1) 先剥掉网页富文本标签
    value = _HTML_TAG_RE.sub("", value).strip()
    # 2) 去掉 KEY= 前缀（允许前面有说明文字）
    m = re.search(r"\b(to|topic)\s*=\s*(.+)$", value, re.I)
    if m:
        value = m.group(2).strip()
    # 3) 去掉成对引号
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    # 4) 去掉行尾注释（编码本身不会含 #）
    if "#" in value:
        value = value.split("#", 1)[0].strip()
    return value


def _ssl_context():
    """优先 certifi CA；不可用则退回系统默认（可能仍为空）。"""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _post_json(url: str, body: bytes, timeout: float, context: Optional[ssl.SSLContext] = None) -> dict:
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    kwargs = {"timeout": timeout}
    if context is not None and url.startswith("https://"):
        kwargs["context"] = context
    with urllib.request.urlopen(req, **kwargs) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_transport_error(exc: BaseException) -> bool:
    """仅传输层失败才允许降级；业务 JSON 错误不算。"""
    if isinstance(exc, (TimeoutError, ConnectionError, OSError, urllib.error.URLError)):
        return True
    if isinstance(exc, ssl.SSLError):
        return True
    # urllib 常把 SSL 包进 URLError.reason
    reason = getattr(exc, "reason", None)
    if isinstance(reason, (ssl.SSLError, TimeoutError, ConnectionError, OSError)):
        return True
    name = type(exc).__name__
    return "SSL" in name or "Certificate" in name or "TimedOut" in name


def send_pushplus(
    title: str,
    content: str,
    token: str | None = None,
    *,
    topic: str = "",
    to: str = "",
    kind: str = "",
    https_timeout: float = _HTTPS_TIMEOUT,
    http_timeout: float = _HTTPS_TIMEOUT,
) -> bool:
    """
    推送目标规则（2026-10-27 修订，满足「本人收全部、好友收白名单」）：

      - 本人【永远】是收件人（self，即不填 to/topic 的默认投递）。
      - 若配置了对外通道（to 好友 / topic 群组，来源优先级：显式参数 > 环境变量 > 配置文件），
        且 kind ∈ FRIEND_KIND_WHITELIST，则【额外】转发给好友/群组。
      - kind 不在白名单（或留空）→ 只发本人，不转发对外通道。
      - 对外通道与本人是两次独立推送（PushPlus 单请求 to/topic 互斥，无法一次既发本人又发好友）。

    显式传 to/topic（手动补发）也遵循同一条规则：本人照收，白名单类别额外转发好友。
    """
    token = (token or load_pushplus_token()).strip()
    if not token:
        print(
            "[通知失败] 未配置 PUSHPLUS_TOKEN，也未找到 ~/new_stock/github/pushplus/token.md",
            flush=True,
        )
        return False

    kind = (kind or "").strip()

    # --- 对外通道目标（好友/群组）；本人不在此列，本人永远收 ---
    if to.strip():
        friend_mode, friend_value = "to", _clean_value(to)
        friend_label = f"好友[{friend_value[:8]}…]"
    elif topic.strip():
        friend_mode, friend_value = "topic", _clean_value(topic)
        friend_label = f"群组[{friend_value}]"
    else:
        cfg_mode, cfg_value = load_pushplus_target()
        friend_mode, friend_value = cfg_mode, cfg_value
        if cfg_mode == "to":
            friend_label = f"好友[{cfg_value[:8]}…]" if cfg_value else "好友"
        elif cfg_mode == "topic":
            friend_label = f"群组[{cfg_value}]"
        else:
            friend_label = ""

    # --- 组装收件人：本人永远在；白名单内额外加对外通道 ---
    targets: list[tuple[str, str, str]] = [("self", "", "本人")]
    if friend_mode in ("to", "topic") and friend_value:
        if kind in FRIEND_KIND_WHITELIST:
            targets.append((friend_mode, friend_value, friend_label))
            print(
                f"[通知] 类别「{kind}」在好友白名单内 → 发本人 + {friend_label}",
                flush=True,
            )
        else:
            print(
                f"[通知] 类别「{kind}」不在好友白名单 → 只发本人（不转发{friend_label}）",
                flush=True,
            )
    elif not kind:
        # 未标注类别：默认只发本人（不转发任何对外通道）
        pass

    ok_all = True
    for mode, value, label in targets:
        ok = _perform_send(
            title,
            content,
            token,
            mode=mode,
            value=value,
            kind=kind,
            https_timeout=https_timeout,
            http_timeout=http_timeout,
        )
        if not ok:
            ok_all = False
    return ok_all


def _perform_send(
    title: str,
    content: str,
    token: str,
    *,
    mode: str,
    value: str,
    kind: str,
    https_timeout: float,
    http_timeout: float,
) -> bool:
    """单次推送（本人 / 好友 / 群组），含 HTTPS→HTTP 兜底。返回是否成功。"""
    content = _with_pages_url(content)
    payload = {"token": token, "title": title, "content": content, "template": "txt"}
    if mode == "to":
        # 好友令牌：官方规定 to 与 topic 不能同时使用
        payload["to"] = value
        target = f"好友[{value[:8]}…]"
    elif mode == "topic":
        payload["topic"] = value
        target = f"群组[{value}]"
    else:
        target = "本人"
    kind_tag = f"[{kind}] " if kind else ""
    body = json.dumps(payload).encode("utf-8")

    # --- 主路径：HTTPS ---
    try:
        data = _post_json(_HTTPS_URL, body, https_timeout, context=_ssl_context())
    except Exception as e:
        if not _is_transport_error(e):
            print(f"[通知失败] HTTPS 请求异常: {e}", flush=True)
            return False
        print(
            f"[通知] HTTPS 传输失败（{type(e).__name__}: {e}），{https_timeout:.0f}s 内降级 HTTP…",
            flush=True,
        )
        try:
            data = _post_json(_HTTP_URL, body, http_timeout, context=None)
            print("[通知] 已走 HTTP 兜底", flush=True)
        except Exception as e2:
            print(f"[通知失败] HTTP 兜底仍失败: {e2}", flush=True)
            return False
    else:
        # HTTPS 通了但业务失败：不再降级（token/配额问题降级也没用）
        if data.get("code") != 200:
            print(
                f"[通知失败] PushPlus 返回: code={data.get('code')} msg={data.get('msg')}",
                flush=True,
            )
            return False
        print(
            f"[通知成功] {kind_tag}{title}（HTTPS → {target}）流水号={data.get('data')}",
            flush=True,
        )
        return True

    if data.get("code") == 200:
        print(
            f"[通知成功] {kind_tag}{title}（HTTP 兜底 → {target}）流水号={data.get('data')}",
            flush=True,
        )
        return True
    print(
        f"[通知失败] PushPlus 返回: code={data.get('code')} msg={data.get('msg')}",
        flush=True,
    )
    return False


_RESULT_URL = "https://www.pushplus.plus/api/open/message/sendMessageResult"


def query_send_result(short_code: str, access_key: str = "", timeout: float = 8) -> dict | None:
    """
    查询消息投递结果（官方开放接口）。

    返回 data：{"status": 0未投递/1发送中/2已发送/3发送失败, "errorMessage": ...}
    需要开放接口 access-key（个人中心 → 接口设置）；没配 key 时返回 None。
    """
    if not access_key:
        return None
    url = f"{_RESULT_URL}?shortCode={short_code}"
    req = urllib.request.Request(url, headers={"access-key": access_key}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[通知] 查询投递结果失败: {e}", flush=True)
        return None
    if data.get("code") != 200:
        print(f"[通知] 查询投递结果返回异常: {data}", flush=True)
        return None
    return data.get("data")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PushPlus 发送（HTTPS→HTTP 兜底）")
    parser.add_argument("--title", required=True)
    parser.add_argument("--content", required=True)
    parser.add_argument("--token", default="")
    parser.add_argument(
        "--topic",
        default="",
        help="群组编码（一对多群组）。留空则自动读配置",
    )
    parser.add_argument(
        "--to",
        default="",
        help="好友令牌（发给指定好友）。留空则自动读配置",
    )
    parser.add_argument(
        "--kind",
        default="",
        help=(
            "通知类别，决定是否转发给好友。"
            f"可选：{', '.join(sorted(FRIEND_KIND_WHITELIST))}（转发给好友），"
            "其它类别或留空 = 只发本人"
        ),
    )
    args = parser.parse_args()
    ok = send_pushplus(
        args.title,
        args.content,
        token=args.token or None,
        topic=args.topic,
        to=args.to,
        kind=args.kind,
    )
    raise SystemExit(0 if ok else 1)
