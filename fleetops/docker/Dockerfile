# FleetOps services image (linux/arm64, GB10). One image, six services; compose.yaml picks the command.
# Built with three named contexts (see compose.yaml): fleetops, supervisor, navfix.
# No secrets in the image: the Discord token and shared keys come from env files at runtime.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir "discord.py>=2.4,<3" "aiohttp>=3.9" "python-dotenv>=1.0" tzdata

# fleet sim, sim bridge, glue (pure standard library)
COPY --from=fleetops sim  /app/fleetops/sim
COPY --from=fleetops glue /app/fleetops/glue

# supervisor tool service + its OpenClaw skill (state lives in the mounted data/ volume)
COPY --from=supervisor tools_service.py bandit.py tools.json README.md /app/supervisor/
COPY --from=supervisor openclaw /app/supervisor/openclaw

# dashboard (Megha / Amal) and navbot (Megha): code only, never .env / .venv / data
COPY --from=navfix dashboard /app/navfix/dashboard
COPY --from=navfix navbot/approvals.py navbot/bot.py navbot/config.py navbot/fleetlog.py navbot/formatter.py \
     navbot/llm.py navbot/mock_supervisor.py navbot/store.py navbot/telemetry.py navbot/requirements.txt \
     navbot/README.md navbot/setup.md /app/navfix/navbot/
COPY --from=navfix navbot/samples /app/navfix/navbot/samples
COPY --from=fleetops docker/config.docker.json /app/navfix/dashboard/config.docker.json

WORKDIR /app
LABEL org.opencontainers.image.title="FleetOps" \
      org.opencontainers.image.description="Warehouse fleet sim, supervisor tools, glue, dashboard and Discord bot for the GB10"
