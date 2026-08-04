# 模拟纯净用户环境：模拟"从 GitHub 克隆 → 安装依赖 → 配置 → 运行"
# 构建: docker build -f docker/Dockerfile -t badminton-clean-test .
# 运行: docker run --rm badminton-clean-test
FROM python:3.12-slim

WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# 先装依赖（验证 requirements.txt 完整性，模拟用户 setup 第一步）
COPY requirements.txt .
RUN pip install -r requirements.txt && pip install pytest

# 拷贝项目源码（.dockerignore 已剔除敏感数据/开发环境，等价于 GitHub 克隆内容）
COPY . .

# 运行纯净环境测试脚本
CMD ["bash", "docker/run_clean_env_test.sh"]
