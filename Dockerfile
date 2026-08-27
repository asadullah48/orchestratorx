# Python 3.13, matching the pinned venv. Not 3.14: the LangGraph dependency
# tree was verified against 3.13 and wheel availability on 3.14 is untested.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ORCHESTRATORX_LLM=fake

WORKDIR /app

# Copy metadata first so dependency layers cache independently of source edits.
# README.md is required here because pyproject declares it as the readme and
# the build backend reads it.
COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir ".[api]"

# Run unprivileged.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"

CMD ["uvicorn", "orchestratorx.api:app", "--host", "0.0.0.0", "--port", "8000"]
