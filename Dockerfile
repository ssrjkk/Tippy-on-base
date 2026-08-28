FROM python:3.12-slim

LABEL org.opencontainers.image.title="Tippy" \
      org.opencontainers.image.description="Community economy in USDC on Base: tips, Polymarket-style on-chain markets, x402" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/ssrjkk/Tippy-on-base"
ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd -r tipbot && useradd -r -g tipbot tipbot

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R tipbot:tipbot /app && chmod +x deploy/entrypoint.sh

USER tipbot

EXPOSE 8000

ENTRYPOINT ["deploy/entrypoint.sh"]
