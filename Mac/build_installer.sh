#!/bin/bash
set -e

# Change directory to script location
cd "$(dirname "$0")"

echo "=== 1. 检查并安装打包依赖 ==="
python3 -m pip install --upgrade pip
python3 -m pip install pyinstaller pyside6

echo "=== 2. 清理历史构建缓存 ==="
rm -rf build dist dmg_folder

echo "=== 3. 执行 PyInstaller 打包 ==="
python3 -m PyInstaller \
  --clean \
  OpenGBD.spec

echo "=== 4. 创建 DMG 拖拽安装布局 ==="
mkdir -p dist/dmg_folder
cp -R dist/OpenGBD.app dist/dmg_folder/
ln -s /Applications dist/dmg_folder/Applications

echo "=== 5. 构建 OpenGBD.dmg 磁盘映像 ==="
rm -f dist/OpenGBD.dmg
hdiutil create -volname "OpenGBD" -srcfolder dist/dmg_folder -ov -format UDZO dist/OpenGBD.dmg

echo "=== 6. 清理临时文件夹 ==="
rm -rf dist/dmg_folder

echo "=== 打包完成！ ==="
ls -lh dist/
