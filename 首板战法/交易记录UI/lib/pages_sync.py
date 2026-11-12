# -*- coding: utf-8 -*-
"""交易落盘 → 导出 docs/trade-ui →（可选）git commit/push 同步 GitHub Pages。

重要：早盘 ensure / cron 是短生命周期进程。进程内 threading.Timer
在父进程退出后会被取消，导致「本机 HTML 有今日选票、Pages 没有」。
因此防抖必须用独立 shell `sleep` + token，保证父进程退出后仍会执行。
"""
from __future__ import annotations

import os
import json
import signal
import tempfile
import fcntl
from contextlib import contextmanager
import shlex
import subprocess
import sys
import threading
import time
from typing import Any

from runtime_paths import BASE_DIR, DATA_DIR, ensure_sys_path

ensure_sys_path()

_JOB_SCRIPT = os.path.join(BASE_DIR, "jobs", "sync_trade_ui_pages.py")
# 交易记录UI → 首板战法 → 工作区 → stock-quant-select
_REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "stock-quant-select"))
_TOKEN_PATH = os.path.join(DATA_DIR, ".pages_sync_token")
PAGES_URL = "https://shen9220.github.io/stock-quant-select/trade-ui/"

_lock = threading.Lock()
_DEBOUNCE_SEC = float(os.environ.get("TRADE_UI_PAGES_DEBOUNCE", "20") or 20)
_LAST_RESULT: dict[str, Any] = {}


def _pages_review_held() -> bool:
    shared_hold = os.path.join(_REPO_ROOT, "首板战法", "交易记录UI", "data", "PAGES_REVIEW_HOLD")
    return any(os.path.exists(path) for path in (os.path.join(DATA_DIR, "PAGES_REVIEW_HOLD"), shared_hold))


def pages_sync_enabled() -> bool:
    if _pages_review_held():
        return False
    v = (os.environ.get("TRADE_UI_PAGES_SYNC") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def pages_git_push_enabled() -> bool:
    if _pages_review_held():
        return False
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


def _write_sync_state(**values):
    os.makedirs(DATA_DIR,exist_ok=True)
    path=os.path.join(DATA_DIR,'pages_sync_state.json')
    try:
        with open(path) as file:state=json.load(file)
    except (OSError,ValueError):state={}
    state.update(values,updated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
    fd,tmp=tempfile.mkstemp(prefix='pages-state-',suffix='.tmp',dir=DATA_DIR)
    with os.fdopen(fd,'w') as file:json.dump(state,file,ensure_ascii=False,indent=2)
    os.replace(tmp,path)


@contextmanager
def _process_lock():
    os.makedirs(DATA_DIR,exist_ok=True)
    with open(os.path.join(DATA_DIR,'pages_publish.lock'),'a') as file:
        fcntl.flock(file,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(file,fcntl.LOCK_UN)


def _run_git(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    if os.environ.get('TRADE_UI_PAGES_DEADLINE'):
        timeout=min(timeout,max(.1,float(os.environ['TRADE_UI_PAGES_DEADLINE'])-time.time()))
    env=dict(os.environ,GIT_TERMINAL_PROMPT='0',GIT_SSH_COMMAND='ssh -o BatchMode=yes -o ConnectTimeout=10')
    command=['git',*args]
    process=subprocess.Popen(command,cwd=_REPO_ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             text=True,env=env,start_new_session=True)
    try:
        out,err=process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command,process.returncode,out,err)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGKILL)
        out,err=process.communicate()
        return subprocess.CompletedProcess(command,124,out,(err or '')+' Git request timed out')


def _https_remote(url):
    if url.startswith('git@github.com:'):return 'https://github.com/'+url.split(':',1)[1]
    if url.startswith('ssh://git@github.com/'):return 'https://github.com/'+url.split('github.com/',1)[1]
    return url


def _git_push_pages() -> dict[str, Any]:
    if not pages_git_push_enabled():return {'pushed':False,'reason':'git_push_disabled'}
    paths=['docs/trade-ui','docs/index.html','docs/.nojekyll']
    st=_run_git(['status','--porcelain','--',*paths])
    if st.returncode:return {'pushed':False,'error':'git status failed'}
    if st.stdout.strip():
        for attempt in range(5):
            added=_run_git(['add','--',*paths])
            if not added.returncode:break
            if 'index.lock' not in added.stderr:break
            time.sleep(1)
        if added.returncode:return {'pushed':False,'error':(added.stderr or 'git add failed')[:500]}
        commit=_run_git(['commit','-m',f"chore(pages): sync trade-ui snapshot {time.strftime('%Y-%m-%d %H:%M:%S')}"])
        if commit.returncode and 'nothing to commit' not in (commit.stdout+commit.stderr).lower():
            return {'pushed':False,'error':(commit.stderr or commit.stdout)[:400]}
    remote=_run_git(['remote','get-url','origin'])
    if remote.returncode:return {'pushed':False,'error':'origin missing'}
    url=_https_remote(remote.stdout.strip())
    config=['-c','http.lowSpeedLimit=1024','-c','http.lowSpeedTime=20']
    if url.startswith('https://github.com/'):
        config+=['-c','credential.helper=','-c','credential.helper=!gh auth git-credential']
    head=_run_git(['rev-parse','HEAD']).stdout.strip()
    observed=_run_git([*config,'ls-remote',url,'refs/heads/main'],timeout=30)
    if observed.returncode==0 and observed.stdout.split() and observed.stdout.split()[0]==head:
        return {'pushed':False,'reason':'already_current','commit':head}
    # A prior failed push may leave a clean worktree and unpublished commits.
    # Retry that push even when this export introduced no file changes.
    push=_run_git([*config,'push',url,'HEAD:main'],timeout=120)
    if push.returncode:return {'pushed':False,'error':(push.stderr or push.stdout or 'git push failed')[:500]}
    return {'pushed':True,'commit':head}


def sync_pages_now(*, push: bool | None = None) -> dict[str, Any]:
    if _pages_review_held():return {'ok':False,'reason':'awaiting_user_ui_review','pushed':False}
    with _lock,_process_lock():
        if _pages_review_held():return {'ok':False,'reason':'awaiting_user_ui_review','pushed':False}
        started=time.time()
        _write_sync_state(status='exporting',started_at=time.strftime('%Y-%m-%d %H:%M:%S'),error=None)
        try:
            from pages_export import export_pages_bundle
            summary=export_pages_bundle()
            _write_sync_state(status='pushing',exported_at=summary.get('exported_at'))
            do_push=pages_git_push_enabled() if push is None else bool(push)
            summary['git']=_git_push_pages() if do_push else {'pushed':False,'reason':'skipped'}
            if summary['git'].get('error'):summary['ok']=False
            _write_sync_state(status='success' if summary.get('ok') else 'failed',
                exported_at=summary.get('exported_at'),commit=summary['git'].get('commit'),
                pushed=summary['git'].get('pushed',False),error=summary['git'].get('error'),
                elapsed_seconds=round(time.time()-started,2))
        except Exception as exc:
            _write_sync_state(status='failed',error=str(exc)[:500],elapsed_seconds=round(time.time()-started,2))
            raise
        global _LAST_RESULT
        _LAST_RESULT=summary
        print(f"[pages-sync] ok={summary.get('ok')} exported={summary.get('exported_at')} git={summary.get('git')}",flush=True)
        return summary


def html_newer_than_pages() -> bool:
    """本机交易页 HTML 比 Pages 副本新，就该再导出。"""
    src = os.path.join(BASE_DIR, "templates", "index.html")
    dst = os.path.join(_REPO_ROOT, "docs", "trade-ui", "index.html")
    try:
        src_m = os.stat(src).st_mtime
    except OSError:
        return False
    try:
        dst_m = os.stat(dst).st_mtime
    except OSError:
        return True
    return src_m > dst_m + 0.5


def schedule_pages_sync_if_html_changed() -> None:
    if html_newer_than_pages():
        print("[pages-sync] 检测到 HTML 已更新，调度 Pages 同步", flush=True)
        schedule_pages_sync(delay=5)


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
        log_file = open(os.path.join(DATA_DIR, "pages_sync.log"), "a")
        subprocess.Popen(
            ["/bin/bash", "-c", cmd],
            cwd=BASE_DIR,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_file.close()
        print(f"[pages-sync] 已调度独立同步 delay={wait:.0f}s；结果写入 pages_sync.log", flush=True)
    except Exception as e:
        print(f"[pages-sync] 调度失败: {e}", flush=True)
