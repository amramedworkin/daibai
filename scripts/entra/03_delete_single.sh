#!/bin/bash
DAIBAI_TENANT_ID="${DAIBAI_TENANT_ID:-e12adb01-a6b3-47bb-86c0-d662dacb3675}"
CI_MODE=false
[[ "$1" == "--ci" || -n "${CI:-}" ]] && CI_MODE=true

CURRENT_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null)
if [[ "$CURRENT_TENANT" != "$DAIBAI_TENANT_ID" ]]; then
    if $CI_MODE; then
        echo "❌ CI: Wrong tenant ($CURRENT_TENANT). Need: $DAIBAI_TENANT_ID"
        exit 1
    fi
    [[ -t 0 ]] && az login --tenant "$DAIBAI_TENANT_ID" || az login --tenant "$DAIBAI_TENANT_ID" --use-device-code
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
