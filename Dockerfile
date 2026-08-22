FROM --platform=$BUILDPLATFORM node:24-slim AS web-builder
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

COPY backend/requirements-runtime.txt ./requirements-runtime.txt
ARG PIP_FIND_LINKS
ARG PIP_NO_INDEX
ARG PIP_TRUSTED_HOST
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --disable-pip-version-check -r requirements-runtime.txt

COPY backend/deebee ./deebee
COPY --from=web-builder /web/dist ./web

ENV DEEBEE_WEB_DIR=/app/web
ENV DEEBEE_BASE_PATH=/deebee
EXPOSE 3000
CMD ["uvicorn", "deebee.main:app", "--host", "0.0.0.0", "--port", "3000"]
