# DaiBai Azure Serverless Hosting Strategy

This document outlines the architecture, costing, and roadmap for deploying DaiBai on Azure using a "super-cheap" serverless model designed to scale from 1 to 10,000+ users.

## 1. Comprehensive Architecture

The architecture utilizes a consumption-based model where you only pay for active execution and data storage.

### Architecture Diagram

```mermaid
graph TD
    subgraph Client_Layer
        User((User))
        GUI[Static Web App / React]
    end

    subgraph Security_Identity
        B2C[Azure AD B2C - Auth]
        Vault[Azure Key Vault - Secrets]
    end

    subgraph Logic_Gateway
        APIM[API Management - Consumption]
        AF[Azure Functions - Backend]
    end

    subgraph Data_Storage
        Cosmos[Cosmos DB Serverless - Profiles/Chat]
        SQL[SQL Serverless - Data Inventory]
    end

    subgraph External_Connectors
        Relay[Azure Relay - Local Data Servers]
        Stripe[Stripe API - Billing]
        LLM[LLM Providers - OpenAI/Anthropic]
    end

    User --> GUI
    GUI --> B2C
    GUI --> APIM
    APIM --> AF
    AF --> Vault
    AF --> Cosmos
    AF --> SQL
    AF --> Relay
    AF --> Stripe
    AF --> LLM
```

*[Mermaid source: [docs/mermaid/azure2-architecture.mmd](mermaid/azure2-architecture.mmd)]*

### Component Breakdown

| Component | Description |
|-----------|-------------|
| **Azure Static Web Apps** | Hosts the frontend with a free tier for hobbyists and a global CDN. |
| **Azure Functions (Python)** | Replaces the FastAPI server.py with serverless logic that scales to zero when idle. |
| **Azure AD B2C** | Provides secure user management (Free for first 50k monthly active users). |
| **Azure Key Vault** | Centralized storage for user-provided LLM keys and system database credentials. |
| **Azure Cosmos DB (Serverless)** | Stores chat history and agent metadata with pay-per-request pricing. |
| **Azure Relay/Hybrid Connections** | Enables the serverless cloud backend to securely query "local" data servers behind firewalls without VPNs. |

## 2. Costing Analysis (Monthly Estimates)

Estimates are based on typical consumption patterns for a serverless environment.

| Users | Compute (Functions) | Data (Cosmos/SQL) | Security (B2C/Vault) | Total Est./Mo |
|-------|----------------------|-------------------|----------------------|---------------|
| 1 | $0.00 (Free Grant) | $0.05 | $0.00 | **~$0.05 - $1.00** |
| 10 | $0.00 (Free Grant) | $0.50 | $0.00 | **~$5.00** |
| 100 | $5.00 | $15.00 | $1.00 | **~$21.00** |
| 1,000 | $45.00 | $120.00 | $10.00 | **~$175.00** |
| 10,000 | $400.00 | $900.00+ | $50.00 | **~$1,350.00+** |

## 3. Automated Migration Roadmap

| Phase | Description |
|-------|-------------|
| **Phase 1: Environment Abstraction** | Modify config.py to support DefaultAzureCredential for seamless local-to-cloud transition. Transition local .env variables to Azure App Configuration. |
| **Phase 2: Infrastructure as Code (IaC)** | Develop Bicep templates to automate the provisioning of the entire resource stack. |
| **Phase 3: CI/CD Pipeline** | Establish GitHub Actions to automate deployments directly to Azure Static Web Apps and Azure Functions. |
| **Phase 4: Billing & Metering** | Implement usage tracking within the Python logic to report "tokens used" or "tasks completed" to the Stripe Gateway. |

## 4. Outstanding Items (Beyond Architecture)

- **Multi-Tenancy Logic:** Ensure the database schema and API endpoints isolate data so users cannot see each other's local server configurations.
- **BYOK (Bring Your Own Key) Support:** Implement a secure UI for users to store their own Anthropic/OpenAI keys in an encrypted per-user vault.
- **Stripe Webhook Integration:** Build handlers for subscription lifecycle events (payment success, cancellation, credit exhaustion).
- **Local Agent Binary:** Create a lightweight "Relay Agent" that users can install locally to bridge their local data servers to the Azure cloud.
- **Telemetry & Audit:** Implement Azure Monitor/Application Insights to track failed queries and agent performance across different LLM providers.
