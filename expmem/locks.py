# -*- coding: utf-8 -*-
"""跨平台进程文件锁（11-12 下一批）。

为什么独立成模块：skills.file_lock 原实现 import fcntl——POSIX-only，仓库
在 Windows 测试机上跑不了（Windows 无 fcntl），而 Windows 侧有 msvcrt.locking
同语义。锁语义：
  - POSIX：flock LOCK_EX（同进程内重复打开不互斥，跨进程互斥——本仓每进程
    一把锁的用法正合适）；
  - Windows：msvcrt.locking LK_LOCK（被占时每秒重试，~10s 后抛 OSError——
    比死等好，锁竞争本身就是异常状态）。区域锁锁 1 字节（offset 0），空文件
    也合法。
点前缀锁文件（.lock）不入检索——load_catalog 只扫 *.md，天然不装锁文件。"""
import contextlib
import os


def _lock_fh(fh):
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        return "flock"
    except ImportError:
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        return "msvcrt"


def _unlock_fh(fh, kind):
    if kind == "flock":
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    else:
        import msvcrt
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass              # 已随句柄释放（Windows 关闭自动解锁），不挡收尾


@contextlib.contextmanager
def exclusive_lock(path: str):
    """独占文件锁。点前缀文件（.xxx.lock）放数据目录里，与数据同生命周期。"""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fh = open(path, "a")
    kind = None
    try:
        kind = _lock_fh(fh)
        yield fh
    finally:
        if kind:
            try:
                _unlock_fh(fh, kind)
            finally:
                fh.close()
        else:
            fh.close()
