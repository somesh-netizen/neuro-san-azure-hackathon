"""
Central configuration for the Neuro-San load test suite.
Override any value via environment variables or a .env file in this directory.
"""

import os
from dotenv import load_dotenv

load_dotenv(override=False)

# ── Endpoints ─────────────────────────────────────────────────────────────────
API_URL      = os.getenv("API_URL",      "https://neurosanhackathon-api.eastus.cloudapp.azure.com")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://hackathon.evolution.ml")

# ── Agents discovered from GET /api/v1/list ────────────────────────────────────
KNOWN_AGENTS = [

    "agent_network_designer",
]

DEFAULT_AGENT = os.getenv("AGENT", "agent_network_designer")

# ── Timeouts (seconds) ────────────────────────────────────────────────────────
# CHAT_TIMEOUT is a SILENCE/read timeout for the streamed response, not a total cap.
# Raised 360→600 so we MEASURE the true completion-time tail under load instead of
# calling a slow-but-still-streaming design a "failure" at 6 min. Matches the ingress
# proxy-read-timeout (600s). A design that keeps emitting progress is never cut.
CHAT_TIMEOUT = int(os.getenv("CHAT_TIMEOUT", "600"))
FAST_TIMEOUT = int(os.getenv("FAST_TIMEOUT", "10"))

# ── Think time (closed-loop pacing between a participant's turns) ─────────────
# Applied AFTER a turn's answer arrives, before the next turn. NOT a compute cap.
THINK_TIME_MIN = int(os.getenv("THINK_TIME_MIN", "120"))   # 2 min
THINK_TIME_MAX = int(os.getenv("THINK_TIME_MAX", "240"))   # 4 min

# ── LLM model + pricing (used by metrics.py for cost estimation) ──────────────
# The Azure deployment is named "gpt-5-mini" but the underlying model is gpt-4o-mini
# (verified via az: model=gpt-4o-mini, version 2024-07-18). Pricing key must match the
# REAL model or cost is overstated ~25-33× (gpt-4o rates). gpt-4o-mini = $0.15/$0.60 /1M.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ── Token quota (aggregate Azure OpenAI deployment capacity) ──────────────────
# 11 working keys × 30M TPM each = 330M TPM (key-9 dropped: no model deployment in
# southeastasia). Tokens are NOT the bottleneck — a CPU-bound pod burns <10M TPM — so
# 330M is ~50× headroom. This ceiling is for burn-rate % context, not a real risk.
TOKEN_QUOTA_TOTAL = int(os.getenv("TOKEN_QUOTA_TOTAL", "330000000"))  # 11 × 30M

# ── The 11 Azure OpenAI resources behind the pods (key | resource | resource-group) ──
# Used by metrics.get_per_key_tpm() to pull per-key token usage from Azure Monitor.
# key 9 (southeastasia) intentionally absent — dropped from the deployment.
AZURE_OPENAI_RESOURCES = [
    ("1",  "25083-mqryb3vb-southcentralus", "rg-2508345-2922"),
    ("2",  "azure-openai-cognizant-ai-lab", "neuro-san-studio-marketplace-rg"),
    ("3",  "2508345-2051-resource",         "MC_neuro-san-studio-marketplace-rg_neuro-san-hackathon-aks_eastus"),
    ("4",  "25083-mqry1k59-northcentralus", "rg-2508345-4576_ai"),
    ("5",  "25083-mqqgolnd-centralus",      "rg-2508345-3558"),
    ("6",  "25083-mqqgg6gs-swedencentral",  "rg-2508345-4576_ai"),
    ("7",  "25083-mqszfieo-westus",         "rg-2508345-3004"),
    ("8",  "25083-mqt02653-westeurope",     "rg-2508345-7536"),
    ("10", "25083-mqt06fkz-francecentral",  "rg-2508345-5993"),
    ("11", "25083-mqszostk-westus3",        "rg-2508345-4759"),
    ("12", "25083-mqszy2vy-southindia",     "rg-2508345-5101"),
]
PER_KEY_TPM_LIMIT = int(os.getenv("PER_KEY_TPM_LIMIT", "30000000"))  # 30M each

# ── Backend node sizing (for per-pod CPU% math: 1 pod per D16s_v3 node) ───────
BACKEND_NODE_VCPU    = int(os.getenv("BACKEND_NODE_VCPU", "16"))       # D16s_v3
BACKEND_MEM_LIMIT_MI = int(os.getenv("BACKEND_MEM_LIMIT_MI", "49152"))  # 48Gi limit (raised from 6Gi)
UI_CPU_LIMIT         = float(os.getenv("UI_CPU_LIMIT", "2"))           # UI 2-core limit
UI_MEM_LIMIT_MI      = int(os.getenv("UI_MEM_LIMIT_MI", "6144"))       # UI 6Gi limit

# ── Azure Blob metrics (optional — needs az login with storage access) ────────
BLOB_STORAGE_ACCOUNT = os.getenv("BLOB_STORAGE_ACCOUNT", "")
BLOB_RESOURCE_GROUP  = os.getenv("BLOB_RESOURCE_GROUP",  "")

# ── Realistic chat messages used across all user types ────────────────────────
CHAT_MESSAGES = [
    # ── Agent / multi-agent design ────────────────────────────────────────────
    "Create a simple agent network for customer support automation.",
    "Design an agent network that classifies and routes IT support tickets.",
    "Build an agent network for automated code review and pull request feedback.",
    "Design a multi-agent system that monitors social media and generates daily reports.",
    "Create an agent network for automated onboarding of new employees.",
    "Design an agent that answers questions about company HR policies.",
    "Build an agent network for e-commerce product recommendations.",
    "Design a multi-agent pipeline for processing and summarising customer feedback.",
    "Create an agent that helps junior developers debug Python errors.",
    "Design an agent network for automated invoice processing and approval.",
    "Build an agent that monitors cloud costs and suggests optimisations.",
    "Design a research agent that summarises recent papers on a given topic.",
    "Create an agent network for automated content moderation.",
    "Design an agent that helps product managers write user stories.",
    "Build an agent network for supply chain risk monitoring.",
    "Design an agent that triages incoming support emails and drafts responses.",
    "Create a multi-agent system for automated A/B test analysis.",
    "Design an agent that generates weekly engineering team status reports.",
    "Build an agent network for automated database query optimisation.",
    "Design an agent that helps sales teams prepare for customer calls.",
    "Create an agent network for real-time fraud detection alerts.",
    "Design an agent that reviews infrastructure-as-code for security issues.",
    "Build a multi-agent system for personalised learning path recommendations.",
    "Design an agent that monitors application logs and alerts on anomalies.",
    "Create an agent network for legal contract review and risk flagging.",

    # ── Azure and cloud ────────────────────────────────────────────────────────
    "Explain what Azure Kubernetes Service is in simple terms.",
    "What is horizontal pod autoscaling and when should I use it?",
    "How do I set up a private AKS cluster with no public IP?",
    "What is the difference between Azure Functions and Azure Container Apps?",
    "Explain Azure Managed Identity and why it is better than storing credentials.",
    "How does Azure Key Vault integrate with Kubernetes via external-secrets?",
    "What are Azure availability zones and how do they affect AKS node pools?",
    "How do I enable Azure Monitor for containers on my AKS cluster?",
    "What is the difference between Azure Blob and Azure Data Lake Storage?",
    "How do I configure Azure Front Door for global load balancing?",
    "Explain Azure Service Bus vs Event Hub — when would you use each?",
    "How do I migrate a VM-based workload to AKS with minimal downtime?",
    "What is Azure Chaos Studio and how would I use it for resilience testing?",
    "How do I set up blue-green deployments on AKS?",
    "What are the cost optimisation options for an AKS cluster running 24/7?",
    "How do I enable GPU nodes on AKS for ML inference workloads?",
    "Explain Azure RBAC vs Kubernetes RBAC — how do they interact?",
    "What is the best way to handle secrets rotation in a live AKS deployment?",
    "How do I configure pod disruption budgets for zero-downtime deployments?",
    "What are the networking options in AKS — kubenet vs Azure CNI?",

    # ── Kubernetes and DevOps ─────────────────────────────────────────────────
    "How do I monitor a Kubernetes cluster effectively with minimal overhead?",
    "What is the difference between a Deployment and a StatefulSet?",
    "How do I debug a pod that is stuck in CrashLoopBackOff?",
    "Explain Kubernetes resource requests and limits — what happens without them?",
    "How do I set up a GitOps workflow with ArgoCD on Kubernetes?",
    "What is a Kubernetes operator and when should I build one?",
    "How do I implement canary deployments in Kubernetes?",
    "Explain Kubernetes network policies — how do I restrict pod-to-pod traffic?",
    "What is the best way to manage multiple Kubernetes environments with Helm?",
    "How do I troubleshoot high CPU throttling on a Kubernetes pod?",
    "What are Kubernetes taints and tolerations?",
    "How do I set up Prometheus and Grafana on AKS from scratch?",
    "What is a Kubernetes sidecar container and when is it useful?",
    "How do I reduce image pull time for large Docker images on Kubernetes?",
    "Explain the Kubernetes scheduler — how does it decide where to place pods?",
    "What is OOMKilled and how do I prevent it in production?",
    "How do I implement rolling updates with automatic rollback on failure?",
    "What is Kubernetes service mesh and when does it add value?",
    "How do I handle configuration drift in a Kubernetes cluster?",
    "What are the best practices for writing a production-ready Dockerfile?",

    # ── Python and programming ────────────────────────────────────────────────
    "How can I reduce cold-start latency in a containerised Python service?",
    "What is the best way to structure a large Python project with multiple packages?",
    "Explain Python asyncio — when should I use it over threads?",
    "How do I profile a slow Python API to find the bottleneck?",
    "What is the difference between Pydantic v1 and v2?",
    "How do I implement exponential backoff with jitter in Python?",
    "Explain Python's GIL — does it affect async code?",
    "What is the best Python library for building CLI tools in 2024?",
    "How do I write a Python decorator that retries on exception?",
    "What is the fastest way to read a 10GB CSV file in Python?",
    "How do I implement a circuit breaker pattern in Python?",
    "Explain the difference between multiprocessing and multithreading in Python.",
    "What are Python dataclasses and when should I use them over regular classes?",
    "How do I write unit tests for code that makes external API calls?",
    "What is dependency injection in Python and why does it matter for testing?",
    "How do I use Python type hints effectively in a large codebase?",
    "What is the best way to manage Python environment variables securely?",
    "How do I implement rate limiting in a FastAPI application?",
    "Explain Python's memory model — how does garbage collection work?",
    "What are the performance differences between list, tuple, and set in Python?",

    # ── AI, ML and LLMs ───────────────────────────────────────────────────────
    "Explain agent chaining and how multi-agent workflows improve AI systems.",
    "What is retrieval-augmented generation (RAG) and when should I use it?",
    "How do I evaluate the quality of an LLM-generated response automatically?",
    "What is prompt engineering and what techniques actually work?",
    "How do I prevent prompt injection attacks in a production AI system?",
    "Explain the difference between fine-tuning and few-shot prompting.",
    "What is the best way to stream LLM responses to a web frontend?",
    "How do I implement semantic search over a large document corpus?",
    "What are embeddings and how do vector databases store them?",
    "How do I reduce hallucinations in an LLM-powered product?",
    "Explain the ReAct agent pattern — how does it differ from chain-of-thought?",
    "What is function calling in OpenAI models and how do I use it?",
    "How do I build an AI system that can use tools like search or a calculator?",
    "What are the token limits for GPT-4 and how do I handle long documents?",
    "How do I implement a memory system for a conversational AI agent?",
    "What is the difference between zero-shot, one-shot, and few-shot prompting?",
    "How do I monitor LLM latency and cost in production?",
    "What is LangChain and when is it worth using versus building from scratch?",
    "How do I implement a feedback loop to improve an AI agent over time?",
    "What are the ethical considerations when deploying an AI chatbot publicly?",

    # ── System design and architecture ────────────────────────────────────────
    "Help me design a fault-tolerant microservices architecture.",
    "Describe a scalable architecture for processing real-time event streams.",
    "What is the best way to handle API rate limiting in a distributed system?",
    "How do I design a system that handles 100,000 concurrent WebSocket connections?",
    "What is the CQRS pattern and when does it make sense?",
    "How do I design an idempotent API that is safe to retry?",
    "What is eventual consistency and how do I handle it in my application?",
    "How do I design a multi-tenant SaaS application on Kubernetes?",
    "What is the strangler fig pattern for migrating a monolith to microservices?",
    "How do I implement distributed tracing across multiple microservices?",
    "What is the saga pattern and how does it handle distributed transactions?",
    "How do I design a job queue system that survives pod restarts?",
    "What is the best architecture for a real-time leaderboard with 1 million users?",
    "How do I design an API gateway for a large microservices platform?",
    "What is database sharding and when should I use it?",
    "How do I implement a cache invalidation strategy that doesn't cause stale data?",
    "What is the event sourcing pattern and what problems does it solve?",
    "How do I design a system that can replay historical events for debugging?",
    "What are the trade-offs between REST, GraphQL, and gRPC for internal APIs?",
    "How do I design a notification system that works at scale?",

    # ── Security ──────────────────────────────────────────────────────────────
    "What are the best practices for securing a cloud-native application on Azure?",
    "How do I implement zero-trust networking in a Kubernetes cluster?",
    "What is OAuth2 PKCE and when should I use it instead of client credentials?",
    "How do I scan a Docker image for vulnerabilities in a CI pipeline?",
    "What are the most common security misconfigurations in Kubernetes?",
    "How do I implement mutual TLS between microservices?",
    "What is a supply chain attack and how do I protect my Python dependencies?",
    "How do I rotate secrets in a live production system without downtime?",
    "What is SAST vs DAST and how do they fit into a DevSecOps pipeline?",
    "How do I securely store API keys used by a mobile app?",

    # ── Data and analytics ────────────────────────────────────────────────────
    "How do I build a real-time analytics dashboard for a high-traffic API?",
    "What is the difference between a data lake and a data warehouse?",
    "How do I implement change data capture from a Postgres database?",
    "What is dbt and how does it fit into a modern data stack?",
    "How do I design a feature store for a machine learning platform?",
    "What are the best practices for data versioning in an ML pipeline?",
    "How do I monitor data quality automatically in a production pipeline?",
    "What is the medallion architecture (bronze/silver/gold) in data engineering?",
    "How do I handle schema evolution in an event-driven system?",
    "What is the best way to backfill historical data in a streaming pipeline?",

    # ── Random / hackathon vibes ──────────────────────────────────────────────
    "Hello, what can you help me with?",
    "What is the meaning of life according to an AI?",
    "Write me a haiku about Kubernetes.",
    "What would you do if you were a pod that kept getting OOMKilled?",
    "Explain cloud computing to my grandmother.",
    "What is the funniest bug you have ever heard of?",
    "If you had to pick one programming language for the rest of your life, what would it be and why?",
    "What is the best tech stack for a two-person startup in 2024?",
    "Write a motivational speech for a developer who just pushed to production on a Friday.",
    "What should I learn first — backend, frontend, or DevOps?",
    "How do I tell my manager the estimates were wrong?",
    "What is technical debt and how do I convince leadership it matters?",
    "Give me a 30-second elevator pitch for microservices.",
    "What is the most overengineered solution you know of?",
    "How do I stay motivated when debugging the same issue for three days?",
    "What is your favourite design pattern and why?",
    "Explain agile in one sentence.",
    "What is the difference between a senior and a junior engineer?",
    "How do I negotiate a better salary as a software engineer?",
    "Write a limerick about a failed deployment.",
    "What is the best way to document an API that developers will actually read?",
    "How do I build a side project that does not take over my life?",
    "What is the worst advice you have heard about software engineering?",
    "How do I deal with imposter syndrome as a developer?",
    "What would you build if you had unlimited compute and no deadlines?",
    "How do I explain to a non-technical stakeholder why we need unit tests?",
    "What is the two-pizza rule and does it actually work?",
    "Write a short story where a bug becomes a feature.",
    "What is the most important thing I should know as a new engineer joining a big company?",
    "How do I run a better engineering retrospective?",
    "What is the best way to onboard a new developer to a complex codebase?",
    "How do I write a good post-mortem after a production incident?",
    "What is platform engineering and how is it different from DevOps?",
    "How do I prioritise technical work against product feature requests?",
    "What is the difference between an SLO, SLA, and SLI?",

    # ── Agent / multi-agent design (extended) ─────────────────────────────────
    "Design an agent that reads Jira tickets and auto-assigns them to the right team.",
    "Build a multi-agent pipeline that ingests PDFs and answers questions from them.",
    "Create an agent network for automated penetration testing report generation.",
    "Design an agent that monitors GitHub PRs and leaves style-guide feedback.",
    "Build an agent that tracks competitor pricing and sends weekly summaries.",
    "Design a multi-agent system for orchestrating data migrations between clouds.",
    "Create an agent that converts natural language into SQL queries with validation.",
    "Design an agent network for automated insurance claim processing.",
    "Build an agent that summarises Slack channel activity into a daily digest.",
    "Create a multi-agent system for managing cloud infrastructure drift detection.",
    "Design an agent that generates Terraform code from architecture diagrams.",
    "Build an agent network for personalised fitness plan recommendations.",
    "Create an agent that interviews a codebase and writes missing documentation.",
    "Design a multi-agent pipeline for real-time news aggregation and fact-checking.",
    "Build an agent that generates test cases from a feature specification document.",
    "Design an agent network for automated financial report analysis.",
    "Create an agent that translates API documentation between languages.",
    "Build an agent network that monitors hospital bed availability in real time.",
    "Design an agent that generates marketing copy variants and picks the best one.",
    "Create an agent that reviews open-source contributions for licence compliance.",
    "Build a multi-agent system for predictive maintenance in manufacturing plants.",
    "Design an agent that learns user preferences and auto-organises email folders.",
    "Create an agent network for real-time sports commentary generation.",
    "Build an agent that converts spreadsheet formulas into plain English explanations.",
    "Design a multi-agent system for climate data aggregation and anomaly alerting.",

    # ── Azure extended ────────────────────────────────────────────────────────
    "How do I configure Azure Defender for Containers on an existing AKS cluster?",
    "What is the difference between Azure App Service and Azure Container Apps?",
    "How do I implement disaster recovery for an AKS cluster in a second region?",
    "What are the steps to enable workload identity on an existing AKS cluster?",
    "How do I reduce Azure egress costs for a data-heavy AKS workload?",
    "What is Azure Policy and how do I enforce resource tagging automatically?",
    "How do I set up Azure Private Link for a PostgreSQL Flexible Server?",
    "What is the difference between Azure Kubernetes Fleet Manager and regular AKS?",
    "How do I configure node auto-provisioning on AKS to cut idle compute costs?",
    "What is Azure Deployment Environments and how does it help MLOps teams?",
    "How do I migrate an Azure SQL Database to Azure Cosmos DB with zero downtime?",
    "What is the best way to do blue-green deployments with Azure Container Apps?",
    "How do I set up cross-region replication for Azure Container Registry?",
    "What is Azure Landing Zone and why does an enterprise need it?",
    "How do I export Azure Monitor metrics to a third-party observability platform?",
    "What is the difference between Azure DevOps and GitHub Actions for CI/CD?",
    "How do I implement network segmentation between AKS node pools?",
    "What are Azure Spot VMs and how do I use them safely for batch workloads?",
    "How do I configure Azure Application Gateway WAF in front of an AKS ingress?",
    "What is Azure Managed Grafana and how does it compare to self-hosted Grafana?",

    # ── Kubernetes extended ───────────────────────────────────────────────────
    "How do I implement fine-grained RBAC for a multi-team Kubernetes cluster?",
    "What is Karpenter and how does it differ from the Cluster Autoscaler?",
    "How do I configure Kubernetes resource quotas for namespace isolation?",
    "What is a Kubernetes admission webhook and when would I write one?",
    "How do I implement pod topology spread constraints for high availability?",
    "What is KEDA and how do I autoscale a deployment based on queue depth?",
    "How do I set up Kubernetes audit logging and ship it to a SIEM?",
    "What is Crossplane and how does it compare to Terraform for cloud resources?",
    "How do I reduce Kubernetes API server load in a very large cluster?",
    "What is Flux CD and how does it implement GitOps differently from ArgoCD?",
    "How do I safely drain a Kubernetes node during a scheduled maintenance window?",
    "What are init containers and what problems do they solve?",
    "How do I implement a custom Kubernetes controller using the controller-runtime?",
    "What is the difference between Helm and Kustomize for managing manifests?",
    "How do I implement resource-efficient logging at scale on Kubernetes?",
    "What is OpenTelemetry and how do I instrument a Python service with it?",
    "How do I configure liveness and readiness probes correctly to avoid false restarts?",
    "What is Velero and how do I use it for Kubernetes backup and restore?",
    "How do I implement mutual TLS between pods using cert-manager?",
    "What is a DaemonSet and when should I use it instead of a Deployment?",

    # ── Python extended ───────────────────────────────────────────────────────
    "How do I build a production-grade async API with FastAPI and SQLAlchemy?",
    "What is the best way to implement distributed locking in Python with Redis?",
    "How do I build a streaming HTTP response in FastAPI for LLM outputs?",
    "What is Poetry and how does it compare to pip and conda for dependency management?",
    "How do I implement a Python plugin system that loads modules at runtime?",
    "What is the best way to handle structured logging in a Python microservice?",
    "How do I build a Python library that supports both sync and async usage?",
    "What is hypothesis testing in Python and how do I use it for property-based tests?",
    "How do I implement a saga pattern in Python for distributed transactions?",
    "What is the best way to serialize complex Python objects to JSON?",
    "How do I build a Python gRPC service from a proto definition?",
    "What is Celery and when should I use it instead of a cloud-native job queue?",
    "How do I implement graceful shutdown for a Python ASGI server?",
    "What is the best way to do database migrations safely in production with Alembic?",
    "How do I build a Python package that publishes to PyPI via a GitHub Action?",
    "What is the difference between abc.ABC and Protocol in Python?",
    "How do I implement backpressure in a Python streaming data pipeline?",
    "What is the best way to do feature flags in a Python application?",
    "How do I debug a memory leak in a long-running Python process?",
    "What is the difference between deepcopy and shallow copy in Python?",

    # ── AI and ML extended ────────────────────────────────────────────────────
    "What is the difference between RAG and fine-tuning for domain-specific LLMs?",
    "How do I implement a hybrid search combining vector search and BM25?",
    "What is LLM output structured extraction and how do I do it reliably?",
    "How do I measure and reduce LLM hallucination rate in a production system?",
    "What is the difference between GPT-4o and GPT-4 Turbo for coding tasks?",
    "How do I implement a multi-modal agent that can analyse images and text?",
    "What is Constitutional AI and how does it influence how models behave?",
    "How do I build an LLM evaluation pipeline that runs automatically in CI?",
    "What is speculative decoding and how does it speed up LLM inference?",
    "How do I implement tool use in an AI agent without using a framework?",
    "What is the best way to chunk documents for retrieval-augmented generation?",
    "How do I implement a long-term memory system for an AI assistant?",
    "What is the difference between an AI agent and an AI workflow?",
    "How do I reduce the cost of LLM API calls without degrading quality?",
    "What is RLHF and why is it used to train conversational AI models?",
    "How do I implement streaming token generation in a web application?",
    "What is mixture of experts (MoE) and which models use it?",
    "How do I build a document QA system that cites its sources accurately?",
    "What is the best way to handle very long contexts in an LLM application?",
    "How do I detect and handle when a user is trying to jailbreak my AI agent?",

    # ── System design extended ────────────────────────────────────────────────
    "How do I design a globally distributed key-value store with strong consistency?",
    "What is the CAP theorem and what does it mean for a real application?",
    "How do I design a payment system that handles double-spend atomically?",
    "What is consistent hashing and where is it used in distributed systems?",
    "How do I design a rate limiter that works across multiple server instances?",
    "What is the Paxos algorithm and why is it hard to implement correctly?",
    "How do I design a search autocomplete system that responds in under 100ms?",
    "What is the difference between a message broker and a streaming platform?",
    "How do I design a URL shortener that handles billions of redirects per day?",
    "What is the two-phase commit protocol and when does it fail?",
    "How do I design a recommendation engine for a streaming platform?",
    "What is a bloom filter and where would I use one in a distributed system?",
    "How do I design a system that processes 1 million events per second?",
    "What is the LMAX disruptor pattern and how does it achieve low latency?",
    "How do I design a ride-sharing dispatch system that minimises wait time?",
    "What is a skip list and how does Redis use it for sorted sets?",
    "How do I design an e-commerce checkout system that is resilient to failures?",
    "What is the difference between optimistic and pessimistic locking?",
    "How do I design a collaborative text editor like Google Docs?",
    "What is the best data structure for an in-memory leaderboard update at 100k/s?",

    # ── Security extended ─────────────────────────────────────────────────────
    "How do I implement content security policy headers correctly in a Next.js app?",
    "What is a confused deputy attack and how do I prevent it in an API?",
    "How do I perform threat modelling for a new microservice before it ships?",
    "What is the difference between symmetric and asymmetric encryption?",
    "How do I detect and respond to a credential stuffing attack in real time?",
    "What is SSRF and how do I prevent it in a service that fetches URLs?",
    "How do I implement secure WebSocket communication in a web application?",
    "What is the difference between authentication and authorisation?",
    "How do I implement a secure file upload service that prevents malicious files?",
    "What is a timing attack and how do I write constant-time comparison functions?",

    # ── Data and MLOps extended ───────────────────────────────────────────────
    "How do I implement an online feature store for real-time ML inference?",
    "What is the best way to version ML models alongside their training data?",
    "How do I monitor for data drift in a production ML model?",
    "What is the difference between batch inference and online inference?",
    "How do I build a CI/CD pipeline for machine learning model retraining?",
    "What is Feast and how does it compare to Tecton for feature management?",
    "How do I implement reproducible ML experiments with DVC and MLflow?",
    "What is the best way to handle imbalanced classes in a classification problem?",
    "How do I implement A/B testing for two competing ML models in production?",
    "What is Spark structured streaming and how does it compare to Flink?",
    "How do I build a data lineage system to track where each metric comes from?",
    "What is the medallion architecture and when does it not work well?",
    "How do I implement SLOs for a machine learning model's prediction quality?",
    "What is shadow mode deployment for ML models and how do I set it up?",
    "How do I build a self-healing data pipeline that retries failed tasks?",

    # ── Science, maths, and logic ─────────────────────────────────────────────
    "Explain the Monty Hall problem and why the answer is counterintuitive.",
    "What is Bayes theorem and give me a real-world engineering example.",
    "Explain the travelling salesman problem and what makes it computationally hard.",
    "What is a Fourier transform and why does it matter for signal processing?",
    "Explain quantum entanglement in terms a software engineer would understand.",
    "What is the P vs NP problem and why does it matter for cryptography?",
    "How does GPS actually know your location to within a few metres?",
    "What is a Merkle tree and why does Bitcoin use one?",
    "Explain the halting problem and what it means for software in practice.",
    "What is the difference between precision and recall in machine learning?",
    "Explain why floating point numbers are imprecise and how to handle that in code.",
    "What is entropy in information theory and how is it related to compression?",
    "Explain the birthday paradox and its relevance to hash collisions.",
    "What is a Markov chain and where is it used in real systems?",
    "Explain gradient descent in simple terms without using calculus notation.",

    # ── Business, product, and career ─────────────────────────────────────────
    "How do I build a compelling business case for migrating to Kubernetes?",
    "What metrics should a CTO track to understand engineering team health?",
    "How do I estimate the ROI of investing in developer experience tooling?",
    "What is the difference between a product manager and a product owner?",
    "How do I run an effective quarterly planning process for an engineering team?",
    "What are the warning signs that a software project is about to miss its deadline?",
    "How do I structure a technical RFC document that people will actually read?",
    "What is the DORA metrics framework and how do I implement it?",
    "How do I build a culture of blameless post-mortems in an engineering org?",
    "What is Wardley mapping and how do I use it for technology strategy?",
    "How do I decide when to build versus buy a software component?",
    "What is the best way to communicate technical risk to a non-technical board?",
    "How do I set up an inner-source programme in a large engineering organisation?",
    "What is a platform team and how does it differ from a traditional ops team?",
    "How do I measure the productivity of a software engineering team fairly?",

    # ── Completely random ─────────────────────────────────────────────────────
    "What would happen if the internet went down for 24 hours globally?",
    "If programming languages were personalities, what would Python be like at a party?",
    "Explain the Turing test and whether a modern LLM could pass it.",
    "What is the most elegant algorithm you know of and why?",
    "If you had to delete one programming language from history, which one and why?",
    "Write a short poem about a developer debugging at 2am.",
    "What is the most important invention of the last 50 years for software engineers?",
    "If the cloud was a physical building, what would it look like?",
    "What would software engineering look like in 2050?",
    "Explain recursion using a story involving mirrors.",
    "What is the funniest variable name you have ever seen in production code?",
    "If databases had emotions, how would a production database feel on a Monday morning?",
    "Write a one-paragraph horror story for a software engineer.",
    "What is the biggest lie in software engineering?",
    "If you could add one feature to git, what would it be?",
    "Describe the perfect code review in under 100 words.",
    "What would a software engineer's CV look like in 2030?",
    "What is the software engineering equivalent of a chef's kiss?",
    "If bugs had names like hurricanes, what would the worst bug of 2024 be called?",
    "What is the most underrated skill a software engineer can have?",
    "Write a motivational poster slogan for a Kubernetes operations team.",
    "What would happen if every developer switched keyboards for a day?",
    "If Stack Overflow shut down tomorrow, what would happen to the industry?",
    "What is the software equivalent of a double rainbow?",
    "Describe a microservice using only food analogies.",
    "What is the most passive-aggressive comment you have ever seen in code?",
    "Write a job description for a Senior Kubernetes Pod.",
    "What would a stand-up meeting look like if the attendees were microservices?",
    "If you had to explain CI/CD to a five-year-old, how would you do it?",
    "What is the engineering equivalent of winning the lottery?",
    "Describe a distributed system using only sports metaphors.",
    "What would the world look like if version control had been invented in the 1800s?",
    "Write a Yelp review for the command line.",
    "What is the saddest line of code ever written?",
    "If you could only use three terminal commands for the rest of your life, which ones?",
    "What is the software engineering equivalent of a Swiss Army knife?",
    "Describe the feeling of deploying to production on a Friday using a weather metaphor.",
    "What would a software engineer order at a coffee shop if drinks were named after patterns?",
    "If technical debt were a physical object, what would it be?",
    "Write a two-sentence thriller about a missing semicolon.",
    "What is the best analogy for explaining eventual consistency to someone who has never coded?",
    "If open source were a country, what would its flag look like?",
    "What is the most romantic thing a software engineer could say using only technical jargon?",
    "Describe Agile methodology using a cooking show metaphor.",
    "What would a performance review look like for a load balancer?",
    "If you could name a Kubernetes namespace after a song, which song would fit best?",
    "What is the engineering equivalent of a messy kitchen?",
    "Write the plot of a blockbuster film where the villain is legacy code.",
    "What would a software engineer's horoscope say about deploying today?",
    "Describe the feeling of finally fixing a race condition using dance moves.",
    "What is the most unexpectedly philosophical thing about software engineering?",

    # ── Scenario-based / specific enough to defeat semantic caching ───────────
    "I have a Python service that processes 50,000 JSON events per minute from Kafka. It crashes every 4 hours. Where do I start debugging?",
    "My AKS pod starts fine but fails readiness checks after exactly 45 seconds. What are the five most likely causes?",
    "I need to migrate a 2TB PostgreSQL database to Azure with less than 30 seconds of downtime. Walk me through the steps.",
    "My Helm upgrade has been pending for 20 minutes. kubectl rollout status shows 0/3 replicas updated. What do I check first?",
    "I have three microservices: A calls B which calls C. C is timing out under load but B looks fine. How do I diagnose this?",
    "My Docker image is 4.2GB and takes 8 minutes to pull on a cold node. What are the five best ways to reduce it?",
    "I need to implement authentication for a REST API that will be used by both mobile apps and server-to-server calls. What should I use?",
    "My Redis cache hit rate dropped from 94% to 61% overnight and nothing was deployed. What could cause this?",
    "I have a FastAPI endpoint that takes 800ms on average but occasionally spikes to 12 seconds. How do I find why?",
    "My Kubernetes HPA is scaling pods up every 5 minutes then scaling them back down. How do I stop this thrashing?",
    "I need to run a database migration that adds a NOT NULL column to a table with 500 million rows in production. How?",
    "My Python service uses 2.1GB RAM at idle and I cannot figure out why. It has no data loaded at startup. How do I investigate?",
    "I need to implement a job queue that survives a complete pod restart without losing jobs. What are my options on Azure?",
    "My CI pipeline takes 47 minutes. The longest step is running 1200 unit tests. How do I get this under 10 minutes?",
    "I have a Node.js API that returns 200 but occasionally returns an empty body instead of JSON. How do I track this down?",
    "My Prometheus alerts are firing for high memory but kubectl top shows pods are fine. What is going wrong?",
    "I need to give a contractor read-only access to specific Kubernetes namespaces without giving cluster-admin. How?",
    "My Next.js application builds fine locally but fails in Docker with a different error every time. What is the pattern here?",
    "I have a gRPC service that works fine with one client but deadlocks with two concurrent clients. What should I check?",
    "My Azure Blob Storage reads are taking 800ms in production but under 50ms in staging. They use the same region. Why?",
    "I need to implement idempotency for a payment API that gets called by a third-party webhook with no retry tracking. How?",
    "My Kubernetes cluster has 47 nodes but one node always gets 80% of the pods scheduled on it. What is wrong?",
    "I have a Python script that imports a module and takes 8 seconds to start. The module has no circular imports. What is slow?",
    "My HTTPS certificate expired in production and I cannot rotate it without restarting the ingress. What are my options?",
    "I need to run a long-running background task in a FastAPI application without blocking the event loop. How?",

    # ── Extra diverse fill ─────────────────────────────────────────────────────
    "What caused the Bronze Age Collapse around 1200 BC and could it happen to modern civilisation?",
    "Explain the Fermi paradox and which proposed solution you find most plausible.",
    "What is the most underrated programming language that deserves more adoption?",
    "How does a compiler turn source code into machine code, at a high level?",
    "What is the Doppler effect and how does it help astronomers detect exoplanets?",
    "Explain how JPEG compression works without mentioning discrete cosine transforms.",
    "What is the prisoner's dilemma and what does it reveal about cooperation?",
    "How does a garbage collector decide when to free memory in a managed runtime?",
    "Explain the concept of technical debt using a mortgage analogy.",
    "What is the difference between a compiler and an interpreter?",
    "How does Diffie-Hellman key exchange allow two parties to share a secret over a public channel?",
    "What is Conway's Law and how does it affect software architecture decisions?",
    "Explain the Lindy effect and how it applies to choosing technology stacks.",
    "What is the most important thing a junior engineer can do in their first 90 days?",
    "How does a neural network learn and what is backpropagation in plain English?",
    "What is the difference between correlation and causation and why does it matter in data analysis?",
    "Explain the Byzantine Generals Problem and how blockchain claims to solve it.",
    "What is the most common mistake engineers make when reading a flame graph?",
    "How does TCP congestion control work at a basic level?",
    "What is the dark forest hypothesis and how does it relate to cybersecurity?",
    "Explain the observer effect in physics and its equivalent in software testing.",
    "What is the most dangerous assumption a software engineer can make about time zones?",
    "How does Unicode handle emoji and why do some emoji take more bytes than others?",
    "What is the Dunning-Kruger effect and how does it manifest in software teams?",
    "Explain the difference between latency and throughput using a highway metaphor.",
    "What is the most common cause of production outages that post-mortems rarely mention?",
    "How do content delivery networks decide which edge server to route a user to?",
    "What is the strangest edge case you can think of for sorting a list of strings?",
    "Explain the principle of least astonishment and give an example of violating it.",
    "What is the best way to explain the value of automated testing to a sceptical CEO?",
    "How does WebAssembly differ from JavaScript and what problems does it solve?",
    "What is the most important lesson from the history of software project failures?",
    "Explain the thundering herd problem and three ways to prevent it.",
    "What is the most important question to ask before starting any software project?",
    "How does the Linux kernel handle scheduling across multiple CPU cores?",
    "What is the most expensive one-line bug in software history?",
    "Explain the concept of eventual consistency using a social media example.",
    "What is the difference between horizontal and vertical scaling and when does each fail?",
    "How does a search engine index the web and rank results in under a second?",
    "What is the most important thing that separates a good on-call engineer from a great one?",
    "Explain the concept of zero-trust networking in plain English.",
    "What is the most common misconception engineers have about caching?",
    "How does a database index actually speed up a query and when does it slow one down?",
    "What is the most important soft skill for a senior software engineer?",
    "Explain the difference between a process and a thread using a restaurant analogy.",
    "What is the most counterintuitive thing about distributed systems?",
    "How does a load balancer decide where to send each incoming request?",
    "What is the most important thing to document about a system that is often left undocumented?",
    "Explain the concept of immutable infrastructure and why it improves reliability.",
    "What is the most common way that microservices architectures go wrong in practice?",
    "How does a VPN work at the network packet level?",
    "What is the most important metric to watch during a production deployment?",
    "Explain the concept of back-pressure in data stream processing.",
    "What is the most overlooked aspect of API design?",
    "How does a container differ from a virtual machine at the kernel level?",
    "What is the most important thing a team can do to reduce mean time to recovery?",
    "Explain the concept of a write-ahead log and why databases use it.",
    "What is the most common source of latency variance in production systems?",
    "How does a service mesh like Istio provide observability without code changes?",
    "What is the most important lesson from the history of open-source software?",
    "Explain the concept of chaos engineering and what makes a good chaos experiment.",
    "What is the most dangerous anti-pattern in asynchronous programming?",
    "How does a modern browser render a webpage from HTML to pixels?",
    "What is the most important thing to get right when designing a REST API?",
]

# ── Pass/fail thresholds (used in final summary + dashboard) ──────────────────
THRESHOLDS = {
    "error_rate_pct":             5.0,    # overall error rate < 5%
    "p95_latency_ms":        300_000,     # 5 min — normal for agent_network_designer (5-10 LLM calls)
    "list_success_rate_pct":     97.0,    # 97% of /list calls must succeed
    "connectivity_success_pct":  97.0,    # 97% of /connectivity calls must succeed
    "chat_success_rate_pct":     97.0,    # 97% of chat calls must succeed
    "max_429_count":              10,     # fewer than 10 rate-limit hits total
    "min_rps_efficiency_pct":    75.0,    # RPS must be ≥ 75% of active VU count
    "max_token_quota_pct":       80.0,    # warn when 80% of 10M quota consumed
}

# ── Hackathon-realistic prompts for agent_network_designer ────────────────────
# These are sent by SessionUser/PowerUser. All are brutal enterprise design requests:
# - Each explicitly names 10-15 agents the system must generate HOCON for
# - Each includes specific external tool integrations (SAP, Salesforce, ServiceNow, etc.)
# - Each includes regulatory compliance layers (GDPR, HIPAA, SOX, EU AI Act, PCI-DSS)
# - Each includes real-time + batch dual-mode processing requirements
# - Each includes multi-tier approval workflows, SLA thresholds, and fallback agents
# Designed to maximise internal LLM call chains (5-10 sub-agent calls per design)
# and defeat semantic caching through lexical diversity across all 50 prompts.
HACKATHON_DESIGN_PROMPTS = [
    # ── Financial Services ────────────────────────────────────────────────────
    (
        "Design a 14-agent network for real-time trade surveillance at a Tier-1 investment bank. "
        "Include: a market-data-ingestion agent pulling from Bloomberg B-PIPE and Refinitiv Elektron, "
        "a pattern-detection agent running spoofing, layering, and wash-trade algorithms, "
        "a false-positive-filter agent using historical execution data, "
        "a regulatory-report-generator agent writing MiFID II and Dodd-Frank alerts to XML, "
        "a case-management agent creating JIRA tickets with full audit trail, "
        "a trader-communication-analyser agent scanning Bloomberg Chat and email via Microsoft Graph API, "
        "a risk-score-aggregator agent combining market risk, credit risk, and operational risk, "
        "a sanctions-screening agent querying OFAC SDN and EU Consolidated List in real time, "
        "a senior-alert-escalation agent triggering PagerDuty P1 when score exceeds 85, "
        "a compliance-dashboard agent pushing KPIs to Tableau via REST, "
        "a model-explainability agent generating SHAP values for every alert, "
        "a data-lineage-tracker agent writing provenance to Apache Atlas, "
        "a cross-asset-correlation agent across equities, FX, and derivatives, "
        "and a regulatory-change-monitor agent watching EUR-Lex and SEC EDGAR for new rules. "
        "All decisions must be logged to an immutable audit ledger. GDPR, MiFID II, and SOX compliance mandatory."
    ),
    (
        "Build a 12-agent autonomous loan-origination network for a retail bank that handles "
        "applications from digital, branch, and broker channels simultaneously. "
        "Include: a document-ingestion agent parsing PDFs and images via Azure Form Recognizer, "
        "a bureau-data-fetcher agent pulling Experian, TransUnion, and Equifax scores via REST, "
        "a fraud-detection agent querying LexisNexis ThreatMetrix and running device-fingerprint checks, "
        "a debt-to-income-calculator agent pulling bank-statement data via Plaid API, "
        "a collateral-valuation agent integrating with Zillow AVM and CoreLogic property data, "
        "a regulatory-eligibility agent enforcing CFPB ATR rules and state-specific usury limits, "
        "an adverse-action-notice agent generating ECOA-compliant declination letters in 12 languages, "
        "a pricing-engine agent pulling live SOFR rates from the CME FedWatch API, "
        "a loan-committee-workflow agent routing to three tiers of human approvers via ServiceNow, "
        "a covenant-monitoring agent checking post-origination triggers weekly, "
        "an AML-transaction-profiler agent flagging structuring and rapid-drawdown patterns, "
        "and a portfolio-concentration-alert agent comparing new originations against Basel III limits. "
        "End-to-end SLA: 4 minutes from application submission to conditional approval. FCRA, ECOA, and CRA compliance."
    ),
    (
        "Create a 13-agent network for real-time anti-money-laundering across retail, corporate, and correspondent banking. "
        "Include: a transaction-stream-consumer agent reading from Apache Kafka at 50,000 TPS, "
        "a customer-risk-profiler agent integrating KYC data from Salesforce Financial Services Cloud, "
        "a graph-network-analyser agent running Neo4j Cypher queries to find shell-company rings, "
        "a behaviour-deviation-detector agent comparing transactions against 90-day rolling baselines, "
        "a cross-border-payment-screener agent against SWIFT gpi data and correspondent bank BICs, "
        "a sanctions-and-PEP-checker agent querying WorldCheck ONE and Dow Jones Risk & Compliance, "
        "a cash-structuring-detector agent flagging CTR smurfing under $10,000 thresholds, "
        "a suspicious-activity-report agent writing FINCEN SAR XML and filing via BSA E-Filing, "
        "a case-lifecycle-manager agent orchestrating investigator assignments in Actimize, "
        "a false-positive-retrainer agent feeding confirmed non-SARs back to the ML model, "
        "a de-risking-impact-assessor agent modelling revenue loss before account exit decisions, "
        "a regulatory-exam-readiness agent generating FFIEC BSA/AML exam work papers, "
        "and a cross-institution-typology-sharer agent publishing anonymised red flags via FinCEN 314b API. "
        "GDPR, CCPA, FATF Recommendation 16, and EU 6AMLD compliance. False-positive rate must stay below 12%."
    ),
    (
        "Design a 15-agent autonomous treasury-management network for a Fortune 500 multinational. "
        "Include: a cash-position-aggregator agent polling 47 bank accounts via SWIFT MT940 and BAI2, "
        "a liquidity-forecaster agent running Monte Carlo simulation on 90-day cash flows, "
        "a FX-exposure-calculator agent netting transactional, translational, and economic exposures, "
        "a hedge-recommendation-engine agent generating vanilla forward and option strategies, "
        "a bank-counterparty-risk-monitor agent pulling ISDA CDS spreads from Markit, "
        "an intercompany-loan-scheduler agent optimising netting cycles across 23 legal entities, "
        "a short-term-investment-allocator agent placing overnight deposits on Bloomberg TSOX, "
        "a payment-factory-orchestrator agent routing SEPA, ACH, and CHAPS instructions, "
        "a bank-fee-analyser agent comparing charges against AFP benchmarks, "
        "a covenant-compliance-tracker agent checking RCF and bilateral loan triggers quarterly, "
        "a transfer-pricing-documentation agent generating OECD-compliant master and local files, "
        "a payments-fraud-screener agent running positive-pay and payee-verification rules, "
        "a regulatory-reporting-compiler agent producing LCR, NSFR, and CRR III returns, "
        "a ESG-cash-allocation-advisor agent aligning short-term holdings with SBTi targets, "
        "and a board-treasury-dashboard agent delivering weekly CFO packs to Power BI. "
        "IFRS 9, ASC 815, SOX 302/404, and EMIR compliance. All agent decisions versioned in Git-like audit log."
    ),
    # ── Healthcare & Life Sciences ─────────────────────────────────────────────
    (
        "Design a 14-agent network for end-to-end oncology clinical-trial management at a Tier-1 CRO. "
        "Include: a protocol-deviation-detector agent reading eCRF data from Medidata Rave via REST, "
        "a patient-eligibility-screener agent applying 47 inclusion/exclusion criteria in real time, "
        "a safety-signal-monitor agent pulling AE and SAE reports and running Bayesian signal detection, "
        "a regulatory-submission-compiler agent generating ICH E3 narrative and eCTD Module 5 tables, "
        "a site-performance-benchmarker agent comparing enrolment rates across 34 investigator sites, "
        "a drug-supply-chain-planner agent forecasting randomisation and kit-resupply needs, "
        "a informed-consent-tracker agent verifying re-consent when protocol amendments occur, "
        "a blinding-integrity-monitor agent alerting on unblinding events via IxRS integration, "
        "a real-world-evidence-linker agent matching trial patients to CMS claims via tokenised ID, "
        "a biomarker-analysis-pipeline agent processing ctDNA NGS files from Illumina DRAGEN, "
        "a DSMB-report-generator agent compiling interim analysis dossiers for data safety monitoring, "
        "a study-master-file-keeper agent populating TMF references in Veeva Vault, "
        "a competitive-landscape-watcher agent parsing ClinicalTrials.gov RSS for rival studies, "
        "and a budget-burn-rate-analyser agent comparing actuals against CTMS milestones. "
        "HIPAA, 21 CFR Part 11, ICH E6 GCP, and EU AI Act Article 6 high-risk classification compliance."
    ),
    (
        "Build a 12-agent autonomous radiology-report-triage and peer-review network for a hospital network of 8 sites. "
        "Include: a DICOM-study-ingestion agent pulling from 4 PACS systems via DICOMweb, "
        "a modality-classifier agent routing CT, MRI, PET, and X-ray studies to specialist sub-queues, "
        "a AI-finding-extractor agent running inference on chest X-rays using a CheXNet-class model via ONNX Runtime, "
        "a critical-finding-alerter agent paging radiologists via Vocera badge API for urgent findings within 5 minutes, "
        "a report-draft-generator agent producing structured HL7 FHIR R4 DiagnosticReport resources, "
        "a prior-study-comparator agent retrieving and comparing historical studies from Nuance PowerScribe, "
        "a peer-review-scheduler agent randomly assigning completed reports for double-read against ACR RADPEER criteria, "
        "a discrepancy-tracker agent logging major/minor disagreements and triggering CME credits in HealthStream, "
        "a dose-registry-reporter agent submitting exam dose data to ACR Dose Index Registry, "
        "a billing-code-suggester agent mapping findings to CPT and ICD-10 codes via 3M Encoder, "
        "a turnaround-time-dashboard agent feeding SLA KPIs to a Tableau real-time board, "
        "and a fail-safe-escalation agent routing unread stat studies to on-call teleradiology via NightHawk API. "
        "HIPAA, ACR practice guidelines, Joint Commission standards, and ONC USCDI compliance."
    ),
    (
        "Create a 13-agent pharmaceutical-supply-chain visibility network to prevent drug shortages and counterfeiting. "
        "Include: a serialisation-data-consumer agent reading GS1 EPCIS events from DSCSA Track-and-Trace systems, "
        "a temperature-excursion-detector agent processing IoT sensor streams from Sensitech ColdStream loggers, "
        "a demand-signal-aggregator agent pulling wholesaler POS data from IQVIA MIDAS, "
        "a shortage-predictor agent combining manufacturer capacity data and demand forecasts, "
        "a counterfeit-pattern-analyser agent comparing product authentication codes against MEA blockchain ledger, "
        "a recall-execution-orchestrator agent triggering reverse logistics and patient-notification workflows, "
        "a regulatory-filing-agent compiling FDA Field Alert Reports and submitting via ESG gateway, "
        "a customs-clearance-coordinator agent processing 22 country import dossiers via Descartes GTM, "
        "a contract-manufacturer-scorecard agent monitoring deviation rates against CDMO SLAs, "
        "a inventory-rebalancing-engine agent reallocating buffer stock across 12 DCs using LP optimisation, "
        "a pharmacovigilance-signal-linker agent cross-referencing shortages to adverse event spikes in FDA FAERS, "
        "a ESG-supplier-risk-assessor agent scoring suppliers against EcoVadis and CDP climate data, "
        "and a executive-exception-dashboard agent compiling daily P&L impact of disruptions for the Supply Chain VP. "
        "DSCSA 2023, EU FMD, WHO PIC/S GMP, and ISO 28000 supply chain security compliance."
    ),
    # ── Energy & Utilities ─────────────────────────────────────────────────────
    (
        "Design a 15-agent autonomous energy-trading and grid-balancing network for a European TSO. "
        "Include: a day-ahead-market-bidder agent submitting offers to ENTSO-E EUPHEMIA via ENTSOG API, "
        "a intraday-continuous-trading-agent executing on EPEX SPOT Intraday Continuous via FIX protocol, "
        "a renewable-generation-forecaster agent running ensemble NWP models from ECMWF API, "
        "a demand-response-dispatcher agent sending DRMS signals to 2,000 industrial flexible-load customers, "
        "a frequency-containment-reserve-agent activating FCR within 30 seconds of frequency deviation, "
        "a congestion-management-agent computing redispatch instructions for 500 nodes using DC power-flow, "
        "a battery-BESS-scheduler agent optimising 400 MWh grid-scale battery cycling for arbitrage, "
        "a cross-border-capacity-allocator agent participating in JAO coordinated capacity calculation, "
        "a settlement-reconciliation-agent matching imbalance volumes against TSO settlement statements, "
        "a outage-planning-coordinator agent checking N-1 security for every maintenance window in SCADA, "
        "a carbon-price-exposure-tracker agent computing EUA sensitivity across the trading portfolio, "
        "a model-risk-validator agent back-testing price forecasts and escalating when MAPE exceeds 8%, "
        "a regulatory-transparency-reporter agent submitting REMIT transaction data to ACER ARIS, "
        "a market-manipulation-sentinel agent scanning for spoofing in EPEX order book via SMARTS feed, "
        "and a P&L-attribution-explainer agent decomposing daily trading results by strategy and asset class. "
        "REMIT, MiFID II, EU ETS Phase IV, GDPR, and ENTSO-E network codes compliance."
    ),
    (
        "Build a 13-agent predictive-maintenance network for 500 offshore wind turbines across 6 North Sea wind farms. "
        "Include: a SCADA-telemetry-ingestion agent processing 2,000 sensor streams per turbine via OPC-UA, "
        "a vibration-anomaly-detector agent running FFT and envelope analysis on gearbox accelerometers, "
        "a pitch-and-yaw-performance-analyser agent comparing actual vs theoretical power curves, "
        "a remaining-useful-life-estimator agent running physics-informed LSTM on bearing temperature trends, "
        "a maintenance-work-order-creator agent raising SAP PM orders with bill-of-materials attachments, "
        "a spare-parts-inventory-planner agent optimising consignment stock at 3 onshore logistics hubs, "
        "a weather-window-scheduler agent coordinating CTV and jack-up vessel availability from WaveScout API, "
        "a technician-competency-matcher agent checking offshore certification and right-to-work from Workday, "
        "a permit-to-work-orchestrator agent enforcing LOTO and simultaneous operations rules in Intelex QHSE, "
        "a drone-inspection-tasker agent dispatching autonomous Skyfront Perimeter drones for blade imagery, "
        "a blade-defect-classifier agent running YOLOv9 inference on 4K drone frames for leading-edge erosion, "
        "a contract-SLA-monitor agent tracking turbine availability against 95% contractual target with penalty calc, "
        "and a carbon-avoided-calculator agent reporting Scope 1 savings to CDP and GRI G4 standards. "
        "DNVGL-SE-0190, UK HSE offshore safety case, GDPR, ISO 55001 asset management compliance."
    ),
    # ── Manufacturing & Supply Chain ──────────────────────────────────────────
    (
        "Create a 14-agent autonomous manufacturing-execution network for a Tier-1 automotive stamping plant. "
        "Include: a production-order-receiver agent pulling SAP PP orders via RFC BAPI and BAPIs, "
        "a material-readiness-checker agent querying SAP WM bin locations and triggering forklift AGVs via VDA 5050, "
        "a machine-parameter-setter agent pushing optimised press tonnage, feed rate, and die-cushion to 40 Schuler presses via OPC-UA, "
        "a real-time-quality-inspector agent running 3D point-cloud comparison from Zeiss ATOS scanners, "
        "a statistical-process-control-agent raising Western Electric rule violations and computing Cpk in real time, "
        "a scrap-root-cause-analyser agent correlating defects to specific die, shift, and coil batch using DMAIC logic, "
        "a energy-consumption-optimiser agent scheduling high-draw equipment to avoid peak-demand tariff windows, "
        "a die-maintenance-scheduler agent triggering predictive re-sharpening based on hit-count thresholds, "
        "a customer-shipping-scheduler agent running ATP checks in SAP SD and booking carrier slots in Transplace TMS, "
        "a IATF-nonconformance-manager agent creating 8D reports and routing to quality engineers via SharePoint, "
        "a supplier-coil-quality-gatekeeper agent accepting or quarantining incoming steel against ASTM A656 specs, "
        "a CO2-footprint-tracker agent calculating Scope 2 emissions per part and updating GHG Protocol database, "
        "a shift-handover-report-generator agent compiling OEE, quality, and safety events in FactoryTalk, "
        "and a tooling-cost-per-unit-analyser agent alerting when marginal cost exceeds standard cost by 5%. "
        "IATF 16949, ISO 50001, EU CBAM, Odette EDI, and GDPR compliance."
    ),
    (
        "Design a 12-agent intelligent procurement network for a global consumer-goods company with 3,000 suppliers. "
        "Include: a spend-cube-builder agent extracting and normalising spend from SAP Ariba, Coupa, and legacy ERPs, "
        "a supplier-risk-profiler agent combining Dun & Bradstreet Viability Rating, EcoVadis, and news-sentiment signals, "
        "a demand-signal-translator agent converting S&OP rolling forecasts into supplier capacity-reservation requests, "
        "a rfx-document-generator agent producing 120-line RFQ packages with customised technical specifications, "
        "a bid-evaluation-scorer agent running multi-criteria decision analysis across price, quality, and risk dimensions, "
        "a contract-drafting-agent generating master supply agreements from a Clause Library in OpenText eDOCS, "
        "a savings-validation-agent computing clean-room price-variance analysis against should-cost models, "
        "a purchase-order-expeditor agent chasing overdue POs via automated WhatsApp and email with SAP MM write-back, "
        "a trade-finance-coordinator agent triggering LC amendments and standby letters via SWIFT MT700 messages, "
        "a tariff-and-duty-classifier agent running HTS code determination and duty-drawback analysis for 80 SKUs, "
        "a forced-labour-compliance-screener agent checking suppliers against UFLPA Entity List and ILO-OSH databases, "
        "and a procurement-analytics-narrator agent generating weekly natural-language commentary for the CPO in Power BI. "
        "GDPR, UK Modern Slavery Act, EU Supply Chain Due Diligence Directive, SOX, and ISO 20400 sustainable procurement."
    ),
    # ── Human Resources & Talent ──────────────────────────────────────────────
    (
        "Build a 13-agent end-to-end talent-acquisition network for a 50,000-employee enterprise. "
        "Include: a job-requisition-validator agent checking headcount budgets in Workday HCM and triggering approval workflows, "
        "a multi-channel-sourcer agent posting to LinkedIn Recruiter, Indeed, Glassdoor, and 6 niche job boards via API, "
        "a resume-parser-and-ranker agent extracting structured data via Azure AI Document Intelligence and scoring against JD, "
        "a bias-detector agent flagging gendered language and protected-class proxies in JDs and screening decisions, "
        "a technical-assessment-scheduler agent dispatching HackerRank challenges and scheduling Calendly interviews, "
        "a interview-guide-generator agent creating structured behavioural questions mapped to Lominger competencies, "
        "a background-check-orchestrator agent triggering Sterling IDCO and global sanction checks, "
        "a offer-letter-drafter agent personalising compensation proposals against Radford LTIP benchmark bands, "
        "a relocation-logistics-coordinator agent booking temporary housing and shipping via SIRVA MovePro, "
        "a onboarding-task-dispatcher agent provisioning 47 system accesses in ServiceNow and triggering buddy assignment, "
        "a DEI-pipeline-tracker agent computing funnel conversion rates by protected class for EEO-1 and UK Gender Pay Gap reporting, "
        "a regret-hire-predictor agent flagging 90-day attrition risk using Workday People Analytics signals, "
        "and a recruiter-productivity-benchmarker agent computing time-to-fill and cost-per-hire against SHRM benchmarks. "
        "GDPR, EEOC, OFCCP, EU Pay Transparency Directive, EU AI Act Article 6 high-risk HR system, and CCPA compliance."
    ),
    # ── IT Service Management ─────────────────────────────────────────────────
    (
        "Design a 14-agent autonomous IT incident-management and self-healing network for a financial-services firm. "
        "Include: a multi-source-alert-correlator agent consuming Splunk, Dynatrace, and PagerDuty webhooks and suppressing noise by 80%, "
        "a blast-radius-estimator agent querying CMDB in ServiceNow to map impacted CIs and downstream business services, "
        "a RCA-hypothesis-generator agent running causal inference across 200 telemetry dimensions using Prometheus data, "
        "a runbook-executor agent invoking Ansible playbooks and Terraform remediation scripts via AWX API, "
        "a change-collision-detector agent checking the proposed fix against RFC freeze windows in ServiceNow CAB board, "
        "a rollback-decision-arbiter agent computing blast radius of rollback vs forward fix within 2-minute SLA, "
        "a war-room-coordinator agent posting structured updates every 5 minutes to Slack and MS Teams channels, "
        "a customer-impact-communicator agent drafting personalised email and status-page updates via Statuspage.io API, "
        "a SRE-escalation-router agent matching on-call engineers to incidents using PagerDuty schedule API, "
        "a post-incident-review-drafter agent generating DORA-compliant RCA documents in Confluence, "
        "a MTTR-regression-detector agent comparing current incident to closest K historical incidents, "
        "a compliance-evidence-packager agent collecting artefacts for SOC 2 Type II and ISO 27001 audit log, "
        "a SLO-burn-rate-alerter agent computing 5-minute and 1-hour error-budget burn rates per service, "
        "and a capacity-surge-provisioner agent scaling Azure AKS node pools when CPU/memory thresholds breach. "
        "ISO 27001, SOC 2 Type II, ITIL 4, PCI-DSS Requirement 12.10, and DORA compliance."
    ),
    (
        "Create a 12-agent zero-trust security operations centre network for a defence contractor. "
        "Include: a SIEM-event-normaliser agent ingesting CrowdStrike Falcon, Palo Alto Cortex XDR, and Azure Sentinel feeds, "
        "a threat-intelligence-enricher agent querying MISP, VirusTotal, and Recorded Future via TAXII and REST, "
        "a kill-chain-stage-classifier agent mapping IOCs to MITRE ATT&CK tactics and techniques, "
        "a asset-criticality-scorer agent weighting incidents against CMDB data and NIST CVSS scores, "
        "a automated-triage-and-contain agent isolating hosts via CrowdStrike Real Time Response and blocking IPs in Panorama, "
        "a digital-forensics-collector agent imaging volatile memory using Velociraptor and preserving evidence chain of custody, "
        "a malware-sandbox-submitter agent detonating suspicious binaries in ANY.RUN and Cuckoo, "
        "a threat-hunt-query-generator agent producing KQL, SPL, and Sigma rules from hypothesis, "
        "a vulnerability-prioritiser agent correlating CVEs to live asset exposure using Tenable and Qualys data, "
        "a executive-threat-briefing-agent compiling daily TLP:WHITE and TLP:AMBER reports for CISO and Board, "
        "a regulatory-incident-notification-agent drafting GDPR 72-hour breach notifications and US CISA reporting, "
        "and a red-team-simulation-scheduler agent automating CALDERA adversary emulation and measuring detection coverage. "
        "NIST CSF 2.0, ISO 27001, CMMC Level 3, GDPR Article 33, EU NIS2, and ITAR/EAR compliance."
    ),
    # ── Retail & E-commerce ───────────────────────────────────────────────────
    (
        "Build a 13-agent hyper-personalisation and dynamic-pricing network for a global e-commerce retailer with 50M SKUs. "
        "Include: a real-time-event-stream-consumer agent reading clickstream from Apache Kafka at 200,000 events/second, "
        "a customer-intent-classifier agent running transformer inference to distinguish browse, compare, and buy intent, "
        "a collaborative-filter-recommender agent querying a real-time feature store in Redis for user-item embeddings, "
        "a price-elasticity-estimator agent computing demand curves per SKU-region-segment triplet using causal ML, "
        "a competitor-price-monitor agent scraping 15 rival websites and Bazaarvoice API every 15 minutes, "
        "a markdown-optimiser agent running LP to clear aged inventory without cannibalising full-price sales, "
        "a promotion-eligibility-checker agent applying 40 business rules (margin floor, MAP policy, brand exclusions), "
        "a cart-abandonment-recovery-agent triggering personalised push, email, and retargeting within 3 minutes, "
        "a fraud-velocity-screener agent checking payment against MaxMind GeoIP and Kount device intelligence, "
        "a inventory-reservation-coordinator agent locking ATP in OMS (Manhattan Active) for 15 minutes post-add-to-cart, "
        "a last-mile-ETA-calculator agent querying Project44 and FedEx Delivery Manager for real-time delivery windows, "
        "a returns-propensity-predictor agent scoring likelihood of return and adjusting acceptance policy dynamically, "
        "and a personalisation-fairness-auditor agent testing recommendations for filter-bubble and demographic-parity violations. "
        "GDPR, CCPA, EU AI Act Recital 69 recommender systems, PCI-DSS SAQ A-EP, and EU Consumer Rights Directive compliance."
    ),
    (
        "Design a 12-agent omnichannel inventory-optimisation network for a fashion retailer with 800 stores and 3 DCs. "
        "Include: a demand-sensing-agent fusing POS, weather, social-trend, and Google Trends signals into a 14-day forecast, "
        "a allocation-engine-agent running newsvendor-model LP optimisation across store clusters and size curves, "
        "a inter-store-transfer-recommender agent identifying stock imbalances and generating replenishment proposals, "
        "a replenishment-trigger-agent pushing automatic orders to SAP EWM when safety-stock trips are breached, "
        "a supplier-lead-time-tracker agent querying vendor portals and ASN EDI feeds to update dynamic lead times, "
        "a markdown-calendar-builder agent recommending week-by-week discount cadence to clear end-of-season inventory, "
        "a size-run-integrity-checker agent flagging broken size-sets in real time from RFID rack sensors, "
        "a shrinkage-anomaly-detector agent identifying stores with unexplained inventory loss above 1.5%, "
        "a planogram-compliance-auditor agent comparing shelf images from Trax Retail to approved planograms, "
        "a clearance-channel-selector agent routing excess stock between outlets, wholesale, and liquidation partners, "
        "a carbon-footprint-per-garment-tracker agent computing Scope 3 transport emissions per store replenishment run, "
        "and a buying-performance-scorecard-agent benchmarking buyers on sell-through, margin, and forecast accuracy. "
        "GDPR, EU Textile Labelling Regulation, UK PAYE for retail staff scheduling, and ISO 14064-3 carbon reporting."
    ),
    # ── Government & Public Sector ─────────────────────────────────────────────
    (
        "Create a 14-agent autonomous border-control and customs-clearance network for an international airport handling 50M passengers annually. "
        "Include: a advance-passenger-information-consumer agent processing APIS data from 300 airlines via EDIFACT, "
        "a biometric-identity-verifier agent comparing live facial scans against Interpol FIND and national passport databases, "
        "a visa-and-ETA-eligibility-checker agent querying 190 bilateral treaty rules and ETIAS in real time, "
        "a watch-list-screener agent checking against UN consolidated list, OFAC SDN, and Europol EIS within 2 seconds, "
        "a document-fraud-detector agent running UV/IR authentication data through ML forgery classifiers, "
        "a risk-profiling-agent computing a behavioural risk score combining travel history, booking patterns, and declaration data, "
        "a customs-duty-calculator agent applying WTO tariff schedules and FTA preferential rates to declared goods, "
        "a prohibited-goods-alerter agent correlating baggage X-ray tensor data with manifest declarations, "
        "a secondary-inspection-tasker agent routing high-risk travellers to automated e-gate or officer lanes with briefing, "
        "a cross-border-health-certificate-validator agent checking ICAO IVAC vaccination records, "
        "a GDPR-data-minimisation-enforcer agent deleting non-retained PII within statutory deletion schedules, "
        "a traveller-experience-dashboard-agent tracking queue wait times and publishing to airport digital signage API, "
        "a inter-agency-intelligence-sharer agent pushing derogatory travel patterns to FRONTEX EUROSUR, "
        "and a oversight-audit-trail-agent generating immutable logs for every automated decision under EU AI Act Article 14 human oversight. "
        "EU AI Act high-risk Annex III, Schengen SIS II, GDPR, UN Palermo Protocol, and ICAO Doc 9944 API standards."
    ),
    (
        "Build a 13-agent smart-city traffic and emergency-services coordination network for a city of 2 million. "
        "Include: a multi-modal-traffic-flow-aggregator agent fusing data from 1,200 loop detectors, 400 Bluetooth scanners, and Waze Live SDK, "
        "a adaptive-signal-control-agent running SCOOT/SCATS algorithms and pushing phase plans to Siemens Sitraffic via NTCIP, "
        "a incident-detection-agent identifying accidents using camera AI from Axis Communications and ANPR feeds, "
        "a emergency-vehicle-priority-agent pre-empting signals for ambulances, fire and police via CCTV-based detection, "
        "a public-transport-interface-agent adjusting bus stop dwell time recommendations in real time via GTFS-RT, "
        "a air-quality-routing-agent computing low-NOx alternative routes using DEFRA Automatic Urban and Rural Network API, "
        "a congestion-charge-enforcement-agent reading ANPR data, applying tariff rules, and generating penalty notices, "
        "a school-zone-speed-enforcement-agent activating variable speed limits on TfL street manager API during school hours, "
        "a street-works-conflict-detector agent checking permit applications against upcoming events in LoST geofencing, "
        "a major-event-traffic-modeller agent running VISSIM microsimulation for stadium events and publishing diversion routes, "
        "a vulnerable-road-user-protector agent using V2X C-ITS messages to warn cyclists and pedestrians via mobile app, "
        "a infrastructure-fault-reporter agent detecting pothole and signal outages from IoT sensors and raising FixMyStreet tickets, "
        "and a transport-equity-analyser agent checking whether optimisation disproportionately disadvantages deprived wards. "
        "GDPR, EU AI Act, UK Public Sector Equality Duty, NTCIP standards, and ISO 37120 sustainable cities compliance."
    ),
    # ── Telecommunications ────────────────────────────────────────────────────
    (
        "Design a 14-agent autonomous 5G network-operations network for a Tier-1 MNO managing 80,000 sites. "
        "Include: a performance-KPI-collector agent streaming PMC counters from Ericsson ENM and Nokia NetAct via NETCONF, "
        "a anomaly-detection-agent running LSTM-Autoencoder on RRC, PDCP, and PDSCH KPI streams, "
        "a root-cause-localiser agent using topology-aware graph attention to isolate faulty cells, "
        "a self-healing-parameter-tuner agent pushing antenna tilts and TX power via Ericsson CM API, "
        "a traffic-load-balancer agent performing inter-frequency and inter-RAT handover optimisation, "
        "a capacity-demand-forecaster agent predicting PRB utilisation by cell using 12-week rolling ARIMA, "
        "a planned-maintenance-conflict-checker agent validating field-engineer site access against O-RAN RIC SLA windows, "
        "a massive-MIMO-beam-manager agent retuning massive-MIMO beam patterns from drive test feedback, "
        "a energy-saving-policy-enforcer agent applying DTX/DRX sleep policies during low-traffic hours per 3GPP TS 38.300, "
        "a field-force-dispatcher agent routing closest certified engineer with right test equipment via ServiceMax, "
        "a spectrum-interference-detector agent identifying PIM and external interferers using iBwave spectrum analyser data, "
        "a regulatory-EMF-compliance-reporter agent computing ICNIRP reference levels for all active sites and filing with Ofcom, "
        "a network-slice-SLA-assurer agent monitoring per-slice throughput and latency for enterprise MVNO contracts, "
        "and a sustainability-kWh-tracker agent computing network energy efficiency per bit and reporting to GSMA Connected World. "
        "3GPP SA5, GDPR, EU Radio Equipment Directive, Ofcom spectrum licensing, and GSMA Net Zero by 2040 compliance."
    ),
    # ── Insurance ─────────────────────────────────────────────────────────────
    (
        "Create a 13-agent intelligent claims-processing and fraud-detection network for a P&C insurer. "
        "Include: a first-notice-of-loss-parser agent extracting structured data from phone transcripts via Azure Speech-to-Text, "
        "a policy-coverage-eligibility-verifier agent querying Duck Creek Policy Administration via REST, "
        "a multi-source-document-collector agent requesting and parsing police reports, medical records, and repair estimates, "
        "a fraud-indicator-scorer agent running network-analysis on claimant relationship graph in Neo4j, "
        "a comparable-sales-valuer agent pulling Kelley Blue Book and Copart auction data for total-loss vehicles, "
        "a medical-bill-repricing-agent applying state fee schedules and PPO network rates from Zelis, "
        "a litigation-risk-assessor agent predicting representation probability from 200 claim features, "
        "a reserve-adequacy-auditor agent comparing case reserves against actuarial ultimates from Guidewire ClaimCenter, "
        "a subrogation-opportunity-identifier agent detecting third-party liability and filing SDN demand letters, "
        "a customer-communication-agent drafting personalised status updates and settlement offers in 8 languages, "
        "a reinsurance-bordereau-compiler agent aggregating ceded losses per treaty layer in Sequel Impact, "
        "a regulatory-complaint-tracker agent logging BBB, DOI, and CFPB complaints and calculating response SLAs, "
        "and a climate-catastrophe-exposure-aggregator agent estimating PML shifts after wildfire and flood events using RMS. "
        "NAIC Model Laws, Solvency II, GDPR, CCPA, EU AI Act explainability requirements, and state unfair-claims-practices acts."
    ),
    # ── Real Estate & Construction ─────────────────────────────────────────────
    (
        "Build a 12-agent AI-driven commercial-real-estate investment and asset-management network. "
        "Include: a market-signal-aggregator agent fusing CoStar vacancy data, Trepp CMBS spreads, and CBRE cap-rate surveys, "
        "a due-diligence-coordinator agent orchestrating legal, environmental, and structural report collection from vendors, "
        "a DCF-valuation-engine agent building 10-year unlevered and levered cash-flow models in real time, "
        "a tenant-creditworthiness-analyser agent pulling Dun & Bradstreet financials and lease-abstract data, "
        "a lease-expiry-risk-profiler agent mapping rollover exposure across a 120-property portfolio by quarter, "
        "a sustainability-grader agent computing GRESB, BREEAM, and ENERGY STAR scores per asset, "
        "a capital-expenditure-planner agent prioritising roof, HVAC, and façade works by remaining useful life and ROI, "
        "a rent-optimiser agent benchmarking asking rents against comparable transactions and generating negotiation ranges, "
        "a REIT-compliance-monitor agent verifying 75% income and asset tests under IRC Section 856 quarterly, "
        "a ESG-reporting-compiler agent generating TCFD, SFDR Article 8, and GRI 302 disclosures automatically, "
        "a lender-covenant-surveillance-agent checking DSCR, LTV, and occupancy covenants monthly and alerting on breach, "
        "and a property-tax-appeal-identifier agent comparing assessed values against comparable sales for 15 municipalities. "
        "GDPR, EU Taxonomy Regulation, SFDR, MIFID II (fund distribution), Sarbanes-Oxley, and local building codes."
    ),
    # ── Logistics & Transportation ─────────────────────────────────────────────
    (
        "Design a 15-agent autonomous freight-forwarding and customs-brokerage network handling 5,000 shipments daily. "
        "Include: a booking-intake-agent parsing shipper requests from Cargowise ONE and email via Microsoft Power Automate, "
        "a carrier-rate-shopper agent querying 40 ocean, air, and road carrier APIs simultaneously for spot rates, "
        "a space-and-equipment-allocator agent matching cargo to vessel rotations in inttra's booking platform, "
        "a HS-code-classifier agent applying EU Combined Nomenclature to 3,000 SKUs using Descartes CustomsInfo, "
        "a export-licence-screener agent checking EAR, ITAR, and EU Dual-Use against ECCN database, "
        "a import-duty-calculator agent computing CIF value, applicable MFN rate, and anti-dumping duty, "
        "a document-generator agent producing commercial invoice, packing list, certificate of origin, and EUR.1 in 22 languages, "
        "a customs-entry-filer agent submitting AES and ACE entries via ASC X12 EDI 301/303 transactions, "
        "a dangerous-goods-compliance-checker agent applying IATA DGR and IMDG regulations to hazmat cargo, "
        "a track-and-trace-enricher agent fusing ocean AIS vessel data from MarineTraffic with carrier milestones, "
        "a demurrage-and-detention-calculator agent computing D&D exposure and submitting disputes to carriers, "
        "a carbon-emissions-calculator agent applying GLEC Framework to compute multimodal carbon intensity per TEU, "
        "a trade-finance-documentary-credit-agent preparing LC-compliant document packages per UCP 600, "
        "a regulatory-change-watch-agent monitoring WCO, WTO, and national customs authority RSS feeds for tariff changes, "
        "and a shipper-experience-scorecard-agent computing NPS and OTIF per lane for quarterly business reviews. "
        "WCO SAFE Framework, EU ADR, SOLAS VGM, C-TPAT, AEO, GDPR, and ISO 28000 compliance."
    ),
    # ── Education ─────────────────────────────────────────────────────────────
    (
        "Create a 12-agent adaptive-learning and student-success network for a higher-education institution with 40,000 students. "
        "Include: a learning-record-store-consumer agent reading xAPI statements from Canvas LMS and Coursera for Campus, "
        "a knowledge-gap-identifier agent running mastery-learning inference across 600 concept nodes per curriculum, "
        "a next-best-activity-recommender agent personalising study sequences using reinforcement learning, "
        "a early-alert-detector agent flagging students at risk of withdrawal using 25 engagement and academic signals, "
        "a advisor-caseload-prioritiser agent routing at-risk students to academic advisors via Salesforce Education Cloud, "
        "a disability-accommodation-checker agent verifying DSP accommodations before exam scheduling in Accommodate, "
        "a plagiarism-and-AI-authorship-detector agent submitting submissions to Turnitin Similarity and GPTZero, "
        "a peer-tutor-matching-agent pairing struggling students with peer tutors by schedule and subject from WCOnline, "
        "a scholarship-eligibility-screener agent checking academic, financial, and demographic criteria across 300 funds, "
        "a degree-audit-and-what-if-agent computing time-to-graduation and recommending course substitutions in DegreeWorks, "
        "a experiential-learning-credit-evaluator agent mapping workplace competencies to course learning outcomes, "
        "and a institutional-research-report-generator agent compiling IPEDS and NSC Clearinghouse submissions automatically. "
        "FERPA, GDPR, UK GDPR for HE sector, EU AI Act high-risk education use cases, ADA/Section 508, and QAA compliance."
    ),
    # ── Media & Entertainment ─────────────────────────────────────────────────
    (
        "Build a 13-agent AI-powered content moderation and rights-management network for a global streaming platform with 200M subscribers. "
        "Include: a ingest-pipeline-agent transcoding and fingerprinting every new asset via AWS Elemental MediaConvert, "
        "a audio-visual-content-classifier agent running multi-label inference for nudity, violence, hate speech, and CSAM, "
        "a digital-rights-management-verifier agent checking EIDR registrations and ISRC codes against rights database, "
        "a geo-rights-enforcer agent blocking content by territory using MaxMind GeoIP2 and contract rights windows, "
        "a UGC-DMCA-takedown-processor agent evaluating counter-notices and scheduling reinstatement per 512(g) timeline, "
        "a music-sync-licensing-royalty-calculator agent computing per-stream micro-royalties against PRS/ASCAP rates, "
        "a content-ID-fingerprint-matcher agent querying Google Content ID and Audible Magic for pre-emptive matching, "
        "a subtitle-and-dubbing-QC-agent validating ISO 15924 character encoding and Netflix Hermes timing specs, "
        "a viewer-engagement-analyser agent computing completion rate, skip rate, and re-watch heatmaps per segment, "
        "a parental-control-taxonomy-enforcer agent applying PEGI, MPAA, and ESRB classifications to recommendation feeds, "
        "a public-performance-licence-compliance-agent tracking theatrical exhibition royalty obligations in 60 countries, "
        "a disinformation-signal-detector agent flagging synthetic media and coordinated inauthentic behaviour patterns, "
        "and a accessibility-compliance-auditor agent verifying WCAG 2.2 AA, ADA, and European Accessibility Act captions. "
        "GDPR, CCPA, DSMA (EU Digital Services Act), UK Online Safety Act, COPPA, and EUCD compliance."
    ),
    # ── Agriculture & Food ────────────────────────────────────────────────────
    (
        "Design a 12-agent precision-agriculture and food-traceability network for a 20,000-acre arable farm group. "
        "Include: a multi-source-field-data-ingestion agent fusing satellite NDVI from Planet Labs, drone multispectral from DJI, and IoT soil sensors, "
        "a crop-disease-and-pest-early-warning-agent running vision transformer inference on field imagery for 40 pathogens, "
        "a variable-rate-application-planner agent generating prescription maps for fertiliser and crop-protection in ISOXML, "
        "a autonomous-machinery-task-scheduler agent dispatching John Deere Operations Center tasks to self-driving tractors, "
        "a water-stress-and-irrigation-controller agent computing ETc using FAO-56 Penman-Monteith and triggering pivot controllers, "
        "a yield-prediction-and-harvest-scheduler agent combining phenological models and weather ensemble for logistics planning, "
        "a grain-quality-grader agent classifying protein, moisture, and DON mycotoxin from NIR spectrometer feeds, "
        "a traceability-blockchain-recorder agent writing field-to-fork events to GS1 US Lightweight Messaging Spec on Azure Confidential Ledger, "
        "a carbon-and-biodiversity-credit-calculator agent quantifying soil organic carbon uplift for VCS and Gold Standard markets, "
        "a agri-subsidy-compliance-tracker agent checking against EU CAP Basic Income Support and eco-scheme eligibility rules, "
        "a commodity-price-risk-hedger agent recommending CBOT futures and options to lock in harvest margins, "
        "and a food-safety-incident-responder agent executing FSMA 204 traceability lot-code recalls within 24 hours. "
        "EU Regulation 178/2002, FSMA 204, Global G.A.P., ISO 22000, GDPR, and EU Biodiversity Strategy 2030 compliance."
    ),
    # ── Legal & Compliance ─────────────────────────────────────────────────────
    (
        "Build a 13-agent AI-powered contract-lifecycle-management network for a global law firm managing 200,000 active contracts. "
        "Include: a contract-intake-classifier agent routing NDAs, MSAs, SaaS agreements, and employment contracts to specialist queues, "
        "a clause-extraction-and-normaliser agent parsing 400+ clause types using Azure AI Document Intelligence and custom NER, "
        "a risk-scoring-engine-agent flagging uncapped liability, unilateral termination, and IP-ownership clauses against firm playbook, "
        "a redline-negotiation-suggester agent proposing fallback positions and alternative language from clause library in ContractPodAi, "
        "a obligation-and-deadline-tracker agent creating calendar reminders in ServiceNow for renewal, notice, and audit obligations, "
        "a counterparty-due-diligence-agent querying Companies House, SEC EDGAR, and Dun & Bradstreet Hoovers, "
        "a governing-law-and-jurisdiction-mapper agent flagging multi-jurisdictional conflict-of-law issues across 80 countries, "
        "a GDPR-data-processing-agreement-auditor agent checking DPA clauses against SCCs and Binding Corporate Rules, "
        "a e-signature-workflow-orchestrator agent routing to DocuSign with signer-order and witness requirements per jurisdiction, "
        "a contract-performance-KPI-monitor agent extracting and tracking milestone, penalty, and SLA provisions post-execution, "
        "a m-and-a-contract-review-agent bulk-analysing 5,000 target-company contracts for change-of-control triggers, "
        "a legal-spend-analytics-agent reconciling matter budgets against eBilling data in Brightflag, "
        "and a regulatory-change-impact-assessor agent scanning EUR-Lex and US Federal Register for clauses needing amendment. "
        "GDPR, UK GDPR, EU AI Act, CCPA, UCITA, UCC Articles 1-2, Sarbanes-Oxley Section 302, and ABA Model Rules of Professional Conduct."
    ),
    # ── Human Resources Continued ──────────────────────────────────────────────
    (
        "Create a 13-agent workforce-planning and skills-intelligence network for a 100,000-employee professional-services firm. "
        "Include: a external-labour-market-signal-agent scraping LinkedIn, Burning Glass, and Indeed job postings to identify emerging skill demands, "
        "a internal-skills-inventory-agent extracting competencies from performance reviews, certifications, and project allocations in Workday, "
        "a skills-gap-heatmap-generator-agent overlaying future project pipeline demand against current workforce capability, "
        "a succession-risk-quantifier-agent identifying single-point-of-failure roles with no ready successors in 9-box grid, "
        "a learning-pathway-constructor-agent linking skill gaps to Degreed pathways, Coursera content, and internal LMS modules, "
        "a project-staffing-optimiser-agent solving mixed-integer programming to match consultants to engagements by skill, grade, and location, "
        "a visa-and-mobility-compliance-checker-agent validating right-to-work, L1/L2, and business visitor visa rules for 45 countries, "
        "a compensation-equity-analyser-agent running regression to detect unexplained pay gaps by gender, ethnicity, and disability, "
        "a headcount-scenario-planner-agent modelling hiring, attrition, and promotion pathways under 3 revenue growth scenarios, "
        "a gig-and-contractor-workforce-integrator-agent managing SOW compliance and IR35/IC-classification in Fieldglass, "
        "a employee-wellbeing-risk-detector-agent analysing anonymised pulse-survey sentiment and EAP utilisation patterns, "
        "a regulatory-reporting-compiler-agent generating VETS-4212, EEO-1, UK Gender Pay Gap, and EU Pay Transparency returns, "
        "and a organisational-network-analyser-agent mapping informal influence networks from anonymised email metadata. "
        "GDPR, UK GDPR, IR35, EU Pay Transparency Directive, EEOC, OFCCP, and EU AI Act high-risk HR Annex III compliance."
    ),
    # ── Banking Operations ─────────────────────────────────────────────────────
    (
        "Design a 14-agent autonomous corporate-banking KYC refresh and onboarding network. "
        "Include: a document-orchestration-agent requesting and chasing 34 KYC documents from corporate clients via secure portal, "
        "a beneficial-ownership-extractor-agent parsing company registers from Companies House, LEI/GLEIF, and SEC EDGAR, "
        "a UBO-chain-resolver-agent tracing ownership chains up to 25 levels deep to identify natural persons with 25%+ control, "
        "a adverse-media-screener-agent querying Dow Jones Factiva, Refinitiv World-Check, and LexisNexis in real time, "
        "a PEP-and-relative-associate-screener-agent comparing against 2.6M PEP profiles with relationship graph, "
        "a sanctions-and-debarment-checker-agent querying OFAC SDN, EU Consolidated, HM Treasury, and UN 1267 lists, "
        "a business-model-risk-assessor-agent computing inherent risk score using FATF guidance for 40 industry verticals, "
        "a document-authenticity-verifier-agent running ID document forensic checks via Onfido and Jumio APIs, "
        "a credit-risk-profiler-agent pulling Moody's ESG and credit rating transitions for corporate counterparties, "
        "a periodic-review-scheduler-agent segmenting clients into annual, biennial, and triennial review cycles by risk tier, "
        "a correspondent-bank-due-diligence-agent applying Wolfsberg Group questionnaire to 140 correspondent relationships, "
        "a regulatory-filing-packager-agent compiling EDD evidence packs for FCA and ECB supervisory requests, "
        "a client-friction-minimiser-agent detecting duplicate document requests and pre-populating forms from prior submissions, "
        "and a KYC-cost-per-client-analyser-agent benchmarking operational cost against LexisNexis KYC Benchmarking survey. "
        "FATF 40 Recommendations, EU 6AMLD, FCA SYSC 6.3, ECB KYC Guide, GDPR, and FinCEN CDD Final Rule compliance."
    ),
    # ── Pharmaceuticals R&D ────────────────────────────────────────────────────
    (
        "Build a 13-agent AI-accelerated drug-discovery network focused on target identification through IND-enabling studies. "
        "Include: a omics-data-integrator agent fusing scRNA-seq from 10x Genomics, proteomics from Bruker timsTOF, and GWAS from UK Biobank, "
        "a target-druggability-scorer agent computing pocket score, conservation, and tissue-selectivity from AlphaFold2 structures, "
        "a patent-freedom-to-operate-agent parsing USPTO, EPO, and WIPO claims for target and compound families, "
        "a hit-identification-engine-agent running virtual screening with Schrödinger Glide and GNINA-CNN across 500M compound library, "
        "a ADMET-prediction-agent computing absorption, distribution, metabolism, excretion, and toxicity profiles with pkCSM, "
        "a lead-optimisation-suggestor-agent proposing R-group substitutions using Bayesian multi-objective optimisation, "
        "a synthetic-route-planner-agent generating retrosynthetic pathways via ASKCOS and rating synthetic accessibility, "
        "a regulatory-strategy-advisor-agent mapping development plan against ICH M4 CTD and selecting qualification strategies, "
        "a competitor-landscape-tracker-agent monitoring ClinicalTrials.gov, Cortellis, and Citeline Pipeline for rival programs, "
        "a in-vitro-assay-design-recommender-agent proposing biochemical, cellular, and selectivity panel protocols, "
        "a CMC-feasibility-assessor-agent evaluating crystalline form, solubility, and API manufacturing complexity early, "
        "a animal-study-3R-evaluator-agent proposing in-silico and organoid alternatives per NC3Rs PREPARE framework, "
        "and a portfolio-prioritisation-agent scoring programs on rNPV, unmet need, and probability of technical success. "
        "ICH Q8/Q9/Q10, GLP 21 CFR Part 58, GDPR for clinical-trial data, EU AI Act Article 6, EU REACH, and 3Rs NC3Rs guidance."
    ),
    # ── Public Safety ─────────────────────────────────────────────────────────
    (
        "Create a 13-agent predictive-policing-and-resource-allocation network with bias-mitigation safeguards for a metropolitan police force. "
        "Include: a crime-incident-stream-consumer agent ingesting CAD data from Motorola PremierOne via WebSocket, "
        "a hotspot-prediction-engine-agent running kernel density estimation and Prophet time-series on 5-year crime history, "
        "a demographic-bias-auditor-agent computing false-positive and disparate-impact rates per protected class before any deployment, "
        "a patrol-resource-scheduler-agent solving vehicle-routing problem across 12 sectors with shift-constraint programming, "
        "a domestic-violence-risk-scorer-agent applying DASH tool scoring and routing high-risk cases to specialist MARAC, "
        "a social-media-open-source-intelligence-agent monitoring Twitter/X, Telegram, and TikTok for emerging threats via Cobwebs API, "
        "a gang-intelligence-network-mapper-agent maintaining entity-relationship graph and suppressing sharing with non-vetting-cleared officers, "
        "a digital-forensics-triage-agent prioritising seized device queues by case priority and examiner capacity in AXIOM, "
        "a custody-suite-rights-compliance-agent tracking PACE Code C entitlements and alerting on detention-limit breaches, "
        "a body-worn-video-evidence-manager-agent tagging BWV footage with officer ID and crime reference and redacting faces for disclosure, "
        "a performance-data-compiler-agent producing HMICFRS PEEL inspection data packs and Crime Statistics Tool uploads, "
        "a mental-health-liaison-connector-agent routing crisis incidents to NHS 111 and Street Triage teams via NHS Spine, "
        "and a community-impact-feedback-agent publishing neighbourhood crime summaries to Neighbourhood Alert and gauging public confidence. "
        "UK PACE 1984, Data Protection Act 2018, College of Policing APP, EU AI Act high-risk law enforcement Annex III, GDPR, and PND NEC4 data sharing."
    ),
    # ── Professional Services ──────────────────────────────────────────────────
    (
        "Design a 12-agent AI-powered audit-execution network for a Big 4 accounting firm conducting a public-company financial-statement audit. "
        "Include: a risk-assessment-agent mapping entity-level and account-level risks using PCAOB AS 2110 methodology, "
        "a materiality-calculator-agent computing planning, tolerable, and clearly trivial materiality thresholds from trial balance, "
        "a journal-entry-tester-agent extracting 100% of JEs from SAP S/4HANA via BAPI and filtering high-risk entries by 12 attributes, "
        "a accounts-receivable-confirmation-agent selecting stratified sample and dispatching electronic confirmations via Confirmation.com, "
        "a inventory-observation-scheduler-agent planning physical count attendance across 15 warehouse locations and recording exceptions, "
        "a going-concern-indicator-agent scanning for 25 negative financial and operational indicators and computing Altman Z-score, "
        "a related-party-transaction-identifier-agent comparing officer-and-director names against vendor/customer master using fuzzy matching, "
        "a revenue-recognition-testing-agent validating ASC 606 five-step model application to a sample of contracts, "
        "a internal-control-deficiency-classifier-agent mapping control gaps to COSO 2013 principles and categorising as SD or MW, "
        "a audit-file-quality-reviewer-agent checking PBC requests, sign-offs, and conclusion linkages meet PCAOB AS 1215, "
        "a independence-conflict-checker-agent screening audit team against restricted entity list via Thomson Reuters Checkpoint Edge, "
        "and a XBRL-tagging-validator-agent verifying iXBRL financial statement tagging against SEC EDGAR Inline XBRL viewer. "
        "PCAOB AS standards, AICPA SASs, IAASB ISAs, SEC Regulation S-X, GDPR for client data, and SOX Section 404 compliance."
    ),
    # ── Defence & Aerospace ────────────────────────────────────────────────────
    (
        "Build a 14-agent autonomous maintenance-repair-overhaul network for a commercial airline fleet of 180 widebody aircraft. "
        "Include: a flight-operations-data-ingester agent consuming ACARS, QAR, and FOQA feeds via SITA AviNet, "
        "a airframe-health-monitor agent running physics-based damage-tolerance models on fatigue-critical structures, "
        "a engine-trend-monitor-agent tracking EGT margin, vibration, and oil-consumption against Rolls-Royce ACMF limits, "
        "a airworthiness-directive-compliance-tracker agent parsing FAA, EASA, and TCCA ADs and computing next-due dates, "
        "a work-package-builder-agent deconstructing MRO visit scope from Aircraft Maintenance Planning System, "
        "a material-provisioning-agent running probabilistic demand forecasting and placing orders in SAP MM with AOG priority levels, "
        "a tooling-and-equipment-scheduler-agent ensuring calibrated tooling availability from TRACKOR calibration records, "
        "a skill-and-authorisation-checker-agent verifying EASA Part-66 licence category and type-rating currency in HRMSat, "
        "a hangar-slot-and-jig-allocation-agent solving multi-resource scheduling across 6 hangars using constraint programming, "
        "a quality-escape-root-cause-agent running FMEA-guided investigation when a rework rate exceeds 3%, "
        "a performance-guarantee-invoice-agent computing power-by-the-hour charges against Rolls-Royce TotalCare SLA, "
        "a regulatory-report-submitter-agent filing EASA Form 1, FAA 8130-3, and CAMO continuing-airworthiness reports, "
        "a lease-redelivery-condition-monitor-agent tracking accrual for lease-end maintenance reserves in Avaloq, "
        "and a ESG-CO2-per-flight-hour-reporter-agent feeding Scope 1 emissions to CORSIA and EU ETS registries. "
        "EASA Part-145, FAA AC 43.13, IOSA, SMS, GDPR, EU ETS Directive, and CORSIA compliance."
    ),
    # ── Marketing & Ad Tech ───────────────────────────────────────────────────
    (
        "Create a 13-agent programmatic-advertising and brand-safety network for a global media agency managing $2B in annual ad spend. "
        "Include: a audience-data-onboarding-agent hashing and matching CRM data to LiveRamp IdentityLink and Trade Desk UID2, "
        "a contextual-safety-classifier-agent scoring page content against GARM Brand Safety Floor using Integral Ad Science API, "
        "a deal-id-marketplace-curator-agent selecting private-marketplace deals from SSPs (Xandr, Magnite, PubMatic) by PMI score, "
        "a bid-optimisation-engine-agent running multi-armed bandit across 800 audience segments and creative variants, "
        "a frequency-cap-enforcer-agent capping individual-user impressions across devices using cross-device graph, "
        "a viewability-and-attention-scorer-agent integrating Moat and Lumen Research attention metrics into CPM adjustments, "
        "a ad-fraud-sentinel-agent querying DoubleVerify and White Ops metrics and auto-blocking suspected bot traffic, "
        "a attribution-model-reconciler-agent reconciling last-click, data-driven, and media-mix-model results, "
        "a media-plan-pacing-monitor-agent comparing daily delivery against flight pacing curves and rebalancing budgets, "
        "a cookie-less-identity-migration-agent testing Unified ID 2.0, Publisher Provided Identifiers, and Cohort-API performance, "
        "a carbon-ad-impressions-calculator-agent computing gCO2 per thousand impressions using Scope3 API, "
        "a creative-performance-fatigue-detector-agent identifying declining CTR and triggering A/B test with new variants, "
        "and a regulatory-consent-signal-validator-agent verifying IAB TCF v2.2 consent strings before every bid request. "
        "GDPR, ePrivacy Regulation, CCPA, UK PECR, COPPA, IAB TCF v2.2, Google EEA Consent Policy, and GARM standards."
    ),
    # ── Environmental ─────────────────────────────────────────────────────────
    (
        "Design a 12-agent enterprise ESG data-collection and disclosure network for a FTSE 100 company. "
        "Include: a Scope-1-emissions-calculator-agent aggregating fuel combustion data from SAP Plant Maintenance and facility IoT meters, "
        "a Scope-2-market-based-and-location-based-calculator-agent pulling energy invoices, RECs, and grid emission factors from IEA, "
        "a Scope-3-value-chain-estimator-agent computing 15 GHG Protocol categories using spend-based, supplier-specific, and hybrid methods, "
        "a supplier-emissions-data-request-agent sending CDP Supply Chain questionnaires and chasing completion via automated Salesforce workflow, "
        "a nature-and-biodiversity-risk-assessor-agent mapping operational sites against IBAT biodiversity databases and TNFD LEAP framework, "
        "a water-stress-accounting-agent computing withdrawal and consumption intensity against WRI Aqueduct water-risk scores, "
        "a social-indicator-aggregator-agent collecting gender pay gap, LTIFR, living-wage coverage, and community investment data from Workday, "
        "a regulatory-disclosure-compiler-agent producing CSRD ESRS, TCFD, GRI, and SASB reports with XBRL tagging, "
        "a SBTi-target-progress-tracker-agent computing annual reduction pathway and flagging deviations above 5% from glide path, "
        "a green-bond-eligibility-screener-agent validating capital projects against ICMA Green Bond Principles use-of-proceeds criteria, "
        "a assurance-evidence-packager-agent compiling reasonable-assurance documentation for KPMG/PwC external verification, "
        "and a ESG-ratings-correlation-analyser-agent tracking MSCI, Sustainalytics, and ISS ratings and modelling score-driver levers. "
        "CSRD, ESRS E1-E4, TCFD, TNFD, EU Taxonomy Regulation, SEC Climate Disclosure Rule, GDPR, and GRI Standards compliance."
    ),
    # ── Banking Risk ───────────────────────────────────────────────────────────
    (
        "Build a 14-agent model-risk-management network for a systemic investment bank. "
        "Include: a model-inventory-keeper-agent maintaining BCBS 239-compliant metadata for 4,200 risk models in Collibra, "
        "a challenger-model-builder-agent running statistical benchmarks and ML alternatives against production model outputs, "
        "a back-testing-orchestrator-agent running daily VaR back-tests per Basel III traffic-light test against P&L attribution, "
        "a sensitivity-analysis-agent perturbing inputs across ±20% ranges and documenting output stability, "
        "a data-quality-gate-agent checking completeness, timeliness, and referential integrity before model ingestion, "
        "a FRTB-SBA-and-IMA-compliance-checker-agent validating P&L attribution test and risk-factor eligibility per CRR III, "
        "a IFRS-9-ECL-model-validator-agent comparing PD, LGD, and EAD parameter outputs against through-the-cycle actuals, "
        "a climate-stress-scenario-integrator-agent embedding ECB and BoE climate scenarios into macro-satellite models, "
        "a counterparty-CVA-and-SA-CCR-calculator-agent computing exposures under SA-CCR netting set rules, "
        "a operational-risk-scenario-analyser-agent running Monte Carlo across 30 BEICFs to compute OpVaR, "
        "a governance-workflow-orchestrator-agent routing model validation findings through three-tier approval in ServiceNow, "
        "a sr-11-7-compliance-monitor-agent checking documentation and pre-implementation approval completeness, "
        "a TRIM-examiner-pack-compiler-agent generating ECB Targeted Review of Internal Models evidence tables, "
        "and a model-performance-KPI-dashboard-agent feeding Gini, HHI, and MAPE metrics to a Board Risk Committee Power BI report. "
        "BCBS 239, FRTB, IFRS 9, ECB TRIM, SR 11-7, CRR III, Basel IV, EBA GL on model risk, and GDPR compliance."
    ),
    # ── Customer Experience ───────────────────────────────────────────────────
    (
        "Create a 12-agent intelligent contact-centre and customer-experience orchestration network for a telecom with 8M subscribers. "
        "Include: a real-time-voice-transcriber-agent processing calls via AWS Transcribe Streaming with live speaker diarisation, "
        "a intent-and-sentiment-classifier-agent running BERT fine-tuned on telecom intents with real-time confidence scoring, "
        "a next-best-action-recommender-agent pulling customer 360 from Salesforce Service Cloud and recommending retention offers, "
        "a churn-propensity-scorer-agent computing likelihood-to-churn from network quality complaints and billing sensitivity, "
        "a knowledge-base-retriever-agent querying Confluence and ServiceNow articles with RAG using Azure AI Search, "
        "a agent-assist-whisper-generator-agent pushing real-time coaching cards to agent desktop via Genesys Widget API, "
        "a automatic-call-summariser-agent generating structured case notes and updating Salesforce after call ends, "
        "a escalation-decision-arbiter-agent triggering supervisor takeover when sentiment drops below threshold for 90 seconds, "
        "a billing-dispute-resolver-agent querying CDR systems and applying fair-usage policy rules for credit decisions, "
        "a NPS-survey-dispatcher-agent sending post-interaction surveys via Qualtrics within 10 minutes of call close, "
        "a regulatory-recording-compliance-agent ensuring Ofcom and GDPR-compliant call recording with 6-year retention and retrieval, "
        "and a contact-centre-performance-publisher-agent pushing AHT, FCR, CSAT, and NPS to a real-time Tableau Operations Dashboard. "
        "GDPR, Ofcom Consumer Experience Report requirements, PCI-DSS for IVR payment, MiFID II (for financial advice calls), and UK Consumer Duty compliance."
    ),
    # ── Private Equity ────────────────────────────────────────────────────────
    (
        "Design a 13-agent private-equity deal-sourcing and portfolio-monitoring network for a $10B growth-equity fund. "
        "Include: a market-signal-scraper-agent consuming Pitchbook, Crunchbase, CB Insights, and SEC Form D filings for target companies, "
        "a proprietary-deal-origin-tracker-agent maintaining relationship mapping in Salesforce Financial Services Cloud, "
        "a company-quality-screener-agent computing composite score on revenue growth, margin, NRR, and CAC:LTV, "
        "a founder-and-team-diligence-agent pulling LinkedIn profiles, patent filings, and academic records with bias guardrails, "
        "a competitive-landscape-mapping-agent generating Gartner-style market maps from Crayon and G2 category data, "
        "a financial-model-population-agent extracting 3-year actuals and spreading to integrated 5-year 3-statement model, "
        "a legal-diligence-coordinator-agent orchestrating cap-table analysis, IP chain-of-title, and customer-contract review, "
        "a valuation-benchmarker-agent computing entry multiple against precedent transactions from Pitchbook comps, "
        "a ESG-materiality-screener-agent applying SASB and UNPRI pre-investment ESG filters, "
        "a portfolio-company-KPI-aggregator-agent pulling monthly metrics from 23 portfolio companies via API and standardising to fund schema, "
        "a value-creation-initiative-tracker-agent monitoring 100-day plan milestones and escalating slippage to deal partners, "
        "a exit-readiness-assessor-agent scoring companies on strategic-buyer fit, public-market comparables, and management bandwidth, "
        "and a LP-reporting-compiler-agent generating ILPA-compliant quarterly reports and PCAP statements. "
        "GDPR, AIFMD II, SEC RIA Form ADV, ILPA Principles 3.0, UNPRI reporting framework, and MiFID II compliance."
    ),
    # ── Cybersecurity ─────────────────────────────────────────────────────────
    (
        "Build a 14-agent AI-powered cloud-security-posture-management network for a multi-cloud enterprise. "
        "Include: a cloud-asset-discovery-agent continuously inventorying resources across AWS, Azure, and GCP via CloudQuery, "
        "a misconfiguration-detector-agent evaluating 600 CIS Benchmark controls across S3, IAM, VPC, and Kubernetes, "
        "a privilege-escalation-path-analyser-agent running Cartography graph traversal to find IAM policy explosion paths, "
        "a secrets-sprawl-detector-agent scanning source repos and container images for API keys and certificates via Trufflehog, "
        "a runtime-threat-behaviour-monitor-agent consuming Falco events from Kubernetes nodes and correlating MITRE ATT&CK techniques, "
        "a container-image-vulnerability-scanner-agent running Trivy and Grype against all registry images and blocking Critical CVEs from prod, "
        "a compliance-posture-reporter-agent mapping controls to SOC 2 Type II, ISO 27001, PCI-DSS, and NIST CSF 2.0, "
        "a network-segmentation-verifier-agent running automated reachability tests to validate zero-trust policy enforcement, "
        "a infrastructure-as-code-security-reviewer-agent scanning Terraform and Helm charts via Checkov before every PR merge, "
        "a identity-threat-detection-agent analysing Okta, Azure AD, and AWS IAM events for account-takeover indicators, "
        "a data-exfiltration-risk-scorer-agent correlating cloud egress spikes with data-classification tags in Microsoft Purview, "
        "a SLA-breach-predictor-agent projecting time-to-remediate for high-risk findings against MTTR SLA commitments, "
        "a vendor-risk-posture-aggregator-agent querying SecurityScorecard and BitSight ratings for top 50 SaaS vendors, "
        "and a board-level-cybersecurity-briefer-agent compiling monthly risk posture narrative with quantified financial exposure. "
        "NIST CSF 2.0, ISO 27001:2022, PCI-DSS v4.0, GDPR, SOC 2 Type II, EU NIS2, DORA Article 16, and CMMC Level 2 compliance."
    ),
    # ── Wealth Management ─────────────────────────────────────────────────────
    (
        "Create a 12-agent autonomous wealth-management and financial-planning network for a private bank with 3,000 UHNW clients. "
        "Include: a client-goals-extractor-agent parsing life-goals statements from onboarding interviews via Zoom transcript API, "
        "a goals-based-financial-plan-builder-agent running Monte Carlo simulation on retirement, education, and estate objectives, "
        "a strategic-asset-allocation-optimiser-agent computing Black-Litterman model incorporating client's market views, "
        "a alternative-investment-eligibility-checker-agent verifying AIFMD investor classification and QPAM status, "
        "a tax-loss-harvesting-agent scanning the portfolio daily for wash-sale-compliant loss-realisation opportunities, "
        "a estate-and-succession-plan-coordinator-agent cross-referencing trust structures with multi-jurisdiction inheritance rules, "
        "a philanthropy-impact-analyser-agent computing impact metrics for DAF and private-foundation grant proposals, "
        "a concentrated-position-risk-manager-agent modelling single-stock tail risk and proposing collar and exchange-fund strategies, "
        "a regulatory-suitability-monitor-agent checking every recommendation against MiFID II suitability and UK Consumer Duty outcome tests, "
        "a performance-attribution-explainer-agent decomposing alpha by factor, selection, and timing using Brinson-Hood-Beebower, "
        "a CRS-and-FATCA-reporting-agent computing account-holder residency and generating automated Form 8938 and CRS XML, "
        "and a client-review-meeting-preparer-agent assembling personalised 40-page portfolio review packs with natural-language commentary. "
        "MiFID II, UK Consumer Duty, GDPR, FATCA, CRS/OECD, Solvency II (for insurance wrappers), AIFMD II, and SEC RIA compliance."
    ),
    # ── Shared Services & Finance Ops ─────────────────────────────────────────
    (
        "Design a 13-agent intelligent accounts-payable and procure-to-pay network for a global shared-service centre. "
        "Include: a multi-channel-invoice-ingestion-agent capturing PDF, EDI X12 810, Peppol UBL, and email invoices via Azure Form Recognizer, "
        "a three-way-match-engine-agent comparing invoice line items against SAP MM PO and GR in real time with 97% straight-through rate, "
        "a duplicate-invoice-detector-agent running fuzzy-match across vendor, amount, date, and invoice-number dimensions, "
        "a VAT-and-GST-reclaim-optimiser-agent classifying input tax recovery eligibility across 35 country VAT schemes, "
        "a payment-terms-optimiser-agent computing dynamic discounting offers via C2FO platform and comparing against WACC, "
        "a IBAN-and-BIC-validator-agent verifying payment destination accounts against SWIFT GPI and running positive-pay confirmation, "
        "a vendor-master-data-steward-agent deduplicating and enriching supplier records with DUNS, LEI, and EIN in Ariba, "
        "a invoice-dispute-resolver-agent logging root-cause by category (PO mismatch, GR miss, price variance) and routing to buyer, "
        "a accruals-preparer-agent computing month-end accruals for uninvoiced receipts and posting to SAP FI via BAPI, "
        "a regulatory-e-invoicing-compliance-agent generating XML per Italy SdI, France Chorus Pro, and Germany ZUGFeRD standards, "
        "a working-capital-KPI-publisher-agent computing DPO, early-payment discount capture, and on-time-payment rate for CFO pack, "
        "a internal-audit-evidence-packager-agent collecting SOX Narrative 4.11 evidence for AP controls quarterly, "
        "and a supplier-relationship-health-scorer-agent tracking response times, dispute rates, and on-time delivery against SLA. "
        "SOX, GDPR, EU e-invoicing Directive 2014/55/EU, VAT Directive 2006/112/EC, UNCITRAL Model Law, and GAAP/IFRS compliance."
    ),
    # ── Transport & Mobility ───────────────────────────────────────────────────
    (
        "Build a 14-agent autonomous fleet-electrification and charging-infrastructure-management network for a 10,000-vehicle urban delivery fleet. "
        "Include: a route-and-mission-profile-analyser-agent computing per-vehicle daily kWh demand from telematics via Samsara API, "
        "a charging-schedule-optimiser-agent solving mixed-integer LP across 300 charging sites to minimise demand-charge peaks, "
        "a grid-connection-capacity-monitor-agent querying DNO agreed-capacity headroom from Western Power Distribution API, "
        "a renewable-energy-procurement-agent matching charging demand to PPA-backed renewable generation using ELEXON settlement data, "
        "a battery-degradation-predictor-agent modelling SoH per vehicle from cycle count, C-rate, and temperature history, "
        "a predictive-range-anxiety-alerter-agent flagging vehicles likely to fail route completion and re-routing to nearest DCFC, "
        "a OCPP-1.6-charger-management-agent monitoring charger status, firmware versions, and fault codes in real time, "
        "a RFID-and-CPO-billing-reconciler-agent matching charge sessions to invoices from 8 CPO partners and disputing anomalies, "
        "a vehicle-to-grid-scheduler-agent participating in NGESO FFR and BM Short-Term Operating Reserve when vehicles are idle, "
        "a carbon-offsetting-credit-tracker-agent computing LCFS credits per kWh delivered and submitting to CARB, "
        "a driver-behaviour-and-range-coaching-agent delivering eco-driving tips via in-cab Samsara tablet based on real-time feedback, "
        "a insurance-fleet-telematics-reporter-agent compiling monthly driving-risk scores per vehicle for AXA Fleet telematics policy, "
        "a regulatory-CVRAS-compliance-agent verifying Zero Emission Vehicle mandate quota compliance per UK ZEVAM 2024, "
        "and a total-cost-of-ownership-vs-ICE-benchmarker-agent tracking kWh cost, maintenance saving, and downtime vs diesel baseline. "
        "UK ZEVAM, EU Alternative Fuels Infrastructure Regulation, OCPP 1.6/2.0.1, GDPR, CARB LCFS, ISO 15118, and ISO 27001 compliance."
    ),
    # ── Media Publishing ──────────────────────────────────────────────────────
    (
        "Create a 12-agent AI-powered newsroom automation and misinformation-detection network for a global news agency. "
        "Include: a wire-service-aggregator-agent consuming AP, Reuters, AFP, and DPA feeds via IPTC newsML-G2, "
        "a source-credibility-scorer-agent rating news sources against MBFC Media Bias Chart and EUvsDisinfo database, "
        "a claim-extraction-and-fact-check-router-agent parsing assertions and querying ClaimBuster, Full Fact API, and Snopes, "
        "a deepfake-audio-video-detector-agent running Hive AI and Microsoft Video Authenticator on all uploaded multimedia, "
        "a story-duplication-detector-agent computing sentence-transformer embeddings and flagging near-duplicate coverage, "
        "a headline-sentiment-bias-auditor-agent comparing draft headlines against body sentiment for sensationalism scoring, "
        "a source-diversity-monitor-agent tracking gender, geographic, and institutional diversity of sources per story, "
        "a real-time-translation-and-adaptation-agent producing culturally-adapted versions in 14 languages via DeepL and GPT-4o, "
        "a SEO-and-distribution-optimiser-agent selecting keywords, meta descriptions, and social-platform snippets per format, "
        "a copyright-and-fair-use-checker-agent verifying third-party media rights before publication via CCC RightsLink, "
        "a breaking-news-push-alert-editor-agent crafting 150-character mobile alerts with contextual severity classification, "
        "and a reader-engagement-and-retraction-tracker-agent monitoring correction requests and updating article version history. "
        "GDPR, EU Digital Services Act Article 17 content moderation, IPSO Editors Code, EUCD, GDPR Art 85 journalistic exemption, and IFCN fact-checker principles."
    ),
    # ── Healthcare Continued ──────────────────────────────────────────────────
    (
        "Design a 14-agent autonomous hospital command-centre network coordinating 1,200 inpatients across 8 wards. "
        "Include: a real-time-capacity-dashboard-agent aggregating bed census, pending discharges, and ED admissions from Epic ADT, "
        "a discharge-barrier-identifier-agent classifying delays into transport, pharmacy, social-care, and clinical-decision categories, "
        "a predictive-admissions-surge-agent combining ED triage acuity scores and 7-day seasonal model to forecast 12-hour demand, "
        "a bed-placement-optimisation-agent running constraint-satisfaction solver matching patient isolation, gender, and clinical needs, "
        "a nurse-staffing-balancer-agent comparing ward CHPPD against NHS England safe-staffing tool and triggering bank/agency requests, "
        "a surgical-waitlist-scheduler-agent prioritising RTT pathways and booking theatre slots in Theatreман from NHS waiting-list data, "
        "a pharmacy-stock-shortage-alerter-agent monitoring formulary depletion from JAC Pharmacy against BNF critical-drug list, "
        "a equipment-decontamination-scheduler-agent coordinating HSDU collection and turnaround for reusable medical devices, "
        "a frailty-score-and-falls-risk-agent running CFS and FIM assessments from structured nursing-note extraction in Epic, "
        "a transfer-to-community-coordinator-agent liaising with ICB intermediate-care, step-down, and social-care via NHS Spine, "
        "a early-warning-score-alarm-manager-agent escalating NEWS2 triggers to critical-care outreach within 3 minutes via Vocera, "
        "a patient-experience-feedback-processor-agent analysing FFT and PALS complaints and routing themes to ward managers, "
        "a CQC-compliance-gap-detector-agent checking 157 KLOE indicators against policy documents and audit records, "
        "and a carbon-footprint-per-patient-day-reporter-agent computing NHS GreenER Scope 1/2/3 data for sustainability board. "
        "CQC KLOE, GDPR, UK GDPR, NHS DSPT, HL7 FHIR R4, NICE guidelines, NHSE safe-staffing, and NET ZERO NHS 2045 compliance."
    ),
    # ── Digital Transformation ─────────────────────────────────────────────────
    (
        "Build a 13-agent enterprise-AI-governance and model-lifecycle network for a regulated financial institution adopting 150 ML models. "
        "Include: a model-registration-intake-agent capturing use-case, data-lineage, and impact-assessment via a structured intake form in ServiceNow, "
        "a EU-AI-Act-risk-classifier-agent mapping models to Annex III prohibited, high-risk, limited-risk, and minimal-risk categories, "
        "a bias-and-fairness-testing-agent running demographic parity, equalised odds, and calibration tests using IBM AI Fairness 360, "
        "a explainability-report-generator-agent producing SHAP waterfall and LIME explanations packaged per EBA GL on internal governance, "
        "a data-provenance-and-consent-checker-agent validating GDPR lawful basis and data-minimisation for every training dataset, "
        "a adversarial-robustness-tester-agent running PGD and FGSM attacks via Cleverhans and documenting model stability bounds, "
        "a third-party-vendor-AI-due-diligence-agent assessing SaaS ML vendors against EU AI Act Article 28 obligations, "
        "a model-drift-monitor-agent comparing live population statistics against training distributions using PSI and CSI daily, "
        "a model-incident-response-agent triggering automated rollback when accuracy drops below MRM-approved threshold, "
        "a audit-trail-immutable-logger-agent writing all model decisions and overrides to Azure Confidential Ledger, "
        "a senior-management-AI-risk-dashboard-agent compiling board-level AI risk appetite and KRI metrics for ECB SREP pack, "
        "a regulatory-sandbox-liaison-agent preparing Innovation Hub submissions for FCA and EBA exploratory assessments, "
        "and a continuous-improvement-feedback-loop-agent routing model performance reports back to data-science squad in JIRA. "
        "EU AI Act, GDPR, ECB TRIM, EBA GL on ICT Risk, SR 11-7, BCBS 239, ISO 42001, IEEE 7001-2021, and NIST AI RMF compliance."
    ),
]
