# DaiBai GUI Authentication (MSAL)

This document describes how the DaiBai Chat UI integrates with Microsoft Entra ID for user authentication, using the **Identity Plane** (AUTH) configuration.

## Overview

The frontend uses **MSAL.js** (Microsoft Authentication Library for JavaScript) to:

- Sign users in via the configured Identity Plane directory
- Store tokens in `sessionStorage` (cleared when the tab closes)
- Attach `Authorization: Bearer <token>` to every API request (`/api/chat`, `/api/settings`, etc.)

## Configuration Source

The GUI fetches auth configuration from the backend **before** initializing MSAL:

```
GET /api/auth-config
```

This endpoint is **public** (no auth required) and returns:

| Field | Description |
|-------|-------------|
| `auth_tenant_id` | Identity Plane tenant ID (from `AUTH_TENANT_ID`) |
| `auth_client_id` | App Registration client ID (from `AUTH_CLIENT_ID`) |
| `authority` | Full MSAL authority URL |
| `known_authorities` | List of trusted authority hosts for MSAL |

## Authority Formats

The backend computes the authority from environment variables:

### Entra External ID (CIAM) — Default

For customer identity (B2C-style) tenants:

- **Authority:** `https://{AUTH_TENANT_NAME}.ciamlogin.com/{AUTH_TENANT_ID}/`
- **Env vars:** `AUTH_TENANT_ID`, `AUTH_CLIENT_ID`, `AUTH_TENANT_NAME` (default: `daibaiauth`)

### Azure AD / Entra ID (Work & School)

For organizational accounts:

- **Authority:** `https://login.microsoftonline.com/{AUTH_TENANT_ID}`
- **Env vars:** `AUTH_TENANT_ID`, `AUTH_CLIENT_ID`, `AUTH_AUTHORITY_TYPE=azure`

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AUTH_TENANT_ID` | Yes | — | Identity Plane tenant ID |
| `AUTH_CLIENT_ID` | Yes | — | App Registration (SPA) client ID |
| `AUTH_TENANT_NAME` | No | `daibaiauth` | CIAM domain (e.g. `daibaiauth` → `daibaiauth.ciamlogin.com`) |
| `AUTH_AUTHORITY_TYPE` | No | `ciam` | `ciam` or `azure` |

## Login / Logout Flow

1. **Login:** User clicks **Login** → `signIn()` → MSAL redirects to the authority (DaiBai sign-in page).
2. **Redirect:** After sign-in, user returns to the app; `handleRedirectPromise()` completes the flow.
3. **Token:** `getApiToken()` acquires an ID token (or access token) and returns it for API calls.
4. **Logout:** User clicks **Sign Out** → `signOut()` → MSAL clears session and redirects to logout.

## Token Usage

- **API calls:** `apiFetch()` adds `Authorization: Bearer <token>` to every request.
- **WebSocket:** The chat WebSocket connects with `?token=<token>` in the query string.
- **Storage:** Tokens are cached in `sessionStorage` by MSAL.

## Fallback

If `/api/auth-config` is unavailable (e.g. offline, misconfigured backend), the GUI falls back to hardcoded defaults for the DaiBai demo tenant (`daibaiauth`).

## See Also

- [AZURE_GUIDE.md](AZURE_GUIDE.md) — AUTH vs INFRA Plane architecture
- [architecture.md](architecture.md) — Dual-Plane Identity specification
