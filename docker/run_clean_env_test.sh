#!/bin/bash
# 纯净环境测试：模拟新用户从 GitHub 克隆后第一次使用
# 关键测试点：缺失依赖/配置/环境变量时不应崩溃，应给出清晰指引
echo "============================================="
echo " 纯净环境测试（模拟从零用户）"
echo "============================================="
echo "[0] 环境: $(python --version) | cwd: $(pwd)"

echo ""
echo "[1] 依赖完整性检查（requirements.txt）"
python -c "import requests, yaml, rich, plyer; print('  OK: requests/yaml/rich/plyer 全部可导入')" \
  || { echo "  FAIL: 有依赖缺失"; exit 1; }

echo ""
echo "[2] 所有模块可导入（无 Windows 依赖泄漏）"
python - <<'EOF' || { echo "  FAIL: 模块导入失败"; exit 1; }
import book, token_util, capture_token, configure
import token_capture_addon  # addon 顶层不应 import mitmproxy
print("  OK: book/token_util/capture_token/configure/token_capture_addon 导入正常")
EOF

echo ""
echo "[3] 模拟 setup 向导（自动填学号 B21000001）"
echo "B21000001" | python setup.py > /tmp/setup.log 2>&1
tail -2 /tmp/setup.log
python - <<'EOF' || { echo "  FAIL: config 生成异常"; exit 1; }
import yaml
cfg = yaml.safe_load(open('config/config.yaml', encoding='utf-8'))
assert cfg['auth']['student_id'] == 'B21000001'
assert cfg['token_capture']['enabled'] is True
print(f"  OK: config.yaml 已生成，学号={cfg['auth']['student_id']}")
EOF

echo ""
echo "[4] 单元测试"
python -m pytest tests/ -q 2>&1 | tail -2

echo ""
echo "[5] 无 token 时 book.py 应优雅提示（不崩溃）"
python book.py --slots > /tmp/out5.log 2>&1; RC5=$?; tail -4 /tmp/out5.log
echo "  退出码: $RC5（期望 1，且无 Traceback）"

echo ""
echo "[6] 无 mitmproxy 时 capture_token --check 应优雅报告"
python capture_token.py --check > /tmp/out6.log 2>&1; RC6=$?; tail -6 /tmp/out6.log
echo "  退出码: $RC6（期望 1）"

echo ""
echo "[7] 无 mitmproxy 时 capture_token --refresh 应优雅提示（不 traceback）"
timeout 15 python capture_token.py --refresh > /tmp/out7.log 2>&1; RC7=$?; tail -3 /tmp/out7.log
echo "  退出码: $RC7（期望 1）"

echo ""
echo "[8] 注入假 token 后只读查询应优雅失败（5004，不崩溃）"
python - <<'EOF'
import json, base64, time, pathlib
def b64(d): return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b'=').decode()
jwt = f"{b64({'alg':'HS256'})}.{b64({'userInfo': json.dumps({'studentId':'B21000001'}),'exp':int(time.time())+3600})}.sig"
pathlib.Path('data').mkdir(exist_ok=True)
pathlib.Path('data/session_cache.json').write_text(json.dumps({'token': jwt}), encoding='utf-8')
print("  已注入未过期的假 token")
EOF
python book.py --slots > /tmp/out8.log 2>&1; RC8=$?; tail -4 /tmp/out8.log
echo "  退出码: $RC8（应优雅报告，无 Traceback）"

echo ""
echo "[9] 无 token 时 configure.py 应优雅提示（不崩溃）"
python configure.py --slots > /tmp/out9.log 2>&1; RC9=$?; tail -3 /tmp/out9.log
echo "  退出码: $RC9（期望 1）"

echo ""
echo "============================================="
echo " 测试结束"
echo "============================================="
