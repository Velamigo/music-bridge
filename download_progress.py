"""Stream curated download progress without exposing extractor URLs or request data."""
import json
import math
import os
import signal
import subprocess
import threading

def number(value):
    try:
        n=float(value)
        return n if math.isfinite(n) and n>=0 else None
    except (TypeError,ValueError):return None

def event(line):
    if line.startswith('MB_STAGE '):
        try:
            raw=json.loads(line[9:])
            if not isinstance(raw,dict):return None
            if raw.get('stage') in ('resolving','tagging'):
                return {'stage':raw['stage'],'progress':None,'message':'正在连接音源…' if raw['stage']=='resolving' else '下载完成，正在整理歌曲信息…'}
        except (ValueError,TypeError):pass
    if not line.startswith('MB_PROGRESS '):return None
    try:raw=json.loads(line[12:])
    except (ValueError,TypeError):return None
    if not isinstance(raw,dict):return None
    downloaded=number(raw.get('downloaded')) or 0
    total=number(raw.get('total')) or number(raw.get('estimate'))
    speed=number(raw.get('speed'));eta=number(raw.get('eta'))
    percent=min(100,downloaded/total*100) if total else None
    return {'stage':'downloading','message':'正在下载音频…','progress':{
        'downloaded':downloaded,'total':total,'speed':speed,'eta':eta,'percent':percent}}

def run(cmd, onprogress, timeout=900):
    process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1,start_new_session=True)
    expired=threading.Event()
    def terminate():
        if process.poll() is None:
            expired.set()
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
    timer=threading.Timer(timeout,terminate);timer.daemon=True;timer.start()
    tail=[]
    try:
        for line in process.stdout:
            parsed=event(line.strip())
            if parsed:onprogress(parsed)
            else:
                tail.append(line)
                if len(tail)>40:tail.pop(0)
        code=process.wait()
        if expired.is_set():raise subprocess.TimeoutExpired(cmd,timeout)
        if code:
            from music_bridge import user_error
            raise RuntimeError(user_error(''.join(tail)))
    finally:
        timer.cancel()
        if process.poll() is None:terminate();process.wait()
        process.stdout.close()
