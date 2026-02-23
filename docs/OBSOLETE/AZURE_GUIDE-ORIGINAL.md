# DaiBai Azure Deployment Framework

**Document Version:** 1.0  
**Last Updated:** February 2025  
**Purpose:** Minute-detail deployment strategy for DaiBai on Azure with absolute cost containment, user/session continuity, and Stripe integration.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Cost Containment Strategy](#2-cost-containment-strategy)
3. [Architecture Overview](#3-architecture-overview)
4. [User Information and Session State](#4-user-information-and-session-state)
5. [Stripe Integration](#5-stripe-integration)
6. [Deployment Components](#6-deployment-components)
7. [Environment and Configuration](#7-environment-and-configuration)
8. [Security and Compliance](#8-security-and-compliance)
9. [Monitoring and Cost Alerts](#9-monitoring-and-cost-alerts)
10. [Migration Path and Phasing](#10-migration-path-and-phasing)

---

## 1. Executive Summary

This framework describes how to deploy DaiBai to Azure with **absolute cost containment** as the primary constraint. The design assumes:

- **Scale-to-zero** when idle to eliminate baseline compute costs
- **Pay-per-use** storage and compute with no reserved capacity
- **User and session persistence** for continuity across visits
- **Stripe** for subscription management and billing

Target monthly cost for low-to-moderate traffic: **$0–15 USD** (within free tiers) scaling to **$20–50 USD** as usage grows.

---

## 2. Cost Containment Strategy

### 2.1 Cost Hierarchy (Cheapest First)

| Tier | Service | Use Case | Est. Monthly Cost |
|------|---------|----------|-------------------|
| 1 | Container Apps Consumption | Compute (scale-to-zero) | $0 (free tier) to ~$5 |
| 2 | GitHub Container Registry | Image storage | $0 |
| 3 | Azure Table Storage | User/session/conversation data | $0–2 |
| 4 | Azure Key Vault | Secrets (optional) | $0 (first 10k ops free) |
| 5 | Application Insights | Logging (optional) | $0 (first 5GB free) |

**Avoid for cost containment:**

- Azure Container Registry (Basic: ~$5/mo) — use GitHub Container Registry instead
- Cosmos DB provisioned throughput — use Table Storage or Cosmos DB Serverless
- Azure Cache for Redis — use in-memory + Table Storage for session fallback
- App Service (always-on) — use Container Apps scale-to-zero

### 2.2 Container Apps Consumption Plan

**Free tier (per month):**

- 180,000 vCPU-seconds
- 360,000 GiB-seconds (memory)
- 2,000,000 HTTP requests

**Beyond free tier:**

- vCPU: ~$0.000024/second
- Memory: ~$0.000003/GiB-second
- Requests: $0.40 per million

**Scale-to-zero behavior:**

- Set `minReplicas: 0` in Container App configuration
- Cold start: ~2–15 seconds on first request after idle
- No charges when idle

**Resource sizing for DaiBai:**

- 0.25 vCPU, 0.5 GiB memory per replica (sufficient for FastAPI + LLM calls)
- 1 replica = ~0.25 × 3600 vCPU-seconds/hour ≈ 900 vCPU-seconds/hour if always on
- With scale-to-zero: ~0 cost when no traffic

### 2.3 Storage Cost Containment

**Azure Table Storage**

- Storage: ~$0.045/GB/month
- Transactions: ~$0.00036 per 10,000 transactions (write), ~$0.00036 per 10,000 (read)
- For 1,000 users, ~10,000 conversations, ~100 GB: ~$5/mo storage + $1–2/mo transactions

**Alternative: Cosmos DB Serverless**

- $0.25 per million Request Units (RUs)
- ~1 RU per 1 KB read, ~5–10 RUs per write
- For low traffic: often cheaper than provisioned; for high traffic, Table Storage may be cheaper

**Recommendation:** Start with **Azure Table Storage** for user, session, and conversation data. Migrate to Cosmos DB or Redis only if query patterns or latency requirements justify it.

### 2.4 Cost Budget and Alerts

- Set **Azure Budget** at $25/month for the resource group
- Configure **Alert** when cost exceeds 80% of budget
- Use **Cost Management** tags: `project=daibai`, `environment=production`

---

## 3. Architecture Overview

### 3.1 High-Level Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Azure Subscription                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │                    Resource Group: rg-daibai-prod                          │  │
│  │                                                                           │  │
│  │  ┌─────────────────────────────────────────────────────────────────────┐ │  │
│  │  │  Container Apps Environment (Consumption)                            │ │  │
│  │  │  ┌─────────────────────────────────────────────────────────────────┐│ │  │
│  │  │  │  Container App: daibai-app                                       ││ │  │
│  │  │  │  - Image: ghcr.io/org/daibai:latest                              ││ │  │
│  │  │  │  - Port: 8080                                                    ││ │  │
│  │  │  │  - Min replicas: 0 (scale-to-zero)                                ││ │  │
│  │  │  │  - Max replicas: 3                                               ││ │  │
│  │  │  │  - CPU: 0.25, Memory: 0.5 GiB                                    ││ │  │
│  │  │  └─────────────────────────────────────────────────────────────────┘│ │  │
│  │  └─────────────────────────────────────────────────────────────────────┘ │  │
│  │                                                                           │  │
│  │  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────┐  │  │
│  │  │ Storage Account     │  │ Key Vault (optional) │  │ App Insights   │  │  │
│  │  │ - Table: users      │  │ - API keys           │  │ (optional)     │  │  │
│  │  │ - Table: sessions   │  │ - DB passwords       │  │                 │  │  │
│  │  │ - Table: convos     │  │ - Stripe webhook     │  │                 │  │  │
│  │  └─────────────────────┘  └─────────────────────┘  └─────────────────┘  │  │
│  │                                                                           │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
         │                              │                              │
         ▼                              ▼                              ▼
   ┌──────────┐                 ┌──────────────┐               ┌─────────────┐
   │  Users   │                 │ MySQL (user  │               │   Stripe    │
   │ (Browser)│                 │ or external) │               │   API       │
   └──────────┘                 └──────────────┘               └─────────────┘
```

### 3.2 Data Flow

```
User Request → Container App (DaiBai)
                    │
                    ├─→ Table Storage (user, session, conversation lookup)
                    ├─→ MySQL (target DB for SQL execution)
                    ├─→ LLM API (Gemini/OpenAI/etc.)
                    └─→ Stripe API (subscription check, webhook)
```

---

## 4. User Information and Session State

### 4.1 Requirements for Continuity

Users must experience continuity across:

- **Sessions:** Return visits without re-authentication (within TTL)
- **Conversations:** Chat history persists and is restorable
- **Preferences:** Default database, LLM, mode, auto-copy, etc.
- **Subscription:** Access tier tied to Stripe subscription

### 4.2 Data Model

#### 4.2.1 Users Table (Azure Table Storage)

| PartitionKey | RowKey | Properties |
|--------------|--------|-------------|
| `USER` | `{email}` (or `{user_id}`) | `stripe_customer_id`, `stripe_subscription_id`, `subscription_status`, `created_at`, `updated_at`, `email`, `name` (optional) |

**Design notes:**

- PartitionKey `USER` allows simple scans if needed; RowKey = email (normalized, lowercase)
- `subscription_status`: `active`, `canceled`, `past_due`, `trialing`, `none`
- `stripe_customer_id`: created on first checkout; used for subscription lookup

#### 4.2.2 Sessions Table

| PartitionKey | RowKey | Properties |
|--------------|--------|-------------|
| `{user_id}` | `{session_id}` (UUID) | `created_at`, `expires_at`, `user_agent`, `ip` (optional), `last_activity` |

**Design notes:**

- Session TTL: 7 days (configurable)
- `expires_at` used for cleanup; Container App can run a timer-triggered job or use TTL if Cosmos DB
- Table Storage has no native TTL; implement cleanup via scheduled function or manual job

#### 4.2.3 Conversations Table

| PartitionKey | RowKey | Properties |
|--------------|--------|-------------|
| `{user_id}` | `{conversation_id}` (UUID) | `title`, `messages` (JSON), `database`, `llm`, `mode`, `created_at`, `updated_at` |

**Design notes:**

- `messages`: JSON array of `{role, content, sql?, results?, timestamp}`
- Limit message history per conversation (e.g., last 50) to control storage
- `title`: first user message truncated to 50 chars

#### 4.2.4 User Preferences Table (or embedded in Users)

| PartitionKey | RowKey | Properties |
|--------------|--------|-------------|
| `PREFS` | `{user_id}` | `database`, `llm`, `mode`, `auto_copy`, `auto_csv`, `auto_execute`, `updated_at` |

### 4.3 Session Lifecycle

1. **First visit (anonymous):**
   - Create ephemeral session (cookie or localStorage)
   - Allow limited usage (e.g., 5 queries) or require sign-up
   - No persistence to Table Storage until user identifies

2. **Sign-up / Sign-in:**
   - User provides email (and optionally password or OAuth)
   - Create or fetch user record in Users table
   - Create session linked to user_id, set cookie
   - Redirect to app with session

3. **Authenticated request:**
   - Extract session_id from cookie
   - Lookup session in Sessions table → get user_id
   - Validate `expires_at`; refresh if within threshold
   - Load user preferences and conversations for user_id

4. **Session expiry:**
   - On next request: session invalid → redirect to sign-in
   - Option: refresh token or “remember me” for longer TTL

### 4.4 Conversation Persistence Flow

**Current state (in-memory):** `_conversations: Dict[str, List[Dict]]` in `daibai/api/server.py`

**Target state:**

1. **Create conversation:** `POST /api/conversations`
   - Require `user_id` (from session)
   - Generate `conversation_id`
   - Insert row in Conversations table with empty messages
   - Return `{id: conversation_id}`

2. **Add message (REST or WebSocket):**
   - Resolve `user_id` from session
   - Fetch conversation; verify `user_id` matches
   - Append message to `messages` array
   - Update row (optimistic or after response)

3. **List conversations:** `GET /api/conversations`
   - Query Conversations table by `PartitionKey = user_id`
   - Return sorted by `updated_at` desc

4. **Load conversation:** `GET /api/conversations/{id}`
   - Fetch by `user_id` + `conversation_id`
   - Return messages

### 4.5 Storage Abstraction

Introduce a **storage backend interface** so the implementation can be swapped:

```python
# daibai/storage/base.py
class StorageBackend(Protocol):
    def get_user(self, email: str) -> Optional[User]
    def create_user(self, user: User) -> User
    def update_user(self, user: User) -> None
    def get_session(self, session_id: str) -> Optional[Session]
    def create_session(self, session: Session) -> None
    def delete_session(self, session_id: str) -> None
    def get_conversations(self, user_id: str) -> List[ConversationSummary]
    def get_conversation(self, user_id: str, conv_id: str) -> Optional[Conversation]
    def save_conversation(self, conv: Conversation) -> None
    def delete_conversation(self, user_id: str, conv_id: str) -> None
```

Implementations:

- `TableStorageBackend` — Azure Table Storage (production)
- `InMemoryBackend` — dict-based (development, testing)
- `CosmosDBBackend` — if migrating later

---

## 5. Stripe Integration

### 5.1 Subscription Model

**Tiers (example):**

| Tier | Price | Limits |
|------|-------|--------|
| Free | $0 | 10 queries/day, 3 conversations |
| Pro | $19/mo | Unlimited queries, unlimited conversations |
| Team | $49/mo | Pro + shared databases, team features |

### 5.2 Stripe Setup

1. **Products and Prices**
   - Create Product: “DaiBai Pro”
   - Create Price: $19/month recurring
   - Create Product: “DaiBai Team”
   - Create Price: $49/month recurring

2. **Checkout Session**
   - `POST /api/checkout/create` — create Stripe Checkout Session
   - `success_url`, `cancel_url` point to DaiBai frontend
   - `customer_email` from authenticated user
   - `metadata`: `{user_id: "..."}` for webhook

3. **Customer Portal**
   - `POST /api/billing/portal` — create Stripe Customer Portal session
   - User can manage subscription, payment method, cancel

### 5.3 Webhook Endpoint

**Route:** `POST /api/webhooks/stripe`

**Events to handle:**

| Event | Action |
|-------|--------|
| `checkout.session.completed` | Create/update user, set `stripe_customer_id`, `subscription_status` |
| `customer.subscription.created` | Set `subscription_status = active` |
| `customer.subscription.updated` | Update `subscription_status` |
| `customer.subscription.deleted` | Set `subscription_status = canceled` |
| `invoice.payment_failed` | Optionally notify user, retry logic |

**Implementation notes:**

- Use raw request body for signature verification (do not parse as JSON first)
- Verify `Stripe-Signature` header with webhook secret
- Return 200 quickly; process asynchronously if needed
- Idempotency: use `event.id` to avoid duplicate processing

**FastAPI pattern:**

```python
@app.post("/api/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature"),
):
    body = await request.body()
    event = stripe.Webhook.construct_event(
        body, stripe_signature, settings.STRIPE_WEBHOOK_SECRET
    )
    # Dispatch to handler based on event.type
```

### 5.4 Access Control

Before serving expensive operations (e.g., LLM call, conversation create):

1. Resolve `user_id` from session
2. Fetch user from storage
3. Check `subscription_status` and tier
4. If Free tier: check daily query count (store in User or separate table)
5. If over limit: return 402 Payment Required or redirect to upgrade

### 5.5 Environment Variables

- `STRIPE_SECRET_KEY` — server-side API key
- `STRIPE_PUBLISHABLE_KEY` — client-side (for Checkout redirect)
- `STRIPE_WEBHOOK_SECRET` — from Stripe Dashboard webhook config
- `STRIPE_PRICE_PRO_MONTHLY` — price ID for Pro tier
- `STRIPE_PRICE_TEAM_MONTHLY` — price ID for Team tier

---

## 6. Deployment Components

### 6.1 Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir .[gui,gemini]  # Adjust extras as needed

COPY . .
RUN pip install --no-cache-dir -e .

EXPOSE 8080

ENV PORT=8080
CMD ["uvicorn", "daibai.api.server:app", "--host", "0.0.0.0", "--port", "8080"]
```

### 6.2 Container App Configuration (Bicep/ARM)

**Key settings:**

- `ingress`: external, HTTPS, target port 8080
- `minReplicas`: 0
- `maxReplicas`: 3
- `cpu`: 0.25
- `memory`: 0.5Gi
- `env`: all secrets and config from Key Vault or App Settings

### 6.3 CI/CD (GitHub Actions)

1. On push to `main`: build image, push to `ghcr.io`
2. Update Container App with new image
3. Use Azure CLI or Bicep deployment task

### 6.4 Config in Containers

- `DAIBAI_CONFIG_PATH`: override path to `daibai.yaml`
- `DAIBAI_CONFIG`: inline YAML string (for App Settings)
- All `${VAR}` in YAML resolved from environment
- Mount `daibai.yaml` via Azure Files if preferred over env

---

## 7. Environment and Configuration

### 7.1 Required Environment Variables

| Variable | Source | Description |
|----------|--------|-------------|
| `GEMINI_API_KEY` | Key Vault / App Settings | LLM provider |
| `DB_PROD_HOST`, `DB_PROD_USER`, `DB_PROD_PASSWORD` | Key Vault | MySQL connection |
| `AZURE_STORAGE_CONNECTION_STRING` | Key Vault | Table Storage |
| `STRIPE_SECRET_KEY` | Key Vault | Stripe API |
| `STRIPE_WEBHOOK_SECRET` | Key Vault | Webhook verification |
| `SESSION_SECRET` | Key Vault | Signing session cookies |
| `DAIBAI_CONFIG` or `DAIBAI_CONFIG_PATH` | App Settings | Config override |

### 7.2 Key Vault References

Container Apps support Key Vault references in env:

```yaml
- name: GEMINI_API_KEY
  secretRef: gemini-api-key
```

Where `gemini-api-key` is a Key Vault secret reference.

---

## 8. Security and Compliance

- **HTTPS only:** Enforced by Container Apps ingress
- **Secrets:** Never in code; use Key Vault
- **Session:** HttpOnly, Secure, SameSite cookies
- **CORS:** Restrict to DaiBai frontend origin
- **Rate limiting:** Consider Azure Front Door or app-level for abuse prevention

---

## 9. Monitoring and Cost Alerts

- **Application Insights:** Optional; first 5GB free
- **Container Apps metrics:** Requests, CPU, memory, replica count
- **Cost alerts:** Budget alert at $25, $50 thresholds
- **Stripe Dashboard:** Monitor failed payments, churn

---

## 10. Migration Path and Phasing

### Phase 1: Minimal Azure (Cost Containment Only)

- Container Apps + Dockerfile
- Config via environment
- In-memory conversations (no persistence)
- No auth, no Stripe
- **Cost:** ~$0–5/mo

### Phase 2: User and Session State

- Add Azure Table Storage
- Implement `TableStorageBackend`
- User table, Sessions table, Conversations table
- Simple email-based auth (magic link or password)
- **Cost:** ~$0–10/mo

### Phase 3: Stripe Integration

- Stripe products, prices, Checkout
- Webhook handler
- Subscription checks in API
- **Cost:** ~$5–15/mo (Azure) + Stripe fees

### Phase 4: Production Hardening

- Key Vault for all secrets
- Application Insights
- Custom domain, SSL
- **Cost:** ~$15–30/mo

---

## Appendix A: Azure Table Storage Schema (Detailed)

### Users

```
PartitionKey: "USER"
RowKey: "user_abc123"  (or email normalized)
Attributes:
  email: str
  stripe_customer_id: str | null
  stripe_subscription_id: str | null
  subscription_status: str  # active, canceled, past_due, trialing, none
  created_at: datetime (ISO)
  updated_at: datetime (ISO)
```

### Sessions

```
PartitionKey: "user_abc123"
RowKey: "sess_xyz789"
Attributes:
  created_at: datetime
  expires_at: datetime
  user_agent: str (optional)
  last_activity: datetime
```

### Conversations

```
PartitionKey: "user_abc123"
RowKey: "conv_def456"
Attributes:
  title: str
  messages: str  # JSON array
  database: str
  llm: str
  mode: str
  created_at: datetime
  updated_at: datetime
```

---

## Appendix B: Stripe Webhook Event Payload Examples

Reference: [Stripe Webhooks](https://docs.stripe.com/webhooks)

- `customer.subscription.created`: `data.object` contains `customer`, `status`, `items`
- `customer.subscription.deleted`: `data.object` contains `customer`, `status: "canceled"`
- `checkout.session.completed`: `data.object` contains `customer_email`, `subscription`, `metadata`

---

## Appendix C: Bicep Snippet (Container App)

```bicep
resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'daibai-app'
  location: location
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'daibai'
          image: 'ghcr.io/org/daibai:latest'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'PORT', value: '8080' }
            // Add secrets via secretRef
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
      }
    }
  }
}
```

---

*End of Azure Deployment Framework*
