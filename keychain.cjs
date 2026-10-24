// Credentials travel only over private process pipes, never arguments or files.
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const source = path.join(__dirname, 'keychain.m');
const binary = path.join(__dirname, '.state', 'keychain-helper');
function run(action, input) {
  if (process.platform !== 'darwin') throw new Error('keychain unavailable');
  if (!fs.existsSync(binary) || fs.statSync(binary).mtimeMs < fs.statSync(source).mtimeMs) {
    fs.mkdirSync(path.dirname(binary), {recursive:true,mode:0o700});
    const built=spawnSync('/usr/bin/clang',['-fobjc-arc','-framework','Foundation','-framework','Security',source,'-o',binary],{stdio:'ignore',timeout:90000});
    if(built.status!==0) throw new Error('keychain unavailable');
  }
  const result=spawnSync(binary,[action],{input,encoding:'utf8',timeout:30000,maxBuffer:65536});
  if(action==='read' && result.status===3) return null;
  if(result.status!==0) throw new Error('keychain unavailable');
  return result.stdout;
}
module.exports={
 read(){const value=run('read');if(!value)return null;const cookie=JSON.parse(value);if(!cookie||typeof cookie.MUSIC_U!=='string'||!cookie.MUSIC_U)throw new Error('invalid saved session');return cookie},
 write(cookie){run('write',JSON.stringify(cookie))},
 remove(){run('delete')}
};
