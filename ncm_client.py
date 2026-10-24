"""Local SDK worker + account-scoped upload ledger. No credential storage."""
import atexit
import download_settings
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parent
STATE = ROOT / '.state'
LEDGER = STATE / 'cloud-ledger.json'

class NcmError(RuntimeError):
    pass

class Client:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.public = {'connected': False, 'user': None, 'ready': self.installed()}
        atexit.register(self.close)

    @staticmethod
    def installed():
        return bool(shutil.which('node')) and (ROOT / 'node_modules/NeteaseCloudMusicApi/package.json').exists()

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.process = None
        self.public = {'connected': False, 'user': None, 'ready': self.installed()}

    def call(self, action, args=None, timeout=45):
        with self.lock:
            if not self.installed():
                raise NcmError('网易云组件尚未安装，请重新运行启动入口安装依赖。')
            if self.process is None or self.process.poll() is not None:
                self.close()
                self.process = subprocess.Popen(['node', str(ROOT / 'ncm_worker.cjs')], cwd=ROOT,
                                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL, text=True, bufsize=1)
            try:
                self.process.stdin.write(json.dumps({'action': action, 'args': args or {}}, ensure_ascii=False) + '\n')
                self.process.stdin.flush()
                ready, _, _ = select.select([self.process.stdout], [], [], timeout)
                if not ready:
                    self.close()
                    raise NcmError('网易云请求超时，登录会话已断开。若刚在上传，请先重新登录并核对云盘。')
                line = self.process.stdout.readline()
                result = json.loads(line)
            except (BrokenPipeError, OSError, ValueError):
                self.close()
                raise NcmError('网易云组件连接中断，请重新扫码登录。') from None
            self.public = result.get('session', self.public)
            if not result.get('ok'):
                raise NcmError(result.get('error', {}).get('message', '网易云请求失败。'))
            return result['data']

CLIENT = Client()

def read_ledger():
    if not LEDGER.exists():
        return {}
    try:
        return json.loads(LEDGER.read_text())
    except (ValueError, OSError):
        raise NcmError('云盘上传记录无法读取，已停止上传以避免重复，请先检查本地记录。') from None

def write_ledger(ledger):
    STATE.mkdir(mode=0o700, exist_ok=True)
    temp = STATE / 'cloud-ledger.tmp'
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as f:
        json.dump(ledger, f, ensure_ascii=False)
    temp.replace(LEDGER)

@lru_cache(maxsize=512)
def _digest(path, size, modified):
    result = hashlib.md5()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()

def digest(path):
    stat = path.stat()
    return _digest(str(path), stat.st_size, stat.st_mtime_ns)

def upload_state(path):
    uid = (CLIENT.public.get('user') or {}).get('id')
    if not uid:
        return None
    try:
        record = read_ledger().get(uid + ':' + digest(path))
    except NcmError:
        return {'state': 'error', 'message': '本地上传记录异常，请先核对。'}
    if record:
        return {k: record.get(k) for k in ('state', 'message', 'cloud_id', 'updated_at')}
    return None

def upload(path, expected_uid, verify_only=False, onprogress=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}\.m4a', path.name) or (path.is_symlink() or path.parent.resolve() != download_settings.folder().resolve()):
        raise NcmError('只能上传本工具下载的 M4A 文件。')
    report=onprogress or (lambda stage,text:None)
    with CLIENT.lock:
        report('verifying','正在检查网易云账号和已有记录…')
        status = CLIENT.call('refresh')
        uid = status['user']['id']
        if not expected_uid or str(expected_uid) != uid:
            raise NcmError('登录账号已变化，请刷新页面，确认目标账号后重新操作。')
        md5 = digest(path)
        key = uid + ':' + md5
        ledger = read_ledger()
        record = ledger.get(key, {})
        filename = path.stem + '_' + md5 + '.m4a'
        verification = {'filename': record.get('filename', filename), 'size': path.stat().st_size,
                        'cloud_id': record.get('cloud_id', '')}
        # Check remote content before uploading, including after a previously interrupted request.
        found = CLIENT.call('verify', verification, timeout=180) if record else {'found': False}
        if found.get('found'):
            record = dict(record, state='verified', message='已在网易云云盘核对到此文件', cloud_id=found['row']['id'],
                          filename=verification['filename'], updated_at=time.time())
            ledger[key] = record
            write_ledger(ledger)
            return {'state': 'verified', 'message': record['message'], 'cloud_id': record['cloud_id']}
        if verify_only:
            result = {'state': 'unconfirmed', 'message': '尚未在云盘核对到此文件，请稍后再核对。' if record else '此文件还没有本工具的上传记录。'}
            if record:
                record.update(result, updated_at=time.time())
                write_ledger(ledger)
            return result
        if record.get('state') in ('pending', 'accepted', 'unconfirmed'):
            raise NcmError('上次上传结果尚未确认。请先使用“核对云盘”；暂不自动重复上传。')
        if found.get('limited'):
            raise NcmError('云盘条目过多，尚未完成重复检查，请先核对后再上传。')
        record = {'state': 'pending', 'message': '正在上传，结果待核对', 'filename': filename, 'file': path.name, 'updated_at': time.time()}
        ledger[key] = record
        write_ledger(ledger)  # Persist before issuing the non-idempotent upload request.
        try:
            report('uploading','下载完成，正在上传网易云盘…')
            result = CLIENT.call('upload', {'file': path.name, 'path': str(path.resolve()), 'md5': md5}, timeout=300)
            record.update(state='accepted', message='接口已接受，正在核对云盘', cloud_id=result.get('cloud_id', ''), updated_at=time.time())
            write_ledger(ledger)
            verification.update(cloud_id=record['cloud_id'])
            report('verifying','上传已提交，正在核对网易云盘…')
            found = CLIENT.call('verify', verification, timeout=180)
            if found.get('found'):
                record.update(state='verified', message='已上传并在网易云云盘核对成功', cloud_id=found['row']['id'])
            else:
                record.update(state='unconfirmed', message='接口已接受，云盘尚未核对到；请稍后点“核对云盘”')
        except NcmError:
            record.update(state='unconfirmed', message='上传结果待确认，请先核对云盘，避免重复上传', updated_at=time.time())
            write_ledger(ledger)
            raise
        record['updated_at'] = time.time()
        write_ledger(ledger)
        return {k: record.get(k) for k in ('state', 'message', 'cloud_id')}
