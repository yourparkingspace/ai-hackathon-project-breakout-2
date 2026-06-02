# Plan: Automated Incident → Slack Stakeholder Notifier

## Context

When an incident is detected by an error/monitoring system (Sentry today; PagerDuty,
Elasticsearch, etc. later), notifying the right people in Slack is currently a manual,
error-prone step. During an incident, an engineer has to stop, figure out who needs to
know (service leads, security, governance, platform engineers, on-call, leadership), and
message them by hand — costing time exactly when speed matters most.

This tool removes that manual step. It receives incident events via webhook, classifies
them, resolves the relevant stakeholders from a versioned config, and posts a structured
alert to the appropriate Slack channel(s) in real time — so responders focus on fixing
the incident while all relevant parties are looped in automatically.

**Goal of the POC:** one source (Sentry) → one Slack channel, with the architecture
deliberately built so adding sources and channels is config/code-isolated, not a rewrite.

**Deliverable location:** the project is built in
`/Users/rene/yps/06/ai-training-1-june-2026/ai-hackathon-project-breakout-2` (currently
empty except `.git`). This document will be saved there as `PLAN.md` during execution.

## Stack & high-level architecture

- **Language/runtime:** Python 3.12 on **AWS Lambda**, fronted by **API Gateway** (HTTP API)
  to receive the Sentry webhook.
- **Infra-as-code:** Terraform (aligns with existing platform tooling) — module for the
  Lambda, API Gateway route, IAM role, and Secrets Manager entry for the Slack token.
- **Slack integration:** `slack_sdk` (`WebClient.chat_postMessage`) with Block Kit messages.
- **Secrets:** Slack bot token + Sentry webhook signing secret stored in AWS Secrets Manager;
  read at cold start, cached.

```
Sentry webhook ──> API Gateway ──> Lambda handler
                                      │
                                      ├─ verify signature
                                      ├─ normalize event (adapter)
                                      ├─ classify severity
                                      ├─ resolve stakeholders + channel (config)
                                      ├─ render Block Kit message
                                      └─ post to Slack (1 channel for POC)
```

## Project layout

```
ai-hackathon-project-breakout-2/
├── PLAN.md                       # this document
├── README.md                     # setup + run instructions
├── pyproject.toml                # deps: slack_sdk, pydantic, pyyaml, boto3, pytest
├── src/incident_notifier/
│   ├── handler.py                # Lambda entrypoint (API Gateway event)
│   ├── config.py                 # loads + validates routing.yaml (pydantic)
│   ├── models.py                 # NormalizedIncident dataclass/pydantic model
│   ├── sources/
│   │   ├── base.py               # SourceAdapter protocol: parse() -> NormalizedIncident
│   │   └── sentry.py             # Sentry payload → NormalizedIncident + sig verification
│   ├── routing.py                # severity classification + stakeholder/channel resolution
│   ├── slack_notifier.py         # Block Kit render + chat_postMessage (multi-channel ready)
│   └── secrets.py                # Secrets Manager fetch + cache
├── config/
│   └── routing.yaml              # stakeholder → channel mapping (versioned)
├── tests/
│   ├── fixtures/sentry_*.json    # sample Sentry webhook payloads
│   ├── test_sentry_adapter.py
│   ├── test_routing.py
│   └── test_slack_notifier.py
└── infra/                        # Terraform (lambda, apigw, iam, secrets)
```

## Key design choices (extensibility from day one)

1. **Source adapter pattern** — `sources/base.py` defines a `SourceAdapter` protocol with
   `verify(request)` and `parse(payload) -> NormalizedIncident`. Sentry is the only
   concrete adapter in the POC; PagerDuty/Elasticsearch become new files implementing the
   same protocol. The handler selects the adapter by route/path (e.g. `/webhook/sentry`),
   so adding a source is a new file + new route, no changes to routing/slack logic.

2. **Normalized incident model** (`models.py`) — every source maps into one shape so all
   downstream logic is source-agnostic:
   ```
   NormalizedIncident:
     source: str            # "sentry"
     incident_id: str
     title: str
     service: str           # derived from project/tags
     severity: str          # critical | high | medium | low
     environment: str
     url: str               # link back to source
     detected_at: datetime
     raw: dict              # original payload for debugging
   ```

3. **Config-driven routing** (`config/routing.yaml`) — the single source of truth for who
   gets notified where. POC ships one real channel; `channels` is a list so multi-channel
   is a config edit, not a code change.
   ```yaml
   defaults:
     channel: "#incidents-poc"        # the one POC channel
   severities:
     critical:
       stakeholders: [service_lead, security, on_call, platform, leadership]
       channels: ["#incidents-poc"]   # extend: add per-team channels here
     high:
       stakeholders: [service_lead, on_call, platform]
       channels: ["#incidents-poc"]
     medium:
       stakeholders: [service_lead, on_call]
       channels: ["#incidents-poc"]
     low:
       stakeholders: [on_call]
       channels: ["#incidents-poc"]
   stakeholders:                       # Slack user-group IDs to @mention
     service_lead:  { slack_group: "S012LEAD" }
     security:      { slack_group: "S012SEC" }
     governance:    { slack_group: "S012GOV" }
     platform:      { slack_group: "S012PLAT" }
     on_call:       { slack_group: "S012ONCALL" }
     leadership:    { slack_group: "S012LEAD2" }
   # optional per-service overrides (future): service -> channel/stakeholder additions
   service_overrides: {}
   ```
   `routing.py` resolves `severity → stakeholder groups → @mention string` and
   `severity → channel list`, with `service_overrides` as the seam for per-service routing
   later. Config is validated by pydantic on load (fail fast on bad config).

4. **Severity classification** — for Sentry, map its `level`/alert metadata to the four
   internal severities in `routing.py`. Keep the mapping in config so it's tunable without
   redeploy.

5. **Idempotency / dedup (lightweight for POC)** — include `incident_id` in the Slack
   message and an in-memory short-TTL cache to avoid duplicate posts on webhook retries.
   Note in README as a known POC limitation; production would use DynamoDB with a TTL.

## Slack message (Block Kit)

A structured message with: severity-colored header, service, environment, title,
"detected at", a button/link to the Sentry issue, and a footer line that @mentions the
resolved stakeholder user-groups. `slack_notifier.post(incident, channels, mentions)`
loops over `channels` (one for POC) calling `chat_postMessage` per channel.

## Implementation steps

1. Scaffold project (`pyproject.toml`, package dirs, README skeleton). Save `PLAN.md`.
2. Define `NormalizedIncident` model and `SourceAdapter` protocol.
3. Implement `sources/sentry.py`: signature verification + payload→`NormalizedIncident`,
   using captured fixture payloads.
4. Implement `config.py` + `config/routing.yaml` with pydantic validation.
5. Implement `routing.py`: severity classification + stakeholder/channel resolution.
6. Implement `slack_notifier.py`: Block Kit rendering + multi-channel post loop.
7. Implement `secrets.py` and wire `handler.py` (route → adapter → normalize → classify →
   resolve → render → post), with structured logging and error handling.
8. Tests for adapter, routing, and notifier (Slack client mocked).
9. Terraform in `infra/` for Lambda + API Gateway + IAM + Secrets Manager.
10. README: Slack app setup (bot token, scopes `chat:write`), Sentry webhook config,
    deploy steps, and how to add a new source/channel.

## Critical files to create

- `src/incident_notifier/handler.py` — orchestration entrypoint.
- `src/incident_notifier/sources/sentry.py` — first source adapter.
- `src/incident_notifier/routing.py` + `config/routing.yaml` — the extensibility seam.
- `src/incident_notifier/slack_notifier.py` — multi-channel-ready delivery.

## Verification

- **Unit tests:** `pytest` — adapter parses fixtures to the right `NormalizedIncident`;
  routing resolves expected stakeholders/channels per severity; notifier builds correct
  Block Kit and calls `chat_postMessage` per channel (mocked Slack client).
- **Local end-to-end:** run the handler locally (e.g. via a small `__main__` or SAM/
  `python -m`) and POST a saved Sentry fixture; assert a message lands in a real test
  Slack channel (`#incidents-poc`) using a sandbox workspace token.
- **Severity matrix check:** POST critical/high/medium/low fixtures and confirm each
  posts to the configured channel(s) with the correct @mention set.
- **Deployed smoke test:** after `terraform apply`, trigger a Sentry test alert (or curl
  the API Gateway URL with a signed fixture) and confirm the Slack post.
- **Extensibility sanity check:** add a second channel to `routing.yaml` and confirm the
  same incident fans out to both with no code change.
