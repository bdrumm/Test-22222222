# Continuous collector + live API + site, in one small image.
#   docker build -t mta-insights .
#   docker run -p 8000:8000 -v mta-data:/data mta-insights            # serve (collects every 30 s, refits models)
#   docker run -v mta-data:/data mta-insights collect --minutes 0     # collector only
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY mta_delay_insights ./mta_delay_insights
COPY pipeline ./pipeline
COPY site ./site
COPY deploy/entrypoint.sh /entrypoint.sh
RUN pip install --no-cache-dir . && chmod +x /entrypoint.sh
ENV MTA_DATA_DIR=/data MTA_TARGETS=/app/pipeline/targets.json PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve"]
