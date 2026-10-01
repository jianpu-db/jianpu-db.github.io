# syntax=docker/dockerfile:1
#
# 简谱旋律查歌 —— **写后端**镜像（FastAPI 版）。
#
# ⚠ 只打包"写后端 + 静态文件服务"，**不打包语料**：语料是 8 万+ 文件 / 20+ MB 的独立 git 仓库，
#   应该**挂卷**进容器（`docker-compose.yml` 里的 `/corpus`），这样：
#     * 镜像小、构建快；
#     * 写进去的东西（投稿生成曲谱 + `git commit`）落在宿主机真实仓库里，不会随容器消失。
#
# 构建（实测见 CI 的 docker job；本机这台没装 Docker，所以本地只做了"步骤等价性"验证）:
#     docker build -t jianpu-write:local .
#     docker run --rm -p 8770:8770 -e JPSUBMIT_TOKEN=xxx -v /path/to/jianpu-db:/corpus jianpu-write:local
#
# 为什么用 uv 而不是 pip: `uv.lock` 把 30 个包钉死，构建可复现；`--frozen` 保证 CI 里
# 不会偷偷解析出新版本（"在我机器上能跑"最常见的来源）。

FROM python:3.13-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# uv 官方镜像里直接拷二进制（比 `pip install uv` 快且不依赖网络上的 PyPI 索引）
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /uvx /bin/

# ── 依赖层：只依赖 pyproject/uv.lock，改代码不会让这一层失效 ────────────────────
FROM base AS deps
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ── 运行层 ────────────────────────────────────────────────────────────────────
FROM base AS runtime
WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    JIANPU_HOST=0.0.0.0
# 写后端本体 + 它要服务的静态文件（静态文件在容器里是"读"的，语料是挂进来的）
COPY app/ ./app/
COPY static/ ./static/
COPY data/ ./data/
COPY index.html 404.html ./
COPY run-api.sh ./
# 非 root 跑（容器里能被写的地方只有挂进来的 /corpus）
RUN useradd --create-home --uid 10001 jianpu \
 && mkdir -p /corpus \
 && chown -R jianpu:jianpu /app /corpus
USER jianpu

EXPOSE 8770
# 健康检查直接用应用自己的 /api/health（不引 curl，镜像更小）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8770/api/health', timeout=4).status == 200 else 1)"

# 语料目录（compose 里挂载）与默认入口
ENV JIANPU_DB=/corpus
CMD ["python", "app/api.py", "8770"]
