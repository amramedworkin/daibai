#!/bin/bash
# --- AZURE CONTEXT ISOLATION ---
ENTRA_TENANT="e12adb01-a6b3-47bb-86c0-d662dacb3675"
ORIGINAL_SUB=$(az account show --query id -o tsv 2>/dev/null)
if [ -n "$ORIGINAL_SUB" ]; then
    trap 'echo -e "\n🔄 Restoring original Azure context..."; az account set --subscription "$ORIGINAL_SUB" > /dev/null 2>&1' EXIT
fi
echo "🔀 Temporarily switching context to Entra Directory..."
az login --tenant "$ENTRA_TENANT" --allow-no-subscriptions > /dev/null 2>&1

CI_MODE=false
[[ "$1" == "--ci" || -n "${CI:-}" ]] && CI_MODE=true
if $CI_MODE; then
    CURRENT_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null)
    if [[ "$CURRENT_TENANT" != "$ENTRA_TENANT" ]]; then
        echo "❌ CI: Wrong tenant ($CURRENT_TENANT). Need: $ENTRA_TENANT"
        exit 1
    fi
fi

SOFT_DELETE=false
if [[ "$1" == "--soft" ]]; then
    SOFT_DELETE=true
    echo "ℹ️  Soft Delete mode enabled. Users will remain in the recycle bin for 30 days."
else
    echo "⚠️  Hard Delete mode (Default). Users will be PERMANENTLY destroyed."
fi

echo ""
echo "🗑️  Fetching users for deletion..."
mapfile -t UPN_LIST < <(az ad user list --query "[].userPrincipalName" -o tsv)

if [ ${#UPN_LIST[@]} -eq 0 ]; then
    echo "No users found."
    exit 0
fi

echo "Select a user to delete:"
select USER_TO_DELETE in "${UPN_LIST[@]}" "Cancel"; do
    if [[ "$USER_TO_DELETE" == "Cancel" ]]; then
        echo "Operation cancelled."
        exit 0
    elif [[ -n "$USER_TO_DELETE" ]]; then
        read -p "Are you sure you want to delete $USER_TO_DELETE? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            OBJ_ID=$(az ad user show --id "$USER_TO_DELETE" --query id -o tsv)
            echo "1/2: Soft deleting $USER_TO_DELETE..."
            az ad user delete --id "$USER_TO_DELETE"
            if [ "$SOFT_DELETE" = false ]; then
                echo "2/2: Purging $USER_TO_DELETE from recycle bin..."
                sleep 5
                az rest --method DELETE --url "https://graph.microsoft.com/v1.0/directory/deletedItems/$OBJ_ID" 2>/dev/null || echo "Purge failed. Item might require manual removal."
                echo "✅ User permanently deleted."
            else
                echo "✅ User soft-deleted."
            fi
        fi
        break
    else
        echo "Invalid selection."
    fi
done
