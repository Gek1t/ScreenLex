"""
ScreenLex — Windows 触控学术课堂实时屏幕取词常驻客户端
启动脚本: python run_desktop.py
"""
import sys
import os
import multiprocessing

# 确保 desktop 包在路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _acquire_startup_guard():
    """命名互斥锁串行化单实例切换，并优雅等待旧实例释放 F8 热键与文件句柄，杜绝双击双开互杀。"""
    mutex_handle = None
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        mutex_handle = kernel32.CreateMutexW(None, False, "Local\\ScreenLexStartupGuard")
        if mutex_handle:
            kernel32.WaitForSingleObject(mutex_handle, 4000)
    except Exception:
        mutex_handle = None
    return mutex_handle


def _release_startup_guard(mutex_handle):
    if not mutex_handle:
        return
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ReleaseMutex(mutex_handle)
        kernel32.CloseHandle(mutex_handle)
    except Exception:
        pass


if __name__ == "__main__":
    # 严格遵循 AGENTS.md §7.3：freeze_support 必须先于任何业务模块导入与单实例清理执行
    multiprocessing.freeze_support()

    _guard = _acquire_startup_guard()
    try:
        import psutil
        current_pid = os.getpid()
        old_procs = []
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.info['pid'] != current_pid:
                    pname = (proc.info.get('name') or '').lower()
                    if pname == 'screenlex.exe':
                        proc.terminate()
                        old_procs.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if old_procs:
            try:
                psutil.wait_procs(old_procs, timeout=2.0)
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _release_startup_guard(_guard)

    from desktop.app import main
    main()

