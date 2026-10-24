// Private JSON-lines worker. Credentials only enter the macOS Keychain over private pipes.
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const crypto = require('node:crypto');
console.log = console.warn = console.error = () => {}; // upstream errors may contain cookies
const runtime = path.join(__dirname, '.state', 'runtime');
fs.mkdirSync(runtime, { recursive: true, mode: 0o700 });
process.env.TMPDIR = runtime;
const api = require('NeteaseCloudMusicApi');
const { checkedBody, profile } = require('./ncm_response.cjs');
const keychain = require('./keychain.cjs');
let cookie = {}, user = null, pending = null, restored=false, remembered=false, persistenceWarning='';
function saveSession(){try{keychain.write(cookie);remembered=true;persistenceWarning=''}catch{remembered=false;persistenceWarning='登录成功，但系统钥匙串未能保存；本次仍可使用，重启后可能需要扫码。'}}
function forgetSession(){keychain.remove();remembered=false;persistenceWarning=''}
async function restoreSession(){if(restored)return;restored=true;try{const saved=keychain.read();if(saved){cookie=saved;remembered=true;await identity()}}catch(error){user=null;cookie={};if(Number(error.code)!==301)persistenceWarning='暂时无法恢复保存的登录，可稍后重试连接或重新扫码。'}}
const session = () => ({ connected: !!user, user, authorization_received: !!cookie.MUSIC_U, ready: true, remembered, persistence_warning:persistenceWarning, package_version: '4.32.0' });
const needLogin = () => { if (!user) throw Object.assign(new Error('请先扫码登录网易云音乐。'), { code: 301 }); };
function parsedCookie(raw) {
  const result = {};
  for (const part of raw.split(';')) {
    const i = part.indexOf('=');
    if (i > 0 && !/^(path|domain|expires|max-age|samesite)$/i.test(part.slice(0, i).trim())) result[part.slice(0, i).trim()] = part.slice(i + 1).trim();
  }
  return result;
}
async function request(name, args = {}) {
  const res = await api[name]({ ...args, cookie });
  return checkedBody(name, res);
}
async function identity() {
  const body = await request('login_status');
  const account = profile(body);
  if (!account) { try{forgetSession()}catch{} cookie = {}; user = null; throw Object.assign(new Error('登录已失效，请重新扫码。'), { code: 301 }); }
  user = account;
}
function cloudRow(row) {
  return require('./ncm_manage.cjs').cloudRow(row);
}
async function verify(args) {
  needLogin();
  if (args.cloud_id && /^\d+$/.test(String(args.cloud_id))) {
    try {
      const data = await request('user_cloud_detail', { id: String(args.cloud_id) });
      const match = (data.data || []).find(x => String(x.songId || x.simpleSong?.id) === String(args.cloud_id) && x.fileName === args.filename && Number(x.fileSize) === args.size);
      if (match) return { found: true, row: cloudRow(match) };
    } catch (error) { if (error.code === 301) throw error; }
  }
  // Reconcile by unique content-hash filename AND byte size; never by song title alone.
  for (let offset = 0; offset < 10000; offset += 100) {
    const data = await request('user_cloud', { limit: 100, offset });
    const rows = data.data || [];
    const match = rows.find(x => x.fileName === args.filename && Number(x.fileSize) === args.size);
    if (match) return { found: true, row: cloudRow(match) };
    if ((!data.hasMore && !data.more) || rows.length === 0) return { found: false };
  }
  return { found: false, limited: true };
}
async function handle(action, args) {
  await restoreSession();
  if (action.startsWith('manage_')) {
    delete require.cache[require.resolve('./ncm_manage.cjs')];
    return require('./ncm_manage.cjs').handle(action, args, { request, identity, getUser: () => user });
  }
  if (action === 'status') return session();
  if (action === 'disconnect') { try{forgetSession()}catch{throw Object.assign(new Error('无法清除钥匙串登录，请在系统钥匙串中删除“音乐收集盒 · 网易云登录”后重试。'),{safeMessage:'无法清除钥匙串中保存的登录，退出未完成，请重试。'})} cookie = {}; user = null; pending = null; return session(); }
  if (action === 'qr_start') {
    const keyRes = await request('login_qr_key');
    const key = keyRes.data?.unikey;
    if (!key) throw new Error('未取得二维码，请稍后重试。');
    pending = { key, expires: Date.now() + 180000 };
    const qr = await request('login_qr_create', { key, qrimg: true });
    return { image: qr.data.qrimg, expires_in: 180, message: '请用网易云音乐 App 扫码，并在手机上确认登录。' };
  }
  if (action === 'qr_poll') {
    if (user || cookie.MUSIC_U) {
      if (!user) await identity();
      pending = null;
      saveSession();
      return { state: 'connected', message: '已登录：' + user.name, ...session() };
    }
    if (!pending || Date.now() > pending.expires) return { state: 'expired', message: '二维码已过期，请重新生成。' };
    const res = await api.login_qr_check({ key: pending.key, cookie });
    const body = res.body || {};
    if (![800, 801, 802, 803].includes(body.code)) throw new Error('二维码状态异常');
    if (body.code === 803 && body.cookie) {
      cookie = parsedCookie(body.cookie);
      await identity();
      pending = null;
      saveSession();
      return { state: 'connected', message: '已登录：' + user.name, ...session() };
    }
    return { state: body.code === 800 ? 'expired' : body.code === 802 ? 'confirm' : 'waiting',
      message: body.code === 800 ? '二维码已过期，请重新生成。' : body.code === 802 ? '已扫码，请在手机上确认登录。' : '等待网易云音乐 App 扫码…' };
  }
  if (action === 'refresh') { needLogin(); await identity(); return session(); }
  if (action === 'list') {
    needLogin();
    const offset = Math.max(0, Math.min(100000, Number(args.offset) || 0));
    const data = await request('user_cloud', { limit: 50, offset });
    return { items: (data.data || []).map(cloudRow), count: data.count, more: !!(data.hasMore || data.more), offset, account_id: user.id };
  }
  if (action === 'verify') return verify(args);
  if (action === 'upload') {
    needLogin();
    if (!/^[A-Za-z0-9_-]{11}\.m4a$/.test(args.file)) throw new Error('无效的本地音频文件。');
    const source = args.path || path.join(__dirname, 'downloads', args.file);
    if (!path.isAbsolute(source) || path.basename(source)!==args.file) throw new Error('无效的本地文件路径。');
    const data = fs.readFileSync(source);
    const digest = crypto.createHash('md5').update(data).digest('hex');
    if (digest !== args.md5) throw new Error('本地文件已变化，请重新核对。');
    const filename = args.file.slice(0, -4) + '_' + digest + '.m4a';
    const result = await request('cloud', { songFile: { name: filename, data, size: data.length, md5: digest, mimetype: 'audio/mp4' } });
    const id = result.privateCloud?.songId || result.songId || result.data?.songId;
    return { accepted: true, cloud_id: id ? String(id) : '', filename, size: data.length };
  }
  throw new Error('不支持的操作。');
}
function safeError(error, action) {
  const code = Number(error?.code || error?.body?.code || error?.status || 0);
  if (code === 301 || code === 302) { try{forgetSession()}catch{} cookie = {}; user = null; return { code: 301, message: '网易云登录失效，请重新扫码。' }; }
  if (code === 429 || code === 405) return { code, message: '网易云暂时限制请求，请稍后重试。' };
  if (error?.safeMessage) return { code, message: error.safeMessage };
  if (action?.startsWith('manage_')) return { code, message: ['manage_delete', 'manage_match'].includes(action)
    ? '云盘修改结果未确认，请刷新列表核对，不要重复提交。'
    : '网易云云盘或曲库查询失败，请稍后重试。尚未提交修改。' };
  if (action === 'qr_start' || action === 'qr_poll') return { code, message: cookie.MUSIC_U
    ? '手机授权已收到，但账号信息查询暂时失败。点击“检查登录”可继续重试，无需刷新二维码。'
    : '登录状态查询失败，请点击“检查登录”重试；二维码过期时再刷新。' };
  // Never forward upstream request/response objects or raw error messages.
  return { code, message: '网易云接口请求失败，请稍后重试；上传失败时请先核对云盘，避免重复提交。' };
}
const lines = readline.createInterface({ input: process.stdin });
let chain = Promise.resolve();
lines.on('line', line => { chain = chain.then(async () => {
  let input;
  try {
    input = JSON.parse(line);
    const data = await handle(input.action, input.args || {});
    process.stdout.write(JSON.stringify({ ok: true, data, session: session() }) + '\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, error: safeError(error, input?.action), session: session() }) + '\n');
  }
}); });
lines.on('close', () => { cookie = {}; user = null; pending = null; process.exit(0); });
