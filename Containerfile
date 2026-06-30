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
#   podman run --rm -e GEMINI_API_KEY -v $(pwd):/repo:ro,Z -w /repo \
#     quay.io/fatherlinux/gatehouse --base main
#
# Run with specific flags:
#   podman run --rm -e GEMINI_API_KEY -v $(pwd):/repo:ro,Z -w /repo \
#     quay.io/fatherlinux/gatehouse --staged --agents bugs,security

FROM quay.io/hummingbird/python:latest-fips-builder
USER 0

RUN dnf install -y git-core && dnf clean all

WORKDIR /app
RUN python3 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

LABEL name="gatehouse" \
      version="0.2.0" \
      summary="Local AI code review CLI using Gemini agents" \
      description="5-agent concurrent code review with anti-noise prompting" \
      maintainer="crunchtools.com" \
      url="https://github.com/crunchtools/gatehouse" \
      org.opencontainers.image.source="https://github.com/crunchtools/gatehouse" \
      org.opencontainers.image.description="Local AI code review CLI" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

WORKDIR /repo
ENTRYPOINT ["gatehouse"]
