# Specs: Automated Incident → Slack Stakeholder Notifier

## 1. Format du webhook Elasticsearch

### 1a. Document de log sous-jacent (champs réels)

Les logs proviennent de services Kubernetes avec le format suivant (valeurs toujours dans
des arrays `[value]`) :

| Champ ES                       | Exemple                           | Usage dans NormalizedIncident |
|--------------------------------|-----------------------------------|-------------------------------|
| `@timestamp[0]`                | `"2026-06-02T13:18:33.938Z"`      | `detected_at`                 |
| `functionalName[0]`            | `"security-accounts-service"`     | `service`                     |
| `kubernetes.namespace_name[0]` | `"streetsmart-global-uat"`        | `environment` (voir §2)       |
| `level[0]`                     | `30` (int, format Bunyan)         | `severity` (voir §2)          |
| `msg[0]`                       | `"prometheus - metrics requested"`| `title`                       |
| `_id`                          | `"YTM5Y2Ux..."`                   | `incident_id`                 |
| `_index`                       | `"fd-streetsmart-global-uat-..."` | fallback environment          |

### 1b. Payload webhook ES Alerting (enveloppe)

ES Alerting envoie les hits sous cette forme (le document ci-dessus est dans `hits[0]`) :

```json
{
  "alert_id": "alert-uuid",
  "alert_name": "High error rate — security-accounts-service",
  "hits": [ { "...document de log..." : "..." } ],
  "context": {
    "matched_count": 42,
    "period_start": "2026-06-02T13:10:00Z",
    "period_end":   "2026-06-02T13:18:00Z"
  }
}
```

> **Note POC :** si on utilise ES Watcher plutôt qu'ES Alerting, le payload est
> entièrement configurable dans l'action Watcher — recommandé pour coller exactement
> à ce format et simplifier l'adapter.

---

## 2. Mappings

### Severity — Bunyan level → interne

| `level[0]` (int) | Label Bunyan | Severity interne |
|-----------------|--------------|-----------------|
| 60              | fatal        | `critical`      |
| 50              | error        | `high`          |
| 40              | warn         | `medium`        |
| ≤ 30            | info / debug | `low`           |

### Environment — namespace Kubernetes → interne

| `kubernetes.namespace_name[0]` contient | `environment` |
|-----------------------------------------|---------------|
| `prod`                                  | `production`  |
| `uat`                                   | `uat`         |
| `staging`                               | `staging`     |
| autre / absent                          | `unknown`     |

Extraction : `namespace.split("-")` puis chercher un token connu.

---

## 3. Modèle `NormalizedIncident` (`src/incident_notifier/models.py`)

```python
class NormalizedIncident(BaseModel):
    source: str          # "elasticsearch"
    incident_id: str     # _id du document ES
    title: str           # msg[0] du document de log
    service: str         # functionalName[0]
    severity: Literal["critical", "high", "medium", "low"]
    environment: str     # dérivé de kubernetes.namespace_name[0]
    url: str             # KIBANA_BASE_URL + "/_index/" + _index + "/" + _id
    detected_at: datetime  # @timestamp[0], toujours UTC
    raw: dict            # payload webhook complet
```

---

## 4. Protocol `SourceAdapter` (`src/incident_notifier/sources/base.py`)

```python
class SourceAdapter(Protocol):
    def verify(self, headers: dict, body: bytes) -> None:
        """Lève HTTPException(401) si signature invalide."""

    def parse(self, payload: dict) -> NormalizedIncident:
        """Mappe le payload webhook vers NormalizedIncident."""
```

---

## 5. Adapter Elasticsearch (`src/incident_notifier/sources/elasticsearch.py`)

**`parse(payload)` — logique d'extraction :**

```python
hit = payload["hits"][0]
service     = hit.get("functionalName", hit.get("kubernetes.container_name", ["unknown-service"]))[0]
title       = hit.get("msg", hit.get("message", ["(no message)"]))[0]
level       = hit["level"][0]
ns          = hit.get("kubernetes.namespace_name", ["unknown"])[0]
detected_at = datetime.fromisoformat(hit["@timestamp"][0].replace("Z", "+00:00"))
incident_id = hit["_id"]
index       = hit["_index"]

severity    = _map_bunyan_level(level)
environment = _extract_env(ns)
url         = f"{KIBANA_BASE_URL}/{index}/_doc/{incident_id}"
```

**`verify(headers, body)` :**
- Header attendu : `X-Elasticsearch-Signature`
- Algo : HMAC-SHA256 du body raw avec `ES_WEBHOOK_SECRET`
- Erreur : lève `HTTPException(401)`

**Cas limites :**
- `functionalName` absent → fallback `kubernetes.container_name[0]` → `"unknown-service"`
- `msg` absent → fallback `message[0]` → `"(no message)"`
- `hits` vide ou absent → `400 Bad Request`
- `level` hors table → `low`

---

## 6. Config schema (`config/routing.yaml` + `src/incident_notifier/config.py`)

Validé par pydantic au cold start (fail fast si clé manquante).

```yaml
defaults:
  channel: "#incidents-poc"

severities:
  critical:
    stakeholders: [service_lead, security, on_call, platform, leadership]
    channels: ["#incidents-poc"]
  high:
    stakeholders: [service_lead, on_call, platform]
    channels: ["#incidents-poc"]
  medium:
    stakeholders: [service_lead, on_call]
    channels: ["#incidents-poc"]
  low:
    stakeholders: [on_call]
    channels: ["#incidents-poc"]

stakeholders:             # IDs placeholders — remplacer au déploiement
  service_lead: { slack_group: "S012LEAD" }
  security:     { slack_group: "S012SEC" }
  governance:   { slack_group: "S012GOV" }
  platform:     { slack_group: "S012PLAT" }
  on_call:      { slack_group: "S012ONCALL" }
  leadership:   { slack_group: "S012LEAD2" }

service_overrides: {}
```

---

## 7. Routing (`src/incident_notifier/routing.py`)

```python
@dataclass
class RoutingResult:
    channels: list[str]
    mentions: list[str]   # format Slack : "<!subteam^S012LEAD>"

def classify(incident: NormalizedIncident, config: RoutingConfig) -> RoutingResult: ...
```

- Lit `config.severities[incident.severity]`
- `service_overrides` : union avec le routing de base (pas de remplacement)
- Severity inconnue → `low`

---

## 8. Slack notifier (`src/incident_notifier/slack_notifier.py`)

```python
def post(incident: NormalizedIncident, result: RoutingResult, client: WebClient) -> None: ...
```

**Block Kit :**
- Header : `[CRITICAL] security-accounts-service — production` (couleur par severity)
- Body : `title`, `detected_at`, `environment`
- Bouton : lien Kibana (`incident.url`)
- Footer : `@mentions` des groupes stakeholders

**Dedup in-memory :**
- `dict[incident_id → datetime]`, TTL 10 min
- Doublon → skip + log `"duplicate skipped: {incident_id}"`

---

## 9. Handler Lambda (`src/incident_notifier/handler.py`)

**Route :** `POST /webhook/elasticsearch`

**Pipeline :**
1. Sélection adapter par path
2. `adapter.verify(headers, raw_body)`
3. `adapter.parse(json.loads(raw_body))`
4. `routing.classify(incident, config)`
5. `slack_notifier.post(incident, result, slack_client)`
6. Retour `200 OK` (toujours — éviter les retry ES)

**Codes d'erreur :**
- `401` — signature invalide
- `400` — payload malformé / `hits` vide
- `500` — erreur inattendue (loggée, pas propagée)

---

## 10. Secrets (`src/incident_notifier/secrets.py`)

```python
def get_secret(name: str) -> str: ...
```

Secrets fetchés au cold start et mis en cache mémoire :
- `SLACK_BOT_TOKEN`
- `ES_WEBHOOK_SECRET`
- `KIBANA_BASE_URL` (pour construire les liens)

---

## 11. Tests

| Fichier | Scénarios |
|---|---|
| `tests/test_elasticsearch_adapter.py` | `parse()` sur fixtures critical/high/medium/low ; `verify()` valide/invalide ; fallback `functionalName` absent ; `hits` vide → 400 |
| `tests/test_routing.py` | `classify()` → bons channels/mentions pour chaque severity |
| `tests/test_slack_notifier.py` | Block Kit correct ; `chat_postMessage` appelé N fois (client mocké) ; dedup skip |

**Fixtures JSON :**
- `tests/fixtures/elasticsearch_fatal.json` — `level: [60]`
- `tests/fixtures/elasticsearch_error.json` — `level: [50]`
- `tests/fixtures/elasticsearch_warn.json` — `level: [40]`
- `tests/fixtures/elasticsearch_info.json` — `level: [30]` (basé sur le payload réel fourni)
