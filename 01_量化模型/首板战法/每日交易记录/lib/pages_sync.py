# -*- coding: utf-8 -*-
"""交易落盘 → 导出 docs/trade-ui →（可选）git commit/push 同步 GitHub Pages。

重要：早盘 ensure / cron 是短生命周期进程。进程内 threading.Timer
在父进程退出后会被取消，导致「本机 HTML 有今日选票、Pages 没有」。
因此防抖必须用独立 shell `sleep` + token，保证父进程退出后仍会执行。
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
from typing import Any

from runtime_paths import BASE_DIR, DATA_DIR, ensure_sys_path

ensure_sys_path()

_JOB_SCRIPT = os.path.join(BASE_DIR, "jobs", "sync_trade_ui_pages.py")
_REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", "..", ".."))
_TOKEN_PATH = os.path.join(DATA_DIR, ".pages_sync_token")
PAGES_URL = "https://shen9220.github.io/stock-quant-select/trade-ui/"

_lock = threading.Lock()
_DEBOUNCE_SEC = float(os.environ.get("TRADE_UI_PAGES_DEBOUNCE", "20") or 20)
_LAST_RESULT: dict[str, Any] = {}


def pages_sync_enabled() -> bool:
    v = (os.environ.get("TRADE_UI_PAGES_SYNC") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def pages_git_push_enabled() -> bool:
    v = (os.environ.get("TRADE_UI_PAGES_GIT_PUSH") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _crypto_python() -> str:
    """优先能 import Crypto / cryptography 的解释器（本机 3.9 系统 Python）。"""
    candidates = [
        os.environ.get("TRADE_UI_PAGES_PYTHON", "").strip(),
        "/usr/bin/python3",
        sys.executable,
        "python3",
    ]
    for py in candidates:
        if not py:
            continue
        try:
            r = subprocess.run(
                [
                    py,
                    "-c",
                    "import importlib;\n"
                    "ok=False\n"
                    "for m in ('Crypto.Cipher.AES','cryptography.hazmat.primitives.ciphers.aead'):\n"
                    "  try:\n"
                    "    importlib.import_module(m); ok=True; break\n"
                    "  except Exception:\n"
                    "    pass\n"
                    "raise SystemExit(0 if ok else 1)",
                ],
                capture_output=True,
                timeout=20,
                check=False,
            )
            if r.returncode == 0:
                return py
        except Exception:
            continue
    return sys.executable


def _run_git(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git_push_pages() -> dict[str, Any]:
    if not pages_git_push_enabled():
        return {"pushed": False, "reason": "git_push_disabled"}

    paths = [
        "docs/trade-ui",
        "docs/index.html",
        "docs/.nojekyll",
    ]
    st = _run_git(["status", "--porcelain", "--", *paths])
    if st.returncode != 0:
        return {"pushed": False, "error": (st.stderr or st.stdout or "git status failed")[:300]}
    if not (st.stdout or "").strip():
        return {"pushed": False, "reason": "no_changes"}

    add = _run_git(["add", "--", *paths])
    if add.returncode != 0:
        return {"pushed": False, "error": (add.stderr or "git add failed")[:300]}

    msg = f"chore(pages): sync trade-ui snapshot {time.strftime('%Y-%m-%d %H:%M:%S')}"
    commit = _run_git(["commit", "-m", msg])
    out = (commit.stdout or "") + (commit.stderr or "")
    if commit.returncode != 0 and "nothing to commit" not in out.lower():
        return {"pushed": False, "error": out[:300]}

    push = _run_git(["push", "origin", "HEAD"], timeout=180)
    if push.returncode != 0:
        return {
            "pushed": False,
            "error": ((push.stderr or push.stdout or "git push failed")[:400]),
        }
    return {"pushed": True}


def sync_pages_now(*, push: bool | None = None) -> dict[str, Any]:
    """立即导出；默认按环境变量决定是否 push。"""
    with _lock:
        from pages_export import export_pages_bundle

        summary = export_pages_bundle()
        do_push = pages_git_push_enabled() if push is None else bool(push)
        if do_push:
            summary["git"] = _git_push_pages()
        else:
            summary["git"] = {"pushed": False, "reason": "skipped"}
        global _LAST_RESULT
        _LAST_RESULT = summary
        print(
            f"[pages-sync] export ok total={summary.get('total')} "
            f"git={summary.get('git')} url={PAGES_URL}",
            flush=True,
        )
        return summary


def schedule_pages_sync(*, delay: float | None = None) -> None:
    """交易落盘后调用：防抖后在独立进程同步（父进程退出也执行）。"""
    if not pages_sync_enabled():
        return
    wait = max(0.0, _DEBOUNCE_SEC if delay is None else float(delay))
    py = _crypto_python()
    push_flag = "--push" if pages_git_push_enabled() else "--no-push"
    token = f"{time.time():.6f}"
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(_TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(token)

    # 独立 shell：sleep 后再比对 token，实现跨进程防抖；start_new_session 防 SIGHUP
    cmd = (
        f"sleep {wait:.0f}; "
        f'tok=$(cat {shlex.quote(_TOKEN_PATH)} 2>/dev/null || true); '
        f'[ "$tok" = {shlex.quote(token)} ] || exit 0; '
        f"exec {shlex.quote(py)} {shlex.quote(_JOB_SCRIPT)} {push_flag}"
    )
    try:
        subprocess.Popen(
            ["/bin/bash", "-c", cmd],
            cwd=BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print(f"[pages-sync] 已调度独立同步 delay={wait:.0f}s token={token[-6:]}", flush=True)
    except Exception as e:
        print(f"[pages-sync] 调度失败: {e}", flush=True)
