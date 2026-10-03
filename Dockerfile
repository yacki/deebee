FROM --platform=$BUILDPLATFORM node:24-slim AS web-builder
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

ARG TARGETARCH
ARG KUBECTL_VERSION=v1.34.11
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
    && curl -fsSLo /tmp/kubectl.sha256 "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl.sha256" \
    && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum --check \
    && chmod 0755 /usr/local/bin/kubectl \
    && rm -f /tmp/kubectl.sha256 \
    && rm -rf /var/lib/apt/lists/*

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
