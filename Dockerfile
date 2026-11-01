FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && useradd --create-home --uid 10001 lims
COPY --chown=lims:lims . ./
RUN mkdir -p /app/var /app/media /app/staticfiles \
    && chown -R lims:lims /app/var /app/media /app/staticfiles \
    && chmod +x /app/deploy/entrypoint.sh
USER lims
EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--worker-class", "gthread", "--threads", "4", "--timeout", "240", "--access-logfile", "-", "--error-logfile", "-"]
