# Data Classification

All data in superteaim is classified into two zones. This is enforced at routing, filesystem, and network levels.

## The Two Zones

| Zone | What belongs here | Where it lives | Processed by | Can leave network? |
|------|------------------|---------------|-------------|-------------------|
| **VAULT** | Client info, contracts, invoices, personal data, credentials, financial records, strategy docs | Encrypted local storage | Local models ONLY | Never |
| **LIBRARY** | Product specs, public FAQs, templates, market research, web scrapes, general knowledge | Any storage | Any model including cloud APIs | Yes |

**Default rule:** If unsure → treat as VAULT.

## Secret Levels

| Level | What | Stored in | Who accesses |
|-------|------|-----------|-------------|
| L0 — Public | Website URLs, public docs | Config files | Any agent |
| L1 — Internal | CRM tokens, internal API keys | Environment variables | Authenticated agents |
| L2 — Sensitive | Email passwords, DB credentials | OpenBao | Named agents only |
| L3 — Financial | Payment keys, bank tokens | OpenBao + approval | Finance agent + human |
| L4 — Critical | Master passwords, encryption keys | Physical/offline storage | Human only, manual |

**Agents never see L3+ secrets in memory.** OpenBao injects them at runtime and they're discarded after use.

## How Agents Respect the Boundary

### Boss Agent Classification

The Boss Agent classifies every request before routing:

```
[VAULT] Analyze this client's Q1 revenue
[LIBRARY] Write a blog post about wine tourism
```

### LiteLLM Routing Policy

Requests tagged VAULT can ONLY go to local models. Requests tagged LIBRARY can go to any configured model.

```yaml
# litellm_config.yaml — routing rules
router_settings:
  policy:
    - tag: "vault"
      allowed_models: ["fast", "reasoning", "coder"]
    - tag: "library"
      allowed_models: ["fast", "reasoning", "coder", "claude", "openai"]
```

### Filesystem Enforcement

- VAULT data: mounted read-only in containers that need it (`/vault:ro`)
- LIBRARY data: mounted read-only (`/library:ro`)
- No agent has write access to data volumes
- Backup directory: write access only from the server, not from agents

### Network Enforcement

- VAULT-classified requests never leave the Docker network
- Cloud API calls only for LIBRARY-tagged requests
- The watchdog monitors for VAULT data in external API calls

## Implementation Checklist

- [ ] Boss Agent system prompt includes classification rules
- [ ] LiteLLM config has tag-based routing
- [ ] Data volumes mounted as read-only
- [ ] Backup script doesn't expose VAULT data
- [ ] Security scan checks for VAULT leaks
- [ ] Human review required for any L3+ secret access
