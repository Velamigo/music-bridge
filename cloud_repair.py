"""Account-bound bulk cloud matching previews; no writes until explicit confirmation."""
import time
import cloud_manage as manage
from ncm_client import NcmError,CLIENT
PLANS={}
def preview(body,progress=lambda text:None):
    with CLIENT.lock:
        uid=manage.current_user(body.get('user_id'))
        rows=manage.rpc('snapshot',uid)['items']
    entries=[];skipped=[];targets=set();existing={x['id'] for x in rows}
    for index,before in enumerate(rows):
        progress(f'核对网易云版本 {index+1}/{len(rows)}：{before["title"]}')
        title=before['title'];artist=before.get('artist','');album=before.get('album','')
        if manage.UNKNOWN.fullmatch(album):album=''
        try:
            if not artist or manage.UNKNOWN.fullmatch(artist):raise NcmError('缺少歌手，需先补全信息')
            refs=manage.official_metadata.lookup(title,artist,album=album,hour=int(time.time()//3600))
            if not refs:raise NcmError('未找到一致的正式发行资料')
            if not album and len({manage.norm(r['album']) for r in refs})!=1:raise NcmError('收录于多张专辑，请先选择专辑')
            with CLIENT.lock:
                manage.current_user(uid)
                found=manage.rpc('search',uid,query=title+' '+artist)['items']
                options=[(row,ref) for row in found for ref in refs if manage.release_matches(row,ref)]
                if not options:raise NcmError('网易云未找到同一发行版本')
                selected,ref=options[0]
                after=manage.rpc('song',uid,target_id=selected['id'])
            if not manage.release_matches(after,ref):raise NcmError('网易云详情与发行资料不一致')
            if not after.get('cover') or not after.get('album_id'):raise NcmError('目标版本没有有效网易云专辑封面')
            if before.get('duration') and abs(before['duration']-after['duration'])>6:raise NcmError('音频时长与目标版本有差异，需单独核对')
            if before['catalog']['id']==after['id']:raise NcmError('已关联正确版本；网易云客户端仍无图需另行核查')
            if after['id'] in existing or after['id'] in targets:raise NcmError('目标版本已在云盘中，需单独核对重复歌曲')
            targets.add(after['id']);entries.append({'before':before,'after':after,'source':ref['url']})
        except NcmError as exc:skipped.append({'id':before['id'],'title':title,'reason':str(exc)})
        except Exception:skipped.append({'id':before['id'],'title':title,'reason':'查询暂时失败，未修改'})
    token=manage.keep(PLANS,dict(uid=uid,entries=entries))
    return dict(plan_id=token,count=len(rows),entries=entries,skipped=skipped)

def apply(body,progress=lambda text:None):
    with CLIENT.lock:
        uid=manage.current_user(body.get('user_id'))
        plan=manage.valid(PLANS,body.get('plan_id'),uid)
        if body.get('confirmed') is not True:raise NcmError('请先核对并确认预览')
        selected=body.get('ids')
        available={x['before']['id']:x for x in plan['entries']}
        if not isinstance(selected,list) or not selected or len(set(selected))!=len(selected) or any(x not in available for x in selected):raise NcmError('请选择本次预览中的歌曲')
        # Validate every selected snapshot before the first mutation.
        latest={x['id']:x for x in manage.rpc('snapshot',uid)['items']}
        for song_id in selected:
            if latest.get(song_id,{}).get('fingerprint')!=available[song_id]['before']['fingerprint']:raise NcmError('云盘内容已变化，请重新检查；尚未执行修改')
        del PLANS[str(body['plan_id'])]
        results=[]
        for index,song_id in enumerate(selected):
            item=available[song_id];progress(f'修正并核对 {index+1}/{len(selected)}：{item["before"]["title"]}')
            token=manage.keep(manage.PLANS,dict(uid=uid,kind='match',before=item['before'],after=item['after']))
            try:
                result=manage.apply(dict(user_id=uid,plan_id=token,confirmed=True))
                results.append(dict(title=item['before']['title'],**result))
                if result['state']!='verified':break
            except Exception:
                results.append(dict(title=item['before']['title'],state='unconfirmed',message='本次结果未确认，已停止后续操作；请核对云盘后再检查'))
                break
        return dict(results=results,remaining=len(selected)-len(results),message=f'已核对 {sum(x["state"]=="verified" for x in results)} / {len(selected)} 首的网易云匹配关系。客户端封面显示仍需刷新验证。')
