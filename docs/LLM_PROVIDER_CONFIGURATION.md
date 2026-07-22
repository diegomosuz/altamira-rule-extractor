# Proveedores LLM

## Interfaz

```text
POST {base_url}/chat/completions
Authorization: Bearer <api_key>
Content-Type: application/json
```

Payload:

```json
{
  "model": "<model>",
  "messages": [],
  "temperature": 0
}
```

## OpenAI personal

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=replace-me
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=replace-with-approved-model
```

## Gateway PwC

```dotenv
LLM_PROVIDER=pwc_gateway
PWC_GENAI_API_KEY=replace-me
PWC_GENAI_BASE_URL=https://replace-with-gateway-base
PWC_GENAI_MODEL=openai.gpt-4o-2024-11-20
```

## Implementación

Una única clase `OpenAICompatibleChatClient`.

Configuración por perfil:

- base_url;
- api_key;
- model;
- headers adicionales opcionales;
- timeout;
- retry.

No hardcodear claves. No imprimir headers. No usar LiteLLM en V1.

Si el gateway no soporta parámetros opcionales, no enviarlos.

El contenido fuente se delimita como datos y nunca puede modificar las instrucciones del system prompt.
