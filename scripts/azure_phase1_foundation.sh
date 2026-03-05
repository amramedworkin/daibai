#!/bin/bash
set -e # Exit immediately if a command exits with a non-zero status.

# Variables (Change suffix to ensure global uniqueness)
SUFFIX="daibai123"
RG_NAME="rg-daibai-prod"
LOCATION="eastus"
LOG_WORKSPACE="log-daibai-prod"
COSMOS_ACCOUNT="cosmos-daibai-$SUFFIX"
KV_NAME="kv-daibai-$SUFFIX"
