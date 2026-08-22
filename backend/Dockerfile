FROM python:3.12-slim
WORKDIR /app
COPY requirements-runtime.txt .
COPY .docker-wheelhouse ./.docker-wheelhouse
RUN pip install --no-cache-dir --no-index --find-links=.docker-wheelhouse -r requirements-runtime.txt \
    && rm -rf .docker-wheelhouse
COPY deebee ./deebee
EXPOSE 8000
CMD ["uvicorn", "deebee.main:app", "--host", "0.0.0.0", "--port", "8000"]
