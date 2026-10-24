#!/bin/zsh
cd "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
if ! command -v brew >/dev/null; then
  echo '首次安装需要 Homebrew。请先从 https://brew.sh 安装，再双击本文件。'
  read '?按回车退出'
  exit 1
fi
for dependency in python3 node ffmpeg; do
  if ! command -v "$dependency" >/dev/null; then
    case "$dependency" in
      python3) brew install python || exit 1 ;;
      node) brew install node || exit 1 ;;
      ffmpeg) brew install ffmpeg || exit 1 ;;
    esac
  fi
done
if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
  echo '文件夹选择和钥匙串功能需要 Apple 命令行工具。请运行 xcode-select --install 后再启动。'
  read '?按回车退出'
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  echo '首次运行：正在安装工具依赖…'
  python3 -m venv .venv || exit 1
  .venv/bin/python -m pip install -r requirements.lock.txt || exit 1
fi
mkdir -p downloads
if [[ ! -f node_modules/NeteaseCloudMusicApi/package.json ]]; then
  if command -v npm >/dev/null && command -v node >/dev/null; then
    echo '正在安装网易云连接组件…'
    npm ci --ignore-scripts --no-audit --no-fund || exit 1
  else
    echo '未安装 Node.js，网易云连接暂不可用；音乐下载仍可使用。'
  fi
fi
if curl --silent --fail http://127.0.0.1:8766/api/status >/dev/null; then
  open https://velamigo.github.io/music-bridge/
  exit 0
fi
(sleep 1; open https://velamigo.github.io/music-bridge/) &
echo '本机连接码页面：http://127.0.0.1:8766/connect'
echo '保留此窗口即可使用网页。关闭窗口或按 Ctrl+C 可停止服务。'
.venv/bin/python server.py
