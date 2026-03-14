import os
import sys
import asyncio

# Ensure daibai is in the python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from daibai.core.config import load_config

async def main():
    print("Loading DaiBai configuration...")
    config = load_config()
    
    provider_name = "azure_openai"
    if provider_name not in config.llm_providers:
        print(f"❌ '{provider_name}' is not configured in DaiBai settings.")
        sys.exit(1)
        
    provider_cfg = config.llm_providers[provider_name]
    
    if not provider_cfg.endpoint:
        print("❌ Azure OpenAI provider is missing the Endpoint in settings.")
        sys.exit(1)

    auth_mode = "API Key" if provider_cfg.api_key else "Managed Identity"
    print(f"✅ Found Azure Configuration ({auth_mode}):")
    print(f"   Endpoint: {provider_cfg.endpoint}")
    print(f"   Deployment: {provider_cfg.model or 'gpt-4o-mini'}")

    from openai import AsyncAzureOpenAI

    if provider_cfg.api_key:
        client = AsyncAzureOpenAI(
            api_version="2024-12-01-preview",
            azure_endpoint=provider_cfg.endpoint,
            api_key=provider_cfg.api_key,
        )
    else:
        from azure.identity.aio import AsyncDefaultAzureCredential
        from azure.identity import get_bearer_token_provider

        credential = AsyncDefaultAzureCredential()
        token_provider = get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        )
        client = AsyncAzureOpenAI(
            api_version="2024-12-01-preview",
            azure_endpoint=provider_cfg.endpoint,
            azure_ad_token_provider=token_provider,
        )
    
    model = provider_cfg.model or "gpt-4o-mini"
    messages = [{"role": "user", "content": "If you receive this, reply with exactly: 'Tokens are active.'"}]
    max_tokens = 10

    print("\n--- REQUEST ---")
    print(f"  Model: {model}")
    print(f"  Max tokens: {max_tokens}")
    print(f"  Messages:")
    for m in messages:
        print(f"    [{m['role']}] {m['content']}")
    print()

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens
        )
        print("--- RESPONSE ---")
        content = response.choices[0].message.content if response.choices else ""
        print(f"  Content: {content.strip()}")
        if response.usage:
            print(f"  Usage: {response.usage.prompt_tokens} prompt + {response.usage.completion_tokens} completion = {response.usage.total_tokens} total tokens")
        print("\n✅ SUCCESS! Azure AI responded.")
    except Exception as e:
        print("\n❌ ERROR: Failed to reach Azure AI or quota exceeded.")
        print(f"   {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
