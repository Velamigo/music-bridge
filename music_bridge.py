#!/usr/bin/env python3
"""Search, download native M4A/AAC, tag without encoding."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
YTDLP = str(ROOT / '.venv/bin/yt-dlp') if (ROOT / '.venv/bin/yt-dlp').exists() else 'yt-dlp'

PROGRESS_TEMPLATE = 'download:MB_PROGRESS {"downloaded":%(progress.downloaded_bytes)j,"total":%(progress.total_bytes|0)j,"estimate":%(progress.total_bytes_estimate|0)j,"speed":%(progress.speed|null)j,"eta":%(progress.eta|null)j,"status":%(progress.status)j}'

def run(args):
    return subprocess.check_output(args, text=True)

def user_error(raw):
    if 'Requested format is not available' in raw:
        return '此音源目前没有可下载的原生 M4A/AAC。请从下拉框换一个音源；其他歌曲可以继续下载。'
    if '429' in raw:
        return 'YouTube 请求过于频繁，请等几分钟再试。'
    if 'Sign in to confirm' in raw or 'not a bot' in raw:
        return 'YouTube 要求登录或机器人验证，请稍后重试或换一个音源。'
    if '403: Forbidden' in raw:
        return 'YouTube 拒绝了这个音源的下载，请稍后重试或换一个音源。'
    lines = [x for x in raw.splitlines() if x.startswith(('ERROR:', '失败：'))]
    return (lines[-1] if lines else raw[-600:]).strip()

def inspect(path):
    data = json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(path)]))
    streams = data['streams']
    if path.suffix.lower() != '.m4a' or len(streams) != 1 or streams[0].get('codec_name') != 'aac':
        raise RuntimeError('文件不是单一 AAC 音轨的 M4A，已停止。')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest='action', required=True)
    s = subs.add_parser('search', help='搜索候选；不会自动下载搜索第一名')
    s.add_argument('query')
    s.add_argument('--count', type=int, default=5, choices=range(1, 21))
    d = subs.add_parser('download', help='下载选定的 YouTube 链接')
    d.add_argument('url')
    d.add_argument('--title')
    d.add_argument('--artist')
    d.add_argument('--album')
    d.add_argument('--track')
    d.add_argument('--disc')
    d.add_argument('--directory')
    a = p.parse_args()
    if a.action == 'search':
        data = json.loads(run([YTDLP, '--ignore-config', '--flat-playlist', '--dump-single-json',
                              '--', f'ytsearch{a.count}:{a.query}']))
        for i, item in enumerate((x for x in data.get('entries', []) if x), 1):
            print(f"{i}. {item.get('title')} | {item.get('channel') or item.get('uploader') or ''} | {item.get('duration')} 秒")
            print(item.get('webpage_url') or item.get('url'))
        return
    parsed = urlparse(a.url)
    if parsed.scheme != 'https' or parsed.hostname not in ('youtube.com', 'www.youtube.com', 'music.youtube.com', 'youtu.be'):
        raise RuntimeError('请输入 HTTPS YouTube 链接。')
    target = Path(a.directory) if a.directory else ROOT / 'downloads'
    target.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target) as tmp:
        report = Path(tmp) / 'path.txt'
        common = [YTDLP, '--ignore-config', '--no-playlist', '--no-overwrites',
                  '--newline', '--progress', '--progress-delta', '0.5', '--no-colors',
                  '--progress-template', PROGRESS_TEMPLATE,
                  '-f', 'bestaudio[ext=m4a][acodec^=mp4a]',
                  '-o', str(Path(tmp) / '%(id)s.%(ext)s'),
                  '--print-to-file', 'after_move:filepath', str(report), '--', a.url]
        for client in ('web_embedded', None):
            cmd = common[:1] + (['--extractor-args', 'youtube:player_client=' + client] if client else []) + common[1:]
            print('MB_STAGE '+json.dumps({'stage':'resolving'}),flush=True)
            child=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
            log=[]
            for line in child.stdout:
                if line.startswith('MB_PROGRESS '):print(line.strip(),flush=True)
                else:
                    log.append(line)
                    if len(log)>40:log.pop(0)
            child.wait();child.stdout.close()
            result=subprocess.CompletedProcess(cmd,child.returncode,stderr=''.join(log))
            if result.returncode == 0:
                break
            if client and 'Requested format is not available' in result.stderr:
                print('当前客户端未提供 M4A，尝试默认客户端…', flush=True)
                continue
            raise RuntimeError(user_error(result.stderr))
        print('MB_STAGE '+json.dumps({'stage':'tagging'}),flush=True)
        source = Path(report.read_text().strip())
        inspect(source)
        dest = target / source.name
        if dest.exists():
            raise RuntimeError(f'文件已存在：{dest}；未覆盖。')
        tags = []
        for key in ('title', 'artist', 'album', 'track', 'disc'):
            if getattr(a, key):
                tags.extend(['-metadata', f'{key}={getattr(a, key)}'])
        if tags:
            tagged = Path(tmp) / 'tagged.m4a'
            subprocess.run(['ffmpeg', '-v', 'error', '-n', '-i', str(source), '-map', '0:a:0',
                            '-c:a', 'copy', *tags, str(tagged)], check=True)
            inspect(tagged)
            tagged.rename(dest)
        else:
            source.rename(dest)
    inspect(dest)
    print(f'已保存：{dest}')

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError, KeyError) as exc:
        print(f'失败：{exc}', file=sys.stderr)
        sys.exit(1)
