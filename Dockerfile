FROM python:3.14.7-alpine3.24
WORKDIR /app

RUN apk update && apk add g++ gcc make python3-dev curl
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
  #ENV PATH="/root/.local/bin:$PATH"
