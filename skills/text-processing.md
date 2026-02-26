---
name: text-processing
description: Process text with a local LLM - summarize, extract data, translate, or analyze content. Uses gpt-oss-20b on bluefin.
---

# Text Processing Skill

Process fetched content through a local LLM for summarization, data extraction, translation, or analysis.

## Tool

### process(content: str, task: str = "summarize", prompt: str = None) -> str

Sends content to the text LLM (gpt-oss-20b on bluefin via LM Studio).

**Built-in tasks:**
- `"summarize"` - Concise summary of key points
- `"extract"` - Structured data: names, dates, prices, entities
- `"translate"` - Translate to English, preserve formatting
- `"analyze"` - Sentiment, tone, themes, patterns

**Custom prompts** override the task parameter:
```
process(content, prompt="List all API endpoints mentioned in this text")
process(content, prompt="Rewrite this as bullet points for a presentation")
```

## Infrastructure

| Component | Details |
|-----------|---------|
| Model | `openai/gpt-oss-20b` |
| Host | bluefin (Tailscale: `100.64.10.17`) |
| GPU | NVIDIA 5060ti 16GB |
| Slots | 4 concurrent requests |
| Context | 128k tokens (unified KV cache) |
| Timeout | 120s default |

Content is truncated at ~48,000 chars (~12k tokens) to leave room for the response.

## Common Patterns

### Fetch then summarize
```
content = fetch("https://example.com/long-article")
summary = process(content, task="summarize")
```

### Extract structured data from a page
```
content = fetch("https://example.com/product")
data = process(content, prompt="Extract: product name, price, availability, specs")
```

### Translate foreign content
```
content = fetch("https://example.fr/article")
english = process(content, task="translate")
```

### Chain with knowledge base
```
results = kb_search("machine learning optimization")
analysis = process(results, task="analyze")
```
