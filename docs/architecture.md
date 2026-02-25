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

