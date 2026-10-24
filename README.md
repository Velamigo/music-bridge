# 音乐收集盒 · Music Bridge

**网页 + Mac 本机程序**。网页托管在 GitHub Pages；搜索、下载、标签处理和网易云盘操作都由使用者自己的 Mac 执行，没有云端下载服务器。

网页入口：https://velamigo.github.io/music-bridge/

## 首次使用（Mac）

1. 下载网页提供的 `MusicBridge-Mac.zip` 并解压到一个固定目录。
2. 准备 [Homebrew](https://brew.sh) 和 Apple Command Line Tools（`xcode-select --install`）。启动器会通过 Homebrew 安装缺少的 Python、Node.js、FFmpeg，并安装项目依赖；首次运行需要联网。
3. 双击 **启动音乐收集盒.command**，保留终端窗口。
4. 在网页点击“打开本机连接码”，将本机页面的 8 位连接码填回网页。浏览器如询问本地网络访问权限，请允许。推荐使用 Mac 上的 Chrome 或 Edge。
5. 指定本机下载文件夹；扫码登录你自己的网易云账号。

浏览器不支持从 Pages 连接本机时，可直接打开 http://127.0.0.1:8766/ 使用完整功能。本机连接码有效期 10 分钟，网页访问令牌有效期 24 小时；重启本机服务后需要重新连接网页。网易云 Cookie 单独保存在这台 Mac 的系统钥匙串中，不必因此重新扫码（平台登录失效时除外）。

## 功能

- 选择“找单曲”或“找专辑”：只填歌手可列出对应歌曲/专辑，歌曲旁直接下载，专辑内可逐首或整张下载。版本选择和信息编辑默认折叠；只有无法确定推荐来源时才要求手动选择。单曲列表每页 25 首。
- 只下载来源提供的原生 M4A/AAC 音轨，写标签时复制音频，不重新编码；显示实际格式和码率。
- Apple 发行目录辅助填写、核对歌手、专辑与曲序，用户可手动修改；平台目录不是原唱身份认证。
- 登录使用者自己的网易云账号，上传后核对远端条目；删除必须先预览并明确确认。
- 云盘紧凑列表、小卡片、添加日期、排序、分页及跨歌名/歌手/专辑/文件名搜索，支持简繁体。
- 云盘资料检查只读；发行目录缓存 7 天，同专辑复用曲目表。暂不推荐未经来源验证的网易云曲库改绑。

## 数据与连接

- 本机程序仅监听 `127.0.0.1:8766`，不向局域网开放。
- Pages 只发布程序代码和安装包，不含用户音乐、Cookie、上传记录或本机目录设置。
- 跨域仅允许 `https://velamigo.github.io`，需本机连接码配对；不接受任意网站调用本机文件、上传或删除接口。
- 网易云 Cookie 只通过私有进程管道进入 macOS Keychain，退出登录会删除该凭据。
- 网易云接入使用非官方 `NeteaseCloudMusicApi 4.32.0`，平台接口可能变动。
- Android、iPhone、Windows 尚不支持本机助手；Mac 程序不是云端服务器，也不会自动安装到访问者设备上。

## 从源码运行

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
npm ci --ignore-scripts
mkdir -p downloads
.venv/bin/python server.py
```

需要 Python 3.10+、Node.js、FFmpeg，以及用于编译系统文件夹/钥匙串辅助程序的 Apple Command Line Tools。

## 发布

`docs/` 是 Pages 静态发布目录。GitHub 仓库 Settings → Pages 选择 `main` 分支的 `/docs`。
打包时必须使用源码允许清单，排除 `.state/`、`downloads/`、`.venv/`、`node_modules/` 和个人截图。
