# Stage 1: Build the Go binary
FROM golang:1.24 AS builder

WORKDIR /build
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o agent-loop ./cmd/agent-loop

# Stage 2: Runtime image
# Use Ubuntu for broad tool compatibility (git, sqlite3, python3, go).
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    sqlite3 \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Go toolchain — the agent runs `go build` / `go test` on target repos.
COPY --from=builder /usr/local/go /usr/local/go
ENV PATH="/usr/local/go/bin:${PATH}"

WORKDIR /app

# Python KB sidecar dependencies
COPY kb/requirements.txt /app/kb/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/kb/requirements.txt

# Copy built binary
COPY --from=builder /build/agent-loop /app/agent-loop

# Copy supporting files
COPY kb/ /app/kb/
COPY eval/ /app/eval/
COPY migrations/ /app/migrations/
COPY prompts/ /app/prompts/

ENTRYPOINT ["/app/agent-loop"]
CMD ["run"]
