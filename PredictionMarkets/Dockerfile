FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN addgroup --system bot && adduser --system --ingroup bot bot

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY --chown=bot:bot private_key.pem /app/private_key.pem
COPY --chown=bot:bot public_key.pem /app/public_key.pem
COPY --chown=bot:bot . .
RUN mkdir -p /app/state && chown bot:bot /app/state

USER bot

# The wrapper restarts a failed child process. Docker Compose's
# `restart: unless-stopped` provides the outer container/host restart policy.
ENTRYPOINT ["python", "15m_bot_runner.py"]
CMD ["--continuous",  "--live"]
