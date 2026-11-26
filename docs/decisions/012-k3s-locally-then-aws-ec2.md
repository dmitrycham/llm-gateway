# ADR-012: k3s in a local Linux VM, then k3s on AWS EC2 with Terraform

- Status: Accepted
- Date: 2026-11-26
- Supersedes: the v1 draft of this decision. Now Phase 2 runs on a local Linux VM, and
  AWS moves to Phase 3 with EC2 and k3s instead of EKS as the main target.

## Context

Phase 1 runs in Docker Compose and must not depend on Kubernetes or AWS (spec §2.3).
Phases 2 and 3 exist to practise operations: Linux administration, Kubernetes, Helm,
PostgreSQL failover and recovery, infrastructure as code and a public HTTPS
deployment (spec §2.2–2.3).

The project has no budget for an always-on cloud environment. A managed Kubernetes
control plane, a NAT gateway and a managed database are billed by the hour whether or
not anything runs on them. The same Helm chart should work in every environment, so
that Phase 3 tests the deployment and not a second set of manifests.

## Decision

- **Phase 2 (`ops-local`).** A Linux VM on the Mac (an OrbStack Linux machine) with
  a user, SSH keys, a firewall, `systemd` and `journald`. k3s is installed by hand, so
  the install is learned rather than hidden. The application is deployed with the Helm
  chart in `deploy/k8s`: probes, requests and limits, ConfigMaps, Secrets encrypted
  with SOPS, Ingress with TLS, and SSE through Ingress without buffering. PostgreSQL
  runs under CloudNativePG (ADR-014), documents go to local S3-compatible storage
  (ADR-015), Kafka and observability move into the cluster.
- **Phase 3 (`staging`).** Terraform in `infra/terraform` creates a VPC, a security
  group, IAM, one EC2 instance with k3s and S3 buckets for backups and documents.
  State is remote. The same Helm chart is installed on that k3s. Backups go to Amazon
  S3. The deployment is public over HTTPS.
- **Cost controls.** Before the first `apply`: an AWS Budgets alert, and a check of
  which services the account's current plan includes. Every run is
  `apply` → check → `destroy`, and its cost is recorded. CI runs only `fmt`,
  `validate` and `plan`, never `apply`.
- **EKS and RDS** are optional short runs (`apply` → check → `destroy`) to compare
  managed and self-managed operation. They are not a target of the Helm chart's
  defaults.
- The application code uses only the standard PostgreSQL driver and the S3 API, with
  no SDK of a specific hosting provider, so nothing in the code changes between
  environments.

## Consequences

Trade-off: Linux and Kubernetes practice and a real AWS deployment without a monthly
cloud bill, in exchange for a single-node cluster with no fault tolerance for the host
itself, and AWS environments that exist only during a run.

Positive:

- Phase 2 costs nothing, and the VM can be rebuilt from the runbook.
- One chart and one Kubernetes distribution in both phases; Phase 3 differs only in
  the infrastructure under it.
- Terraform plans are reviewed in CI without credentials that can change anything.

Negative:

- Single node in both phases: a host failure takes everything down. The failover demo
  covers a PostgreSQL primary failure, not a host failure, and the limits are
  documented.
- k3s on EC2 is operated by us: upgrades, certificates and node security are our job,
  unlike EKS.
- The Phase 3 environment is not always available for a demo; it has to be created
  first.
- OrbStack ties Phase 2 to macOS. On another machine any Linux VM with the same steps
  works, but the runbook is written for OrbStack.

## Alternatives considered

- **EKS as the main Phase 3 target (rejected).** The control plane and NAT gateway are
  billed by the hour even when idle, and EKS hides the node work this phase is meant
  to practise. Kept as an optional short run.
- **kind or minikube for Phase 2 (rejected).** Good for tests, but they run Kubernetes
  inside Docker, not on a Linux host, and they differ from what runs on EC2.
- **Docker Desktop Kubernetes (rejected).** Same issue as kind, and no Linux host to
  administer.
- **ECS on Fargate or another container service (rejected).** Cheaper to start, but
  there is no Kubernetes, and the Phase 2 chart could not be reused.
- **A rented always-on VPS (rejected).** Monthly cost for an environment used a few
  hours a week, and no infrastructure as code practice on a real cloud.
