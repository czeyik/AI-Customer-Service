FROM python:3.11-alpine@sha256:0d55920083f1ce1e38ac292e2772f924b4f8bb4188d336c79bf66963039e6146 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apk upgrade --no-cache libuuid \
    && apk add --no-cache postgresql16-client

COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
    && pip uninstall --yes setuptools wheel \
    && addgroup --system app \
    && adduser --system --ingroup app --home /app app \
    && chown app:app /app

COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app docs/wave-7-knowledge-corpus.md ./docs/wave-7-knowledge-corpus.md
COPY --chown=app:app infra/production ./infra/production
COPY --chown=app:app alembic.ini ./alembic.ini

USER app

EXPOSE 8000

FROM runtime AS test

COPY --chown=app:app . .
RUN PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p no:cacheprovider

FROM runtime AS final

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
