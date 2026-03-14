#!/usr/bin/env python3
"""
Merge current Azure Container App env vars with overrides from local .env.
Preserves vars with secretRef using secretref:name format for az CLI.
Outputs KEY=value or KEY=secretref:name per line.
"""
import json
import sys

def main():
    if len(sys.argv) < 2:
        sys.exit(1)
    overrides_path = sys.argv[1]

    overrides = {}
    with open(overrides_path, "r") as f:
        for line in f:
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                overrides[k] = v

    current = json.load(sys.stdin)
    merged = {}
    for item in current:
        n = item.get("name")
        if not n:
            continue
        if "value" in item:
            merged[n] = item["value"]
        elif "secretRef" in item:
            merged[n] = f"secretref:{item['secretRef']}"

    for k, v in overrides.items():
        merged[k] = v

    for k in sorted(merged.keys()):
        v = merged[k]
        # Output KEY=value without extra quotes - az CLI stores the literal string,
        # so wrapping in quotes would store quotes as part of the value and break verification.
        print(f"{k}={v}")

if __name__ == "__main__":
    main()
