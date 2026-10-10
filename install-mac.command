#!/bin/bash
cd "$(dirname "$0")"

echo "═══════════════════════════════════════"
echo "  🍂 秋招手帐 · Mac 一键安装"
echo "═══════════════════════════════════════"
echo ""

if ! command -v python3 &> /dev/null; then
    echo "❌ 未检测到 Python 3"
    echo ""
    echo "请先安装 Python："
    echo "  1. 打开 https://www.python.org/downloads/"
    echo "  2. 下载 macOS 安装包（黄色大按钮）"
    echo "  3. 双击安装，一路点「继续」"
    echo "  4. 装好后重新双击本脚本"
    echo ""
    open "https://www.python.org/downloads/"
    read -p "按回车键退出..."
    exit 1
fi

echo "✅ 检测到 Python：$(python3 --version)"
echo ""

if [ ! -d "venv" ]; then
    echo "📦 正在创建虚拟环境（约 10 秒）..."
    python3 -m venv venv
fi
echo "✅ 虚拟环境就绪"
echo ""

source venv/bin/activate

echo "📥 正在安装依赖（首次约 1-2 分钟，请耐心等待）..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "✅ 依赖安装完成"
echo ""

if [ ! -f ".env" ]; then
    cat > .env << 'EOF'
# 如需启用 AI 功能，在下面填入你的 Key
# 火山引擎 Ark：https://www.volcengine.com/product/ark
# OPENAI_API_KEY=
# OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3
# Tavily 搜索：https://tavily.com/
# TAVILY_API_KEY=
EOF
    echo "✅ 已创建配置文件 .env"
fi
echo ""

cat > "启动秋招手帐.command" << 'EOF'
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
echo ""
echo "🍂 正在启动秋招手帐..."
echo ""
echo "浏览器将自动打开，如未打开请手动访问：http://127.0.0.1:8000"
echo ""
echo "⚠️  关闭看板：在本窗口按 Control + C"
echo ""
(sleep 2 && open http://127.0.0.1:8000/) &
uvicorn main:app --reload
EOF
chmod +x "启动秋招手帐.command"
echo "✅ 已创建启动脚本「启动秋招手帐.command」"
echo ""

echo "═══════════════════════════════════════"
echo "  🎉 安装完成！"
echo "═══════════════════════════════════════"
echo ""
echo "以后使用步骤："
echo "  1. 双击本文件夹里的「启动秋招手帐.command」"
echo "  2. 浏览器会自动打开"
echo "  3. 关闭时在本窗口按 Control + C"
echo ""

read -p "现在启动看板吗？(y/n): " choice
if [ "$choice" = "y" ] || [ "$choice" = "Y" ]; then
    ./"启动秋招手帐.command"
fi