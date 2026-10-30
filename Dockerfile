# MyFitnessPal MCP Server
# 
# Serves the tools over Streamable HTTP at :8000/mcp (see "Remote server" in
# the README). There is no browser in the container, so supply the MFP session
# via MFP_COOKIES_JSON (generate it with `mfp-mcp export-cookies` on a machine
# where you are logged in) and protect the endpoint with MFP_MCP_AUTH_TOKEN.
#
# Build: docker build -t mfp-mcp .
# Run:   docker run -d -p 8000:8000 -v mfp-data:/data \
#          -e MFP_MCP_AUTH_TOKEN=... -e MFP_COOKIES_JSON='{"...":"..."}' mfp-mcp
#
# For the old stdio behaviour: docker run -i --rm -e MFP_MCP_TRANSPORT=stdio mfp-mcp

FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install the package
RUN pip install --no-cache-dir -e .

# Create non-root user for security; /data holds the refreshed cookies.json
RUN useradd --create-home --shell /bin/bash mcp \
    && mkdir /data && chown mcp:mcp /data
USER mcp

ENV MFP_MCP_TRANSPORT=streamable-http \
    MFP_MCP_HOST=0.0.0.0 \
    MFP_MCP_PORT=8000 \
    MFP_CONFIG_DIR=/data
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/healthz' % os.environ['MFP_MCP_PORT'])" || exit 1

ENTRYPOINT ["python", "-m", "mfp_mcp.server"]
