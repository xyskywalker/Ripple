FROM python:3.13-alpine

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY ripple /app/ripple
COPY skills /app/skills
COPY deploy/docker/docker-entrypoint.sh /usr/local/bin/ripple-entrypoint

RUN chmod +x /usr/local/bin/ripple-entrypoint \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

EXPOSE 8080

ENTRYPOINT ["ripple-entrypoint"]
CMD ["uvicorn", "ripple.service:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
