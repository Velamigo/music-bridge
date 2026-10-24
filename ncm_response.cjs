// login_status wraps its payload in body.data; other SDK methods use body.code.
function checkedBody(name, response) {
  const body = response.body || {};
  const code = body.code ?? (name === 'login_status' ? body.data?.code : undefined);
  if (code !== 200) {
    throw Object.assign(new Error('NetEase request failed'), { code: code || response.status, stage: name, kind: name === 'cloud_match' && code === 400 && (body.message || body.msg) === '当前歌曲不支持匹配' ? 'unsupported' : '' });
  }
  return body;
}
function profile(body) {
  const data = body.data || body;
  const id = data.profile?.userId || data.account?.id;
  return id ? { id: String(id), name: data.profile?.nickname || '网易云用户' } : null;
}
module.exports = { checkedBody, profile };
