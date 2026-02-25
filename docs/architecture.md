# DaiBai Architecture — Dual-Plane Identity

This document describes the Dual-Plane Identity architecture implemented by DaiBai.

## Overview

- Identity Plane
  - Stores user identities (B2C/CIAM).
  - Tenant example: DaiBai B2C (AUTH_TENANT_ID).
  - Accessed by the application via app-only (robot) credentials for administrative tasks (Graph API).
  - Environment variables: `AUTH_TENANT_ID`, `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET`.

- Infrastructure Plane
  - Hosts backend resources and data (subscriptions, storage, databases).
  - Tenant example: IT Protects (AZURE_TENANT_ID).
  - Backend services must use managed identities or service principals scoped to this tenant.

## Computational Airgap

The "airgap" enforces that user tokens (Identity Plane) are never used to access or operate on infrastructure resources. Backend code should obtain credentials bound to the Infrastructure Plane (e.g., DefaultAzureCredential configured with AZURE_TENANT_ID) and must not accept user identity tokens for infra operations.

## Robot Account Validation

At startup, the application will validate robot/app-only credentials (when configured) by requesting a token for Microsoft Graph. If token acquisition fails the service will fail-fast (so CI and deployment catch misconfiguration early).

## Robot-Mediated Token Validation

The backend performs **Robot-mediated validation** of Identity Plane tokens for all protected API routes:

1. **OpenID configuration:** Fetches `https://login.microsoftonline.com/{AUTH_TENANT_ID}/v2.0/.well-known/openid-configuration` (or `https://{AUTH_TENANT_NAME}.ciamlogin.com/{AUTH_TENANT_ID}/v2.0/.well-known/openid-configuration` for Entra External ID).
2. **JWKS:** Uses the `jwks_uri` from the discovery document to obtain public keys for signature verification.
3. **JWT verification:** Validates signature, expiry, and audience (AUTH_CLIENT_ID when set).
4. **Tenant check:** Rejects tokens where `tid` equals AZURE_TENANT_ID (Infrastructure Plane) or differs from AUTH_TENANT_ID.

Environment variables: `AUTH_TENANT_ID`, `AUTH_CLIENT_ID`, `AUTH_TENANT_NAME` (optional), `AUTH_AUTHORITY_TYPE` (optional: `ciam` or `azure`).

