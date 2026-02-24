### **Secretless Azure Identity (Phase 4)**

When running in Azure (Container Apps, Functions, VM with Managed Identity), you can avoid storing passwords in `.env`:

| Service | Traditional (secrets) | Secretless (identity) |
|---------|------------------------|------------------------|
| **Cosmos DB** | `COSMOS_KEY` in .env | `DefaultAzureCredential` — no key needed. Set `COSMOS_ENDPOINT` only. Run `./scripts/cli.sh cosmos-role` to grant your identity Data Contributor. |
| **Redis** | `REDIS_URL` or `AZURE_REDIS_CONNECTION_STRING` with password | Set `REDIS_USE_ENTRA_ID=1`, `AZURE_REDIS_HOST=your-cache.redis.cache.windows.net`, `AZURE_REDIS_PORT=6380`. Requires `redis-entraid` (`pip install -e ".[azure]"`) and Entra ID enabled on the cache. |

**Verification:** Run `python scripts/verify_azure_auth.py` to confirm Cosmos DB is accessible without a key. If it lists containers, Azureification is working.

---

### **Key Settings Reference**

The following settings are present in the codebase and control semantic behavior. Set them in `.env`.

| Setting | Technical Name | Range | Default | Description |
|---------|----------------|-------|---------|-------------|
| **Cache Matching Precision** | `CACHE_THRESHOLD` | `0.0` to `1.0` | `0.90` | Precision for semantic cache matching. High values (e.g. `0.95`–`1.0`) require nearly identical wording for a cache hit; low values (`0.80`–`0.88`) allow looser matches. Validated by Pydantic in `daibai/core/config.py`. |
| **Schema Vector Limit** | `SCHEMA_VECTOR_LIMIT` | `1` to `20` | `5` | Max number of table schemas injected into the LLM prompt (semantic pruning). Higher = more context, higher token cost. Clamped in `get_schema_vector_limit()`. |
| **Schema Refresh Interval** | `SCHEMA_REFRESH_INTERVAL` | `60`+ (seconds) | `86400` (24h) | How often (in seconds) the agent re-scans the physical database structure. Prevents re-indexing if the interval has not passed. Minimum 60. |

---

### **Schema Metadata and SQL Grounding**

The agent uses **schema metadata** to ground its SQL generation. Before generating SQL from natural language, DaiBai extracts the database schema (table names, column names, data types) via `SchemaManager` (`daibai/core/schema.py`). This metadata is injected into the LLM prompt so the model sees the actual structure of your database.

**How it works:**
- `SchemaManager.get_schema_metadata()` queries `information_schema.COLUMNS` (MySQL) to fetch table and column metadata.
- The raw rows are transformed into a structured dict: `{table_name: [column_info, ...]}`.
- `get_schema_ddl()` produces a DDL-like text block (table names, column types, keys) suitable for prompt context.
- The agent includes this schema text when calling the LLM, so generated SQL references real tables and columns instead of hallucinating.

**Impact:** Without schema grounding, the LLM may invent table or column names. With it, SQL generation is constrained to your actual schema, reducing errors and improving accuracy.

**Semantic Schema Mapping (Table Pruning):** Instead of sending every table to the LLM, DaiBai uses embeddings to select only relevant tables. `SchemaManager.vectorize_schema()` stores table DDL embeddings in Redis (`schema:<db>:<table>`). `get_relevant_tables(query)` performs a similarity search and returns the top N table DDLs for the user's question. This reduces token cost and improves accuracy by avoiding irrelevant context.

| Setting | Technical Name | Description | Default |
|---------|----------------|-------------|---------|
| **Schema Vector Limit** | `SCHEMA_VECTOR_LIMIT` | Max number of tables to send to the LLM (semantic pruning). Higher = more context, higher cost. Clamped 1–20. | `5` |

---

### **Daibai Master Configuration Manifest**

| Grouping | Analysis Name (UI Label) | Technical Name | Description | Range / Expected Values | Impact (Correct vs. Incorrect) | Source | Presentation Note |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Identity** | **Agent Name** | `DAIBAI_NAME` | The display name used by the AI in chat bubbles and system greetings. | String (Default: "DaiBai") | **Right:** Consistent branding.<br>**Wrong:** Generic "Assistant" or confusing persona. | `.env` | Simple text input in General Settings. |
| **LLM Logic** | **Response Creativity** | `DAIBAI_TEMPERATURE` | Controls the "randomness" of the model. Lower is more predictable; higher is more creative. | `0.0` to `1.0` (float) | **Right:** `0.0` ensures consistent SQL generation.<br>**Wrong:** `0.8+` leads to hallucinated table names and SQL syntax errors. | `.env` | A slider labeled "Precision vs. Creativity." |
| **SQL Engine** | **Retry Persistence** | `SQL_MAX_ATTEMPTS` | Number of times the agent attempts to fix an invalid SQL query before giving up. | `1` to `5` (int) | **Right:** Agent self-heals from minor typos.<br>**Wrong:** `1` makes agent feel brittle; `5+` wastes API credits. | `agent.py` | "Auto-Repair Strength" dropdown (Low, Med, High). |
| **Semantic Cache** | **Matching Precision** | `CACHE_THRESHOLD` | How similar a new question must be to a cached one to reuse the answer. | `0.0` to `1.0` (Suggested: `0.88` - `0.95`) | **Right:** High speed and lower Azure costs.<br>**Wrong:** Too low = wrong answers from old questions; Too high = cache never works. | `.env` | Slider with "Strict" and "Flexible" markers. |
| **Semantic Cache** | **Memory TTL** | `CACHE_TTL` | Time in seconds before a cached answer is considered "stale" and deleted. | `0` to `31536000` (int) | **Right:** Fresh data for users.<br>**Wrong:** Users see outdated numbers if the DB has updated but cache hasn't expired. | `.env` | Number input with "Days/Hours" conversion. |
| **Redis Cloud** | **Vector Dimension** | `REDIS_VECTOR_DIM` | The size of the vector embeddings stored in Redis. (Based on `all-MiniLM-L6-v2`). | Fixed: `384` | **Right:** Matches the embedding model requirement.<br>**Wrong:** Redis search will fail with "Dimension Mismatch" errors. | `cache.py` | Read-only technical metadata. |
| **Redis Cloud** | **Insight Indexing** | `REDIS_INDEX_NAME` | The internal key name for the semantic search index in Azure Redis. | String (Default: "idx:daibai_cache") | **Right:** Unique namespace for cache data.<br>**Wrong:** Collisions if other apps use the same Redis instance. | `.env` | Advanced setting; "Cache Namespace." |
| **Database** | **Safety Protocol** | `READ_ONLY_MODE` | Restricts the agent to `SELECT` queries only. Disables `UPDATE/DELETE`. | `True` / `False` | **Right:** Enterprise-grade security for production.<br>**Wrong:** `False` allows users to "accidentally" drop tables via chat. | `.env` | Red/Green toggle: "Database Read-Only Lock." |
| **Azure Infra** | **Performance Tier** | `REDIS_SKU` | The Azure hardware tier (Basic vs. Standard vs. Premium). | `C0`, `C1`, `P1` | **Right:** Low latency for many concurrent users.<br>**Wrong:** `C0` (Basic) has no SLA and may experience latency spikes. | Azure Portal | "Cloud Hardware" status indicator. |
| **API Server** | **Network Bind** | `HOST` / `PORT` | The internal address and port the DaiBai server listens on. | Port: `1024-65535` | **Right:** Accessible GUI and API.<br>**Wrong:** "Address already in use" error; server fails to start. | `cli.sh` | Read-only "Server URL" in settings. |
| **Model** | **Embedding Engine** | `EMBEDDING_MODEL` | The specific NLP model used to turn text into math for the cache. | `all-MiniLM-L6-v2` | **Right:** High performance, low memory footprint.<br>**Wrong:** Model not found; system cannot "understand" similarities. | `cache.py` | Dropdown (initially fixed). |
| **Schema** | **Table Pruning Limit** | `SCHEMA_VECTOR_LIMIT` | Max tables to inject into the LLM prompt (semantic pruning). | `1`–`20` (int, default `5`) | **Right:** Focused context, lower cost.<br>**Wrong:** Too low = missing tables; too high = token bloat. | `.env` | Number input. |
| **Schema** | **Schema Refresh Interval** | `SCHEMA_REFRESH_INTERVAL` | How often (in seconds) the agent re-scans the physical database structure. | `60`–`86400` (int, default `86400`) | **Right:** Fresh schema when DB changes.<br>**Wrong:** Stale schema if DB updated but not re-scanned. | `.env` | Number input with "Minutes/Hours" conversion. |