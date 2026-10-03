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
COPY requirements-runtime.txt .
ARG PIP_FIND_LINKS
ARG PIP_NO_INDEX
ARG PIP_TRUSTED_HOST
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements-runtime.txt
COPY deebee ./deebee
EXPOSE 8000
CMD ["uvicorn", "deebee.main:app", "--host", "0.0.0.0", "--port", "8000"]
