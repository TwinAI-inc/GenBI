# Azure AI Foundry Integration (Managed Identity)

GenBI uses Azure OpenAI for all AI-powered features. Authentication is via
Managed Identity — no API keys are stored or transmitted.

## Architecture

```
Browser  -->  POST /api/ai/*  -->  Flask (server.py)
                                     |
                                     |  auth: JWT Bearer token
                                     |  quota: entitlement_service.can_consume()
                                     |
                                     v
                              services/azure_ai_client.py
                                     |
                                     |  auth: DefaultAzureCredential
                                     |        (Managed Identity in Azure,
                                     |         az login locally)
                                     v
                              Azure OpenAI Service
```

## Azure Setup Steps

### A) Create an Azure OpenAI Resource

1. Go to [Azure Portal](https://portal.azure.com) > Create a resource > Azure OpenAI
2. Choose a region and pricing tier
3. Deploy a model (e.g., `gpt-4o`) — note the **deployment name**

### B) Enable Managed Identity on App Service

1. Azure Portal > App Service (`genbi-app`) > Identity
2. System assigned > Status: **On** > Save
3. Copy the **Object (principal) ID** for the next step

### C) Grant App Service Access to Azure OpenAI

1. Azure Portal > your Azure OpenAI resource > Access control (IAM)
2. Add role assignment:
   - **Role**: `Cognitive Services OpenAI User`
   - **Assign access to**: Managed identity > App Service
   - **Select**: `genbi-app`
3. Save

### D) Set App Service Environment Variables

Azure Portal > App Service > Configuration > Application settings:

| Variable | Example Value |
|----------|---------------|
| `AZURE_OPENAI_ENDPOINT` | `https://your-resource.openai.azure.com/` |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-4o` |
| `AZURE_OPENAI_API_VERSION` | `2024-12-01-preview` |

No secrets needed — Managed Identity handles authentication.

### E) Local Development

1. Install Azure CLI: `brew install azure-cli`
2. Login: `az login`
3. Set env vars in `.env`:
   ```
   AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
   AZURE_OPENAI_DEPLOYMENT=gpt-4o
   AZURE_OPENAI_API_VERSION=2024-12-01-preview
   ```
4. `DefaultAzureCredential` will automatically use your `az login` session

## Verification Checklist

- [ ] `POST /api/chart-assist` returns 401 without Bearer token
- [ ] With valid Bearer token, AI endpoints return results
- [ ] `ai_queries` usage increments in DB after each call
- [ ] Rate limit (10/min) triggers on rapid calls (HTTP 429)
- [ ] Quota limit triggers at plan boundary (HTTP 402)
- [ ] Application logs contain NO prompt or response content
- [ ] Logs contain only correlation IDs and error categories

## Security Notes

- User API keys are never accepted or stored
- Prompts and responses are never logged
- Errors return only correlation IDs (no stack traces to client)
- Rate limiting: 10 requests/minute per endpoint per IP
- Quota: enforced via PL/pgSQL `can_consume()` function (atomic)
