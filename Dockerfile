# SOFE Community Server — Docker Image
# Run: docker build -t sofe-community . && docker run -p 8080:8080 sofe-community
#
# Requires AWS credentials for scanning:
#   docker run -p 8080:8080 \
#     -e AWS_ACCESS_KEY_ID=xxx \
#     -e AWS_SECRET_ACCESS_KEY=yyy \
#     -e AWS_DEFAULT_REGION=us-east-1 \
#     sofe-community
#
# Or mount your credentials:
#   docker run -p 8080:8080 -v ~/.aws:/root/.aws:ro sofe-community

FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml README.md ./
COPY sofe/ ./sofe/
COPY policies/ ./policies/
COPY community_server.py ./

RUN pip install --no-cache-dir -e . fastapi uvicorn

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Run
CMD ["uvicorn", "community_server:app", "--host", "0.0.0.0", "--port", "8080"]
