"""Read-only metadata reports and explicit, account-bound confirmation plans."""
import json
import os
import re
import secrets
import official_metadata
import time
from pathlib import Path
from opencc import OpenCC
from ncm_client import CLIENT, STATE, NcmError, read_ledger, write_ledger

PLANS = {}
REPORTS = {}
SNAPSHOTS = {}
CC = OpenCC('t2s')
UNKNOWN = re.compile(r'^(未知.*|unknown.*|undefined|null|none|)$', re.I)

def norm(text):
    return ''.join(x for x in CC.convert(str(text)).lower() if x.isalnum())

def current_user(expected):
    user = CLIENT.public.get('user')
    if not user or str(expected) != user['id']:
        raise NcmError('请确认登录账号后重新操作。')
    return user['id']

def rpc(action, uid, **kwargs):
    return CLIENT.call('manage_' + action, dict(user_id=uid, **kwargs), timeout=180)

def keep(mapping, value):
    now = time.time()
    for key in list(mapping):
        if mapping[key]['expires'] < now:
            del mapping[key]
    if len(mapping) >= 300:
        del mapping[next(iter(mapping))]
    key = secrets.token_hex(24)
    mapping[key] = dict(value, expires=now + 900)
    return key

def valid(mapping, token, uid):
    value = mapping.get(str(token))
    if not value or value['expires'] < time.time() or value['uid'] != uid:
        raise NcmError('检查或确认已过期，请重新检查。')
    return value

def snapshot(body):
    with CLIENT.lock:
        uid=current_user(body.get('user_id'))
        rows=rpc('snapshot',uid)['items']
        token=keep(SNAPSHOTS,{'uid':uid,'items':{x['id']:x for x in rows}})
        return {'snapshot_id':token,'count':len(rows)}

def check(body):
    with CLIENT.lock:
        uid = current_user(body.get('user_id'))
        if body.get('snapshot_id'):
            snapshot=valid(SNAPSHOTS,body['snapshot_id'],uid)
            before=snapshot['items'].get(str(body.get('id')))
            if not before: raise NcmError('歌曲不在本次云盘快照中，请刷新。')
        else:
            before = rpc('detail', uid, id=body.get('id'))
    issues = []
    for field, label in (('title', '歌名'), ('artist', '歌手'), ('album', '专辑')):
        if UNKNOWN.fullmatch(before.get('stored', {}).get(field, '')):
            issues.append('原标签缺少' + label)
    title = re.sub(r'\.(m4a|mp3|flac|wav|webm)$', '', before['title'], flags=re.I).strip()
    if title != before['title']:
        issues.append('歌名包含文件后缀')
    query = str(body.get('query') or '').strip()
    query = re.sub(r'\.(m4a|mp3|flac|wav|webm)\b', '', query, flags=re.I).strip()
    if not query:
        query = ' '.join(x for x in (title, before.get('artist', '')) if not UNKNOWN.fullmatch(x))
    candidates = []
    artist = str(body.get('artist', before.get('artist', ''))).strip()
    title = str(body.get('title') or title).strip()
    references = []
    source_error = ''
    try:
        references = official_metadata.lookup(title, artist, album=before.get('album',''), hour=int(time.time()//3600))
    except Exception:
        source_error = '发行目录 API 暂时无法读取，请稍后重试；未改用普通上传搜索。'
    if references:
        source_error = '已查询正式发行目录并核对歌手、歌名和专辑曲目。多个专辑可能收录同一首歌，请核对具体版本。资料查询不会修改云盘；网易云关联目标尚未验证时不提供改绑。'
    token = keep(REPORTS, dict(uid=uid, before=before, candidates=candidates))
    return {'report_id': token, 'before': before, 'issues': issues, 'candidates': candidates,
            'references': references, 'query': query, 'title_query': title, 'artist_query': artist,
            'message': source_error or ('请填写歌手后重查；无法确认官方来源时不推荐。' if not artist else '发行目录中未找到歌名和歌手均一致的记录，请核对名称。不会回退到普通用户上传结果。')}

def prepare(body):
    with CLIENT.lock:
        uid = current_user(body.get('user_id'))
        kind = body.get('kind')
        if kind == 'delete':
            before = rpc('detail', uid, id=body.get('id'))
            after = None
        elif kind == 'match':
            report = valid(REPORTS, body.get('report_id'), uid)
            before = report['before']
            selected = next((x for x in report['candidates'] if x['id'] == str(body.get('target_id'))), None)
            if not selected:
                raise NcmError('请选择本次检查中的候选版本。')
            after = rpc('song', uid, target_id=selected['id'])
            latest = rpc('detail', uid, id=before['id'])
            if latest['fingerprint'] != before['fingerprint']:
                raise NcmError('歌曲信息已变化，请重新联网检查。')
        else:
            raise NcmError('无效的操作。')
        token = keep(PLANS, dict(uid=uid, kind=kind, before=before, after=after))
        return {'plan_id': token, 'kind': kind, 'before': before, 'after': after,
                'message': '将永久删除这首云盘歌曲，本工具无法撤销。本地已下载文件保留。' if kind == 'delete' else '仅提交曲库关联修正，原文件标签可能仍由平台保留。确认前不会修改。'}

def apply(body):
    with CLIENT.lock:
        uid = current_user(body.get('user_id'))
        plan = valid(PLANS, body.get('plan_id'), uid)
        if body.get('confirmed') is not True:
            raise NcmError('需要你确认预览中的具体操作。')
        del PLANS[str(body['plan_id'])]  # One-shot; an uncertain response is never blindly replayed.
        before = plan['before']
        result = rpc(plan['kind'], uid, id=before['id'], fingerprint=before['fingerprint'], confirmed=True,
                     target_id=plan['after']['id'] if plan['after'] else None)
        if result['state'] == 'verified':
            try:
                ledger = read_ledger()
                for key, record in ledger.items():
                    same_file = before.get('filename') and record.get('filename') == before['filename']
                    if key.startswith(uid + ':') and (str(record.get('cloud_id')) == before['id'] or same_file):
                        if plan['kind'] == 'delete':
                            record.update(state='deleted', message='该文件已从云盘删除，本地文件保留', updated_at=time.time())
                        else:
                            record.update(cloud_id=result['after']['id'], updated_at=time.time())
                write_ledger(ledger)
                path, folders_data, folders_value = load_folders(uid)
                assigned = folders_value['assignments'].pop(before['id'], None)
                if assigned and plan['kind'] == 'match':
                    folders_value['assignments'][result['after']['id']] = assigned
                save_folders(path, folders_data)
            except (NcmError, OSError):
                result['message'] += ' 本地记录更新失败，请刷新后核对。'
        return result

def load_folders(uid):
    path = STATE / 'cloud-folders.json'
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):
            raise NcmError('本机分类配置无法读取，请先检查配置。') from None
    else:
        data = {}
    return path, data, data.setdefault(uid, {'folders': [], 'assignments': {}})

def save_folders(path, data):
    STATE.mkdir(mode=0o700, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
        json.dump(data, f, ensure_ascii=False)
    temp.replace(path)

def folders(body, write=False):
    with CLIENT.lock:
        uid = current_user(body.get('user_id'))
        path, data, value = load_folders(uid)
        if write:
            if body.get('operation') == 'create':
                name = str(body.get('name', '')).strip()
                if not name or len(name) > 60:
                    raise NcmError('分类名称需为 1–60 字。')
                if any(x['name'] == name for x in value['folders']):
                    raise NcmError('已有同名分类。')
                value['folders'].append({'id': secrets.token_hex(8), 'name': name})
            elif body.get('operation') == 'assign':
                folder_id = str(body.get('folder_id', ''))
                if folder_id and not any(x['id'] == folder_id for x in value['folders']):
                    raise NcmError('分类已变化，请刷新。')
                row = rpc('detail', uid, id=body.get('id'))
                if folder_id:
                    value['assignments'][row['id']] = folder_id
                else:
                    value['assignments'].pop(row['id'], None)
            else:
                raise NcmError('无效的分类操作。')
            save_folders(path, data)
        return value
