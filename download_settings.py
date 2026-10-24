import json
from pathlib import Path
import tempfile
ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / '.state' / 'download-directory.json'
def folder():
    if CONFIG.exists():
        return Path(json.loads(CONFIG.read_text())['folder'])
    return ROOT / 'downloads'
def save(value):
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError('请填写下载目录的完整路径。')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise RuntimeError('请填写以 / 开头的完整路径。')
    path = path.resolve()
    if not path.is_dir():
        raise RuntimeError('目录不存在，请先创建目录，再保存。')
    try:
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError:
        raise RuntimeError('这个目录无法写入，请选择其他目录。') from None
    CONFIG.parent.mkdir(mode=0o700, exist_ok=True)
    temp = CONFIG.with_suffix('.tmp')
    temp.write_text(json.dumps({'folder': str(path)}, ensure_ascii=False))
    temp.chmod(0o600)
    temp.replace(CONFIG)
    return path

PICKER_LOCK = __import__('threading').Lock()
def choose():
    import subprocess
    import sys
    if sys.platform != 'darwin':
        raise RuntimeError('系统文件夹选择器目前支持 Mac。')
    if not PICKER_LOCK.acquire(blocking=False):
        raise RuntimeError('文件夹选择窗口已经打开，请先完成选择。')
    try:
        source = ROOT / 'folder_picker.m'
        bundle = ROOT / '.state' / 'FolderPicker.app' / 'Contents'
        executable = bundle / 'MacOS' / 'FolderPicker'
        executable.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        import plistlib
        (bundle / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'local.musicbridge.folderpicker', 'CFBundleName': '音乐下载目录', 'CFBundleExecutable': 'FolderPicker', 'CFBundlePackageType': 'APPL', 'LSUIElement': True}))
        if not executable.exists() or executable.stat().st_mtime < source.stat().st_mtime:
            try:
                subprocess.run(['/usr/bin/clang', '-fobjc-arc', '-framework', 'Cocoa', str(source), '-o', str(executable)],
                               capture_output=True, check=True, timeout=90)
            except (OSError, subprocess.SubprocessError):
                raise RuntimeError('文件夹选择器无法启动，请检查本机开发工具是否可用。') from None
        result = subprocess.run([str(executable), str(folder())], capture_output=True,
                                text=True, check=True, timeout=600)
        selected = result.stdout.strip()
        return selected or None
    finally:
        PICKER_LOCK.release()
