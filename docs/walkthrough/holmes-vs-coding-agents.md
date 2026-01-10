# HolmesGPT vs General Coding Agents

While general-purpose AI coding agents like Claude Code, GitHub Copilot, and Cursor have revolutionized software development, **HolmesGPT is purpose-built for a different problem**: production observability and incident response in cloud-native environments.

This guide explains the architectural and design differences that make HolmesGPT uniquely suited for troubleshooting production systems at scale.

## The Challenge: Production Observability is Different

Production troubleshooting has unique requirements that general coding agents aren't designed to handle:

| Requirement | General Coding Agents | HolmesGPT |
|------------|----------------------|-----------|
| **Data Scale** | Works with files in a codebase | Works with terabytes of telemetry data |
| **Tool Execution** | Requires user approval for each command | Autonomous with pre-approved read-only tools |
| **Domain** | General software development | Specialized for observability platforms |
| **Visualizations** | Describes charts in text (can hallucinate) | Direct links to native dashboards (no hallucinations) |
| **Context** | Static code files | Live, streaming metrics, logs, and traces |

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

## 2. Safe, Read-Only Access Without Approval Prompts

### The Problem

Production troubleshooting requires querying dozens of data sources rapidly. In a typical incident investigation, you might need to:

- Query 5-10 Prometheus metrics
- Search logs across multiple services
- Check Kubernetes pod status and events
- Examine Grafana dashboards
- Query trace data from Tempo
- Check recent deployments in ArgoCD

**With approval prompts, this becomes unusable**: 20+ "Execute this command? (yes/no)" interruptions destroy investigation flow during a time-sensitive incident.

### How General Agents Handle This

General coding agents use an interactive approval model:

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

## Summary: When to Use Each

| Use Case | Recommended Tool |
|----------|-----------------|
| **Code development, debugging application logic** | General coding agents |
| **Production incident response** | HolmesGPT |
| **Analyzing large-scale metrics/logs** | HolmesGPT |
| **Troubleshooting microservices** | HolmesGPT |
| **Alert investigation and root cause analysis** | HolmesGPT |
| **Writing tests, refactoring code** | General coding agents |
| **DevOps automation scripts** | General coding agents |
| **On-call troubleshooting** | HolmesGPT |

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
