FROM python:3.12-slim
WORKDIR /app
COPY requirements-runtime.txt .
ARG PIP_FIND_LINKS
ARG PIP_NO_INDEX
ARG PIP_TRUSTED_HOST
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements-runtime.txt
COPY deebee ./deebee
EXPOSE 8000
CMD ["uvicorn", "deebee.main:app", "--host", "0.0.0.0", "--port", "8000"]
