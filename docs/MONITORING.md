# Redis Monitoring Guide

This guide explains how to set up Redis Insight to visually monitor your Azure Cache for Redis instance used by DaiBai's semantic cache.

## Prerequisites

- Azure Cache for Redis provisioned (e.g. via `./scripts/cli.sh redis-create`)
- Connection string in `.env` as `REDIS_URL` or `AZURE_REDIS_CONNECTION_STRING`

## Connection String

DaiBai reads the Redis connection from your environment. The setup script writes `REDIS_URL` to `.env`:

```
REDIS_URL=rediss://:your-access-key@daibai-redis.redis.cache.windows.net:6380
```

You can also use `AZURE_REDIS_CONNECTION_STRING` (same format). Both are supported.

## Redis Insight Setup

1. **Download Redis Insight**  
   Get the desktop app from [https://redis.io/insight/](https://redis.io/insight/).

2. **Add a database connection**  
   In Redis Insight, click "Add Redis Database" and enter the connection details.

3. **Parse your connection string**  
   From `REDIS_URL` or `AZURE_REDIS_CONNECTION_STRING` in `.env`:
   - Format: `rediss://:password@hostname:port`
   - **Host:** The hostname (e.g. `daibai-redis.redis.cache.windows.net`)
   - **Port:** Typically **6380** for Azure Cache for Redis (SSL port)
   - **Password:** The access key (the part between `:` and `@`)

4. **Enable TLS**  
   For Azure Cache for Redis, you **must** enable **"Use TLS"** (or equivalent). Azure requires encrypted connections.

5. **Connect**  
   Save and connect. You should see keys, memory usage, and be able to run commands.

## Quick Reference

| Setting   | Value for Azure                    |
|----------|-------------------------------------|
| Host     | From connection string (e.g. `*.redis.cache.windows.net`) |
| Port     | **6380** (SSL port)                 |
| Password | Access key from connection string   |
| Use TLS  | **Yes** (required)                  |

## CLI Monitoring

Without Redis Insight, you can use the built-in CLI commands:

```bash
./scripts/cli.sh cache-stats    # info stats, info keyspace
./scripts/cli.sh cache-monitor # live command stream (Ctrl+C to stop)
```

These require `redis-cli`. If missing, the CLI will prompt to install (`apt-get install redis-tools` on Ubuntu/Debian, `brew install redis` on macOS).
