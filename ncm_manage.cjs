const crypto = require('node:crypto');
function fail(message) { throw Object.assign(new Error(message), { safeMessage: message }); }
function id(value) { if (!/^\d{1,20}$/.test(String(value))) fail('无效的歌曲编号。'); return String(value); }
function catalog(song) {
  return { id: String(song.id || ''), title: song.name || '',
    artist: (song.ar || song.artists || []).map(x => x.name).filter(Boolean).join(' / '),
    album: (song.al || song.album || {}).name || '', duration: Math.round((song.dt || song.duration || 0) / 1000) };
}
function cloudRow(row) {
  const linked = catalog(row.simpleSong || {});
  const stored = { title: row.songName || '', artist: row.artist || '', album: row.album || '' };
  const result = { id: String(row.songId || row.simpleSong?.id || ''),
    title: stored.title || linked.title, artist: stored.artist || linked.artist, album: stored.album || linked.album,
    added_at: Number(row.addTime || 0), cover: /^https:\/\//.test(row.simpleSong?.al?.picUrl || '') ? row.simpleSong.al.picUrl : '',
    filename: row.fileName || '', size: Number(row.fileSize || 0), duration: linked.duration,
    stored, catalog: linked };
  result.fingerprint = crypto.createHash('sha256').update(JSON.stringify(result)).digest('hex');
  return result;
}
async function allRows(request) {
  const rows = [];
  for (let offset = 0; offset < 10000; offset += 100) {
    const data = await request('user_cloud', { limit: 100, offset });
    rows.push(...(data.data || []));
    if ((!data.hasMore && !data.more) || !data.data?.length) return rows;
  }
  fail('云盘超过本次检查上限，无法完整核对，已停止修改。');
}
async function ownRow(request, songId) {
  const row = (await allRows(request)).find(x => String(x.songId || x.simpleSong?.id) === songId);
  return row ? cloudRow(row) : null;
}
async function handle(action, args, context) {
  const { request, identity, getUser } = context;
  await identity();
  if (!getUser() || String(args.user_id) !== getUser().id) fail('账号已变化，请刷新页面并重新检查。');
  if (action === 'manage_snapshot') return {items:(await allRows(request)).map(cloudRow)};
  if (action === 'manage_search') {
    const query = String(args.query || '').trim();
    if (!query || query.length > 300) fail('请填写 1–300 字的歌曲或歌手线索。');
    const data = await request('cloudsearch', { keywords: query, type: 1, limit: 8 });
    return { items: (data.result?.songs || []).map(catalog) };
  }
  if (action === 'manage_song') {
    const data = await request('song_detail', { ids: id(args.target_id) });
    if (!data.songs?.length) fail('曲库候选已失效，请重新检查。');
    return catalog(data.songs[0]);
  }
  const songId = id(args.id);
  const current = await ownRow(request, songId);
  if (action === 'manage_detail') {
    if (!current) fail('该歌曲已不在当前账号的云盘中，请刷新列表。');
    return current;
  }
  if (args.confirmed !== true || !args.fingerprint) fail('请先检查并确认具体操作。');
  if (!current || current.fingerprint !== args.fingerprint) fail('歌曲信息已变化，请重新检查并确认，尚未执行修改。');
  if (action === 'manage_delete') {
    await request('user_cloud_del', { id: songId });
    const after = await ownRow(request, songId);
    return { state: after ? 'unconfirmed' : 'verified', message: after ? '接口已接受删除，但列表仍存在，请稍后刷新核对。' : '已从网易云音乐云盘删除；本地文件保留。' };
  }
  if (action === 'manage_match') {
    const targetId = id(args.target_id);
    await request('cloud_match', { uid: getUser().id, sid: songId, asid: targetId });
    const rows = await allRows(request);
    const row = rows.find(x => (x.fileName || '') === current.filename && Number(x.fileSize || 0) === current.size) || rows.find(x => String(x.songId) === songId);
    const after = row ? cloudRow(row) : null;
    const verified = after?.catalog?.id === targetId;
    return { state: verified ? 'verified' : 'unconfirmed', after,
      message: verified ? '已核对曲库关联修正。平台可能保留原文件标签，请查看关联信息。' : '接口已接受关联修正，当前列表尚未确认新关联，请稍后刷新；未改写本地文件。' };
  }
  fail('不支持的云盘管理操作。');
}
module.exports = { handle, cloudRow };
