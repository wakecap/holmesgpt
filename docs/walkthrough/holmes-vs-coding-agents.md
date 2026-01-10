# HolmesGPT vs General Coding Agents

While general-purpose AI coding agents like Claude Code, GitHub Copilot, and Cursor have revolutionized software development, **HolmesGPT is purpose-built for a different problem**: production observability and incident response in cloud-native environments.

This guide explains the architectural and design differences that make HolmesGPT uniquely suited for troubleshooting production systems at scale.

## The Challenge: Production Observability is Different

Production troubleshooting has unique requirements that general coding agents aren't designed to handle:

| Requirement | General Coding Agents | HolmesGPT |
|------------|----------------------|-----------|
| **Data Scale** | Works with files in a codebase | Works with terabytes of telemetry data |
| **Tool Execution** | Requires approval OR sandboxed but lacks integrations | Autonomous with 40+ pre-built observability integrations |
| **Domain** | General software development | Specialized for observability platforms |
| **Visualizations** | Describes charts in text (can hallucinate) | Direct links to native dashboards (no hallucinations) |
| **Context** | Static code files | Live, streaming metrics, logs, and traces |
| **Workflow Integration** | Manual copy/paste to incident tools | Native alert source and destination integrations |

## 1. Handling Terabytes of Data Beyond Context Windows

### The Problem

Production systems generate enormous amounts of telemetry data:

- **Metrics**: Thousands of time series per service, millions of data points per hour
- **Logs**: Gigabytes to terabytes per day across distributed systems
- **Traces**: Millions of spans capturing every request
- **Events**: Continuous stream of state changes and notifications

**No LLM context window can hold this data**—even with 200K+ token windows, you can't fit a week's worth of production metrics for a medium-sized cluster.

### How General Agents Handle This

General coding agents typically struggle with large-scale data:

1. **Manual filtering**: User must export/filter data before feeding it to the agent
2. **Context overflow**: Agent hits token limits and loses important context
3. **Sequential analysis**: Must break investigation into many manual steps
4. **No optimization**: Treats all data equally, doesn't understand telemetry patterns

### How HolmesGPT Solves This

HolmesGPT is architected specifically for observability data at scale:

#### Intelligent Query Narrowing

Holmes automatically refines queries to find relevant data:

```python
# Holmes's investigation flow for a memory leak:
1. Query high-level cluster metrics (last 7 days)
   → Identifies memory trend starting 3 days ago
2. Query namespace-level metrics (last 3 days)
   → Narrows down to "payments" namespace
3. Query pod-level metrics (last 3 days, payments namespace)
   → Identifies specific pod with leak
4. Fetch detailed container metrics (last 24h, specific pod)
   → Analyzes detailed memory allocation patterns
```

Each step uses targeted queries instead of loading everything into context.

#### Server-Side Filtering

All HolmesGPT toolsets are designed with server-side filtering:

```yaml
# Example: Prometheus toolset
- name: prometheus_query_range
  parameters:
    - name: query
      description: "PromQL query to execute"
    - name: start_time
      description: "Start time (Unix timestamp or relative like '-1h')"
    - name: end_time
      description: "End time (defaults to now)"
    - name: step
      description: "Query resolution step"
```

Holmes constructs precise queries with appropriate time ranges and filters, retrieving only relevant data points.

#### Tool Output Transformers

For tools that still return large outputs, HolmesGPT uses transformers to summarize before sending to the LLM:

```yaml
transformers:
  - type: llm_summarize
    model: gpt-4o-mini  # Fast, cheap model for summarization
    max_tokens: 500
    prompt: "Summarize this data focusing on anomalies and errors"
```

This keeps context windows manageable while preserving critical information.

### Real-World Example

**Investigating a latency spike in an e-commerce platform:**

**General Coding Agent Approach:**
```
User: "Here's our Prometheus data [pastes 10MB of metrics export]"
Agent: "I see the context is too large. Can you filter to just the checkout service?"
User: [Exports filtered data, pastes again]
Agent: "I see high latency. Can you provide logs for that time period?"
User: [Exports logs, pastes again]
Agent: "Context limit reached. Let's start over with just the last hour..."
```

**HolmesGPT Approach:**
```bash
holmes ask "investigate the latency spike in checkout service around 2pm today"

# Holmes automatically:
1. Queries Prometheus for checkout service latency (2pm ±1h)
2. Identifies spike at 2:15pm
3. Queries detailed metrics for that 10-minute window
4. Fetches logs from Loki for affected pods (2:15-2:25pm)
5. Checks Kubernetes events for that time range
6. Analyzes and reports root cause in < 30 seconds
```

All without hitting context limits or requiring manual data extraction.

## 2. Built-In Observability Integrations

### The Problem

Production troubleshooting requires querying dozens of specialized data sources rapidly. In a typical incident investigation, you might need to:

- Query 5-10 Prometheus metrics
- Search logs across multiple services
- Check Kubernetes pod status and events
- Examine Grafana dashboards
- Query trace data from Tempo
- Check recent deployments in ArgoCD

**General coding agents face two challenges**:

1. **With approval prompts**: 20+ "Execute this command? (yes/no)" interruptions destroy investigation flow during a time-sensitive incident
2. **With sandboxed environments**: Even when tools are pre-approved, they lack integrations with observability platforms—you'd need to manually build connectors to Prometheus, Grafana, Loki, etc.

### How General Agents Handle This

**Interactive approval model:**

```
Agent: "I'll run: kubectl get pods -n production"
User: [y/n]? y

Agent: "I'll run: kubectl describe pod checkout-api-abc123"
User: [y/n]? y

Agent: "I'll run: kubectl logs checkout-api-abc123"
User: [y/n]? y

Agent: "I'll query Prometheus with: rate(http_requests[5m])"
User: [y/n]? y

... 15 more prompts ...
```

This makes sense for write operations (deployments, configuration changes) but is impractical for read-only troubleshooting.

**Sandboxed environment approach:**

Some coding agents run in sandboxed environments where all tools are pre-approved. However, they still face a critical limitation:

```bash
# What you can do in a sandbox:
✅ kubectl get pods
✅ kubectl logs <pod>
✅ curl http://localhost:9090/api/v1/query  # IF Prometheus is port-forwarded

# What you can't easily do:
❌ Query Prometheus (no built-in integration)
❌ Search Grafana dashboards (no API client)
❌ Query Loki logs (need to build LogQL queries manually)
❌ Fetch Tempo traces (no trace client)
❌ Access cloud provider APIs (AWS, Azure, GCP)
```

You'd need to:
1. Manually port-forward each service
2. Construct API calls from scratch
3. Parse and interpret JSON responses
4. Build context about which APIs to call and how

This defeats the purpose of using an agent—you're doing most of the integration work yourself.

### How HolmesGPT Solves This

HolmesGPT uses a **pre-approved toolset architecture**:

#### Safe by Design

All built-in toolsets are:

- ✅ **Read-only**: No ability to modify state
- ✅ **Validated**: Bash commands go through safety validation
- ✅ **RBAC-compliant**: Respects platform permissions
- ✅ **Audited**: All tool calls are logged

```yaml
# Example: Kubernetes toolset (excerpt)
tools:
  - name: kubectl_describe
    description: "Get detailed info about a resource"
    bash: "kubectl describe {{ resource_type }} {{ name }} -n {{ namespace }}"
    # ✅ Read-only operation

  # ❌ Not included: kubectl delete, kubectl apply, kubectl edit
```

#### Autonomous Execution

Holmes executes approved tools without interruption:

```bash
holmes ask "why is the payment service slow?"

# Internal execution flow (no prompts):
✓ kubectl_get_pods(namespace="payments")
✓ prometheus_query_range(query="rate(http_requests{service='payment'}[5m])", start="-1h")
✓ loki_query(query='{namespace="payments"}', start="-1h")
✓ kubectl_describe(resource="pod", name="payment-api-xyz")
✓ grafana_get_dashboard(dashboard="payment-service-overview")

Analysis complete in 12 seconds.
```

All these operations execute automatically because they're pre-approved safe operations.

#### RBAC Enforcement

HolmesGPT respects existing access controls:

- **Kubernetes**: Uses your kubeconfig permissions
- **Prometheus**: Uses configured auth tokens
- **Grafana**: Respects dashboard permissions
- **AWS/Cloud**: Uses IAM roles and policies

If you don't have permission to view production logs, neither does Holmes.

### Real-World Example

**Investigating a production incident at 3am:**

**General Coding Agent:**
```
23 approval prompts over 15 minutes
Multiple context switches between agent and terminal
Lost train of thought due to interruptions
Incident extended by slow investigation
```

**HolmesGPT:**
```bash
holmes ask "what's causing the spike in 5xx errors?"

# Autonomous investigation completes in 30 seconds
# Root cause identified: database connection pool exhaustion
# Suggested remediation: scale up connection pool or add read replicas
```

**Result**: Faster incident resolution, less cognitive load during high-pressure situations.

## 3. Hallucination-Free Visualization Paths

### The Problem

Observability relies heavily on visualizations—graphs, charts, dashboards, flame graphs. These visualizations contain critical information that's difficult to convey in text.

When general AI agents try to describe visualizations, several issues occur:

- ❌ **Hallucinated values**: "The graph shows 500 requests/sec" when it's actually 5,000
- ❌ **Missed patterns**: Visual patterns (spikes, dips, correlations) lost in text descriptions
- ❌ **No interactivity**: Can't zoom, pan, or explore the data
- ❌ **Trust issues**: How do you verify the agent's description is accurate?

### How General Agents Handle This

General coding agents typically describe visualizations in text:

```
Agent: "Based on the metrics, I can see that:
- CPU usage peaked at around 85% at 2:15pm
- Memory utilization was steady around 60%
- Request rate spiked to approximately 1,200 req/s
- Error rate increased from 0.1% to 2.3%"

User: "Are you sure about those numbers? Can I see the actual graph?"
Agent: "I don't have the ability to generate graphs..."
```

Even worse, the agent might be viewing a text export of metrics data and hallucinating the patterns it describes.

### How HolmesGPT Solves This

HolmesGPT provides **direct visualization paths** that bypass the LLM entirely:

#### Native Dashboard Links

Holmes generates direct links to your observability tools with correct parameters:

```markdown
## Investigation Results

**Grafana Dashboard**: [Payment Service Overview](https://grafana.company.com/d/payment-service?from=1736510400000&to=1736514000000&var-service=payment-api&var-namespace=production)

**Prometheus Graph**: [Request Rate](https://prometheus.company.com/graph?g0.expr=rate(http_requests_total{service="payment-api"}[5m])&g0.range_input=1h)

**Tempo Trace**: [Failed checkout transaction](https://tempo.company.com/trace/abc123def456)
```

These links:

- ✅ **Are programmatically generated**: No LLM hallucination possible
- ✅ **Include correct time ranges**: Automatically calculated from investigation context
- ✅ **Set dashboard variables**: Service names, namespaces, etc. pre-filled
- ✅ **Are interactive**: Full access to zoom, pan, and explore

#### Structured Tool Outputs

For tools that return visualization data, Holmes structures the output to preserve metadata:

```python
# Example: grafana_get_dashboard tool output
{
  "dashboard": {
    "title": "Payment Service Overview",
    "uid": "payment-service-abc123",
    "url": "https://grafana.company.com/d/payment-service-abc123"
  },
  "panels": [
    {
      "title": "Request Rate",
      "type": "graph",
      "targets": [
        {
          "expr": "rate(http_requests_total{service='payment'}[5m])",
          "legendFormat": "{{method}} {{status}}"
        }
      ]
    }
  ],
  "visualizationUrl": "https://grafana.company.com/d/payment-service-abc123?from=1736510400000&to=1736514000000"
}
```

The LLM sees the structure and metadata but doesn't interpret the visual data—it just includes the `visualizationUrl` in its response.

#### Smart Visualization Selection

Holmes knows which visualization is appropriate for each investigation:

| Investigation Type | Visualization | Why |
|-------------------|---------------|-----|
| Latency issues | Service-specific dashboard with time range | Shows request duration trends |
| Error spike | Error rate graphs + logs | Correlates errors with log messages |
| Memory leak | Memory utilization over time + heap dumps | Shows gradual memory increase |
| Trace analysis | Distributed trace waterfall | Shows request flow across services |

### Real-World Example

**Investigating high checkout latency:**

**General Coding Agent:**
```
Agent: "I've analyzed the metrics. The checkout service shows:
- Average latency increased from 150ms to 450ms at 2:30pm
- P95 latency is around 800ms
- Database query time appears to be the bottleneck
- The payment API seems normal"

User: "Can I see the actual graphs? Those numbers seem high."
Agent: "I don't have graphing capabilities, but you can check your
        Grafana dashboard manually..."
```

**HolmesGPT:**
```markdown
## Root Cause Analysis

The checkout service latency increased due to slow database queries
starting at 14:30 UTC.

**Evidence:**

- [Grafana: Checkout Service Dashboard](https://grafana.company.com/d/checkout?from=1736510400000&to=1736514000000)
  Shows latency increase from 150ms → 450ms at 14:30

- [Prometheus: Database Query Duration](https://prometheus.company.com/graph?g0.expr=histogram_quantile(0.95,rate(db_query_duration_bucket{service="checkout"}[5m])))
  Shows P95 query time increased from 50ms → 350ms

- [Tempo: Example Slow Trace](https://tempo.company.com/trace/xyz789)
  Shows 340ms spent in database query vs normal 45ms

**Recommendation:**
Review slow query logs and check if recent database schema changes
affected query performance.
```

**Result**: User can immediately verify the analysis by clicking links, seeing the actual data, and trusting the conclusions.

## 4. Workflow & On-Call Integration

### The Problem

Production incidents don't happen in isolation—they're part of a broader workflow:

1. **Alert fires** in AlertManager/PagerDuty/OpsGenie
2. **On-call engineer gets paged**
3. **Investigation begins** across multiple tools
4. **Root cause identified**
5. **Resolution documented** in the incident management system
6. **Team notified** via Slack/Teams

General coding agents require manual copying and pasting at every step:

- Copy alert details from PagerDuty → paste into chat
- Copy investigation findings → paste back into PagerDuty
- Copy summary → paste into Slack
- Manual context switching between tools

This is slow, error-prone, and disrupts investigation flow.

### How General Agents Handle This

**Manual workflow integration:**

```
1. On-call receives PagerDuty alert
2. Opens PagerDuty, reads alert details
3. Copies alert description
4. Opens coding agent chat
5. Pastes alert details with "investigate this"
6. Agent investigates
7. Copy agent's findings
8. Switch back to PagerDuty
9. Paste findings into incident notes
10. Open Slack
11. Manually summarize and post to team channel
```

Total time: Multiple minutes of context switching during a critical incident.

### How HolmesGPT Solves This

HolmesGPT provides **native integrations** for the entire incident lifecycle:

#### Alert Source Integration

Automatically fetch alerts from multiple sources:

```bash
# AlertManager integration
holmes investigate alertmanager --alertmanager-url https://alertmanager.company.com

# PagerDuty integration
holmes investigate pagerduty --pagerduty-api-key <key>

# OpsGenie integration
holmes investigate opsgenie --opsgenie-api-key <key>

# Jira integration
holmes investigate jira --jira-url https://company.atlassian.net
```

Holmes automatically:
- Fetches active alerts/incidents
- Extracts relevant metadata (labels, tags, severity)
- Prioritizes which alerts to investigate
- Gathers initial context from alert annotations

#### Investigation Loop

Once an alert is fetched, Holmes:

1. **Analyzes alert metadata** - Understands what service/component is affected
2. **Selects relevant toolsets** - Automatically enables appropriate integrations
3. **Gathers multi-source data** - Queries Prometheus, Kubernetes, logs, traces
4. **Performs root cause analysis** - Correlates data to identify issues
5. **Generates actionable recommendations** - Suggests fixes or next steps

All autonomously, without manual tool selection or approval prompts.

#### Results Delivery

Holmes can write investigation results back to the source:

```bash
# Write findings back to PagerDuty incident
holmes investigate pagerduty --pagerduty-api-key <key> --update

# Write to Jira ticket
holmes investigate jira --jira-url https://company.atlassian.net --update

# Send to Slack (via Robusta SaaS or custom integration)
# Results automatically posted to configured Slack channel
```

**Result format:**
- **Root cause summary** - Clear explanation of what went wrong
- **Evidence** - Links to dashboards, traces, and relevant data
- **Recommendations** - Specific actions to resolve or mitigate
- **Timestamp and metadata** - When investigation ran, which tools were used

#### Interactive Follow-Up

Holmes supports interactive mode for drill-down investigations:

```bash
holmes ask "investigate the memory leak in payment-service" --interactive

# Holmes provides initial analysis
# User can ask follow-up questions:
> "show me the specific pod that's leaking"
> "what was the memory usage yesterday at this time?"
> "check if this correlates with deployment times"
```

### Real-World Workflow Comparison

**General Coding Agent Workflow:**

```
Time 00:00 - PagerDuty page received: High API latency
Time 00:02 - Engineer opens laptop, logs into PagerDuty
Time 00:04 - Copies alert details, opens coding agent
Time 00:06 - Pastes alert, waits for investigation to start
Time 00:08 - Agent asks to run kubectl commands (approval prompts)
Time 00:12 - Agent requests to query Prometheus (manual port-forward needed)
Time 00:15 - Copy/paste Prometheus metrics into chat
Time 00:18 - Agent provides analysis
Time 00:20 - Copy findings, paste into PagerDuty notes
Time 00:22 - Manually post summary to Slack
Time 00:25 - Total time to investigation result in tools: 25 minutes
```

**HolmesGPT Workflow:**

```bash
Time 00:00 - PagerDuty page received: High API latency
Time 00:02 - Engineer runs: holmes investigate pagerduty --update
Time 00:03 - Holmes automatically:
           ✓ Fetches incident from PagerDuty
           ✓ Queries Prometheus for API latency metrics
           ✓ Checks Kubernetes pod status
           ✓ Searches logs in Loki
           ✓ Identifies root cause: Database connection pool exhaustion
           ✓ Writes findings back to PagerDuty incident
           ✓ Posts summary to configured Slack channel
Time 00:03 - Total time to investigation result in tools: 3 minutes
```

**Result**: 8x faster incident investigation with full audit trail.

### Integration Architecture

HolmesGPT's integration architecture supports:

| Integration Type | Purpose | Examples |
|-----------------|---------|----------|
| **Alert Sources** | Fetch alerts to investigate | AlertManager, PagerDuty, OpsGenie, Jira |
| **Data Sources** | Gather investigation evidence | Prometheus, Loki, Tempo, Kubernetes, AWS |
| **Knowledge Sources** | Access runbooks and docs | Confluence, Slab, internal wikis |
| **Destinations** | Deliver investigation results | Slack, PagerDuty, Jira, GitHub |
| **Remediation** | Automated fixes (optional) | GitHub PRs, Kubernetes patches |

All integrations:
- ✅ **Work out of the box** - No custom development required
- ✅ **Respect permissions** - Use configured RBAC and API keys
- ✅ **Log all actions** - Full audit trail for compliance
- ✅ **Handle errors gracefully** - Retry logic and fallbacks built-in

## Summary: When to Use Each

| Use Case | Recommended Tool |
|----------|-----------------|
| **Code development, debugging application logic** | General coding agents |
| **Production incident response** | HolmesGPT |
| **Analyzing large-scale metrics/logs** | HolmesGPT |
| **Troubleshooting microservices** | HolmesGPT |
| **Alert investigation and root cause analysis** | HolmesGPT |
| **On-call rotations with PagerDuty/OpsGenie** | HolmesGPT |
| **Automated alert enrichment** | HolmesGPT |
| **Writing tests, refactoring code** | General coding agents |
| **DevOps automation scripts** | General coding agents |
| **Local development and testing** | General coding agents |

## Try It Yourself

See the difference firsthand:

```bash
# Install HolmesGPT
pip install holmesgpt

# Investigate your production system
holmes ask "what pods are having issues in production and why?"

# Holmes will autonomously:
# ✓ Query your cluster
# ✓ Analyze metrics from Prometheus
# ✓ Fetch relevant logs
# ✓ Provide actionable insights with visualization links
# ✓ All in seconds, without approval prompts
```

**[Get started →](../installation/cli-installation.md)**

## Further Reading

- **[Installation Guide](../installation/cli-installation.md)** - Set up HolmesGPT
- **[Built-in Toolsets](../data-sources/builtin-toolsets/index.md)** - See all 40+ integrations
- **[Tool Output Transformers](../development/transformers.md)** - Learn how large outputs are handled
- **[Kubernetes Permissions](../reference/kubernetes-permissions.md)** - Understand RBAC and security model
