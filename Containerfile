# Gatehouse -- Local AI Code Review CLI
# Built on Hummingbird Python image (Red Hat UBI-based)
#
# Requires git at runtime (calls git diff / git ls-files), so uses the
# builder variant which includes shell and dnf.
#
# Build:
#   podman build -t quay.io/fatherlinux/gatehouse .
#
# Run (bind-mount your repo):
#   podman run --rm -e OPENROUTER_API_KEY -v $(pwd):/repo:ro,Z -w /repo \
#     quay.io/fatherlinux/gatehouse --base main
#
# Run with specific flags:
#   podman run --rm -e OPENROUTER_API_KEY -v $(pwd):/repo:ro,Z -w /repo \
#     quay.io/fatherlinux/gatehouse --staged --agents bugs,security

FROM quay.io/hummingbird/python:3.14-fips-builder AS lock
USER 0
RUN pip install --no-cache-dir uv==0.13.7
WORKDIR /lock
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project \
      --format requirements-txt -o /requirements.txt

FROM quay.io/hummingbird/python:3.14-fips-builder
USER 0

RUN dnf install -y git-core && dnf clean all

WORKDIR /app
RUN python3 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"
COPY --from=lock /requirements.txt /tmp/requirements.txt
# pip 26.2 vendors certifi 2026.6.17 (GHSA-6q48-6c7w-9gw2); 26.2.2 is fixed
RUN pip install --no-cache-dir --upgrade "pip>=26.2.2" && \
    pip install --no-cache-dir --no-deps -r /tmp/requirements.txt
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .

LABEL name="gatehouse" \
      version="0.25.2" \
      summary="Local AI code review CLI using concurrent LLM agents" \
      description="5-agent concurrent code review with anti-noise prompting" \
      maintainer="crunchtools.com" \
      url="https://github.com/crunchtools/gatehouse" \
      org.opencontainers.image.source="https://github.com/crunchtools/gatehouse" \
      org.opencontainers.image.description="Local AI code review CLI" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

WORKDIR /repo
ENTRYPOINT ["gatehouse"]
