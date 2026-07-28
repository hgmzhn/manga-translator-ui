# Manga Translator Docker Image
# 支持 CPU 和 GPU 版本

ARG CUDA_VERSION=12.1.0
ARG PYTHON_VERSION=3.12
ARG BUILD_TYPE=cpu

# ===== CPU 版本 =====
FROM python:${PYTHON_VERSION}-slim AS base-cpu

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    git \
    wget \
    curl \
    build-essential \
    g++ \
    libeigen3-dev \
    libdbus-1-3 \
    libegl1 \
    libfontconfig1 \
    libgl1 \
    libglib2.0-0 \
    libopengl0 \
    libsm6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    libxkbcommon0 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ===== GPU 版本 =====
FROM nvidia/cuda:${CUDA_VERSION}-cudnn8-runtime-ubuntu22.04 AS base-gpu

# 设置非交互式安装
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai

# 安装 Python 3.12
RUN apt-get update && apt-get install -y \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y \
    python3.12 \
    python3.12-dev \
    python3.12-venv \
    git \
    wget \
    curl \
    build-essential \
    g++ \
    libeigen3-dev \
    libdbus-1-3 \
    libegl1 \
    libfontconfig1 \
    libgl1 \
    libglib2.0-0 \
    libopengl0 \
    libsm6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    libxkbcommon0 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 设置 Python 3.12 为默认
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1

# 升级 pip
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12

# ===== 选择基础镜像 =====
FROM base-${BUILD_TYPE} AS base

# 设置工作目录
WORKDIR /app

# 复制项目文件
COPY . /app/

# 安装 Python 依赖
ARG BUILD_TYPE=cpu
RUN pip install --no-cache-dir uv && \
    uv sync --locked --no-default-groups --group "$BUILD_TYPE"

ENV PATH="/app/.venv/bin:$PATH"

# 创建必要的目录，并由代码内置模板生成所有运行时配置表和提示词表。
# 这些文件属于用户配置且多被 .gitignore 忽略，不能依赖宿主机已有文件。
RUN mkdir -p /app/result /app/models /app/logs /app/config && \
    python -c "from manga_translator.runtime_files import ensure_runtime_files; print(ensure_runtime_files())"

# 备份默认数据以便在挂载空卷时恢复
RUN cp -r /app/config /app/default_config && \
    cp -r /app/fonts /app/default_fonts && \
    cp -r /app/dict /app/default_dict && \
    cp -r /app/manga_translator/server/data /app/default_server_data

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV MANGA_TRANSLATOR_WEB_SERVER=true
ENV QT_QPA_PLATFORM=offscreen

# 暴露端口
EXPOSE 8000

# 复制并设置 entrypoint
RUN chmod +x /app/packaging/docker-entrypoint.sh
ENTRYPOINT ["/app/packaging/docker-entrypoint.sh"]

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# 启动命令
CMD ["python", "-m", "manga_translator", "web", "--host", "0.0.0.0", "--port", "8000"]
