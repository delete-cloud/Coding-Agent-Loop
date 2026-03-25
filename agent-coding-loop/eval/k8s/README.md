# Kubernetes Eval Pipeline

Run agent-coding-loop eval tasks as one-shot K8s Jobs.

This setup is currently scoped to a local cluster workflow. The default PVC
uses a `hostPath` mount into this repository, so treat it as local-cluster
oriented rather than a portable multi-cluster deployment recipe.

## Build the image

From the `agent-coding-loop/` directory:

```bash
docker build -t agent-coding-loop:latest .
```

Tag and push to your registry:

```bash
docker tag agent-coding-loop:latest registry.example.com/agent-coding-loop:latest
docker push registry.example.com/agent-coding-loop:latest
```

## Create the API key secret

```bash
kubectl create secret generic model-api-key --from-literal=api-key="$MODEL_API_KEY"
```

## Setup PVC

Evaluation jobs use a shared volume to collect outputs. Before running jobs:

```bash
kubectl apply -f eval/k8s/pvc.yaml
```

The bundled [pvc.yaml](/Users/kina/Code/Agent/Coding-Agent-Loop/agent-coding-loop/eval/k8s/pvc.yaml)
bind-mounts results onto your host machine via `hostPath`.

## Render the job template

The template uses Go template syntax. Render it with `sed` for quick one-offs.
You must provide:

- `TaskID`: original benchmark task id, kept for labels and result directories
- `TaskSlug`: K8s-safe slug for names, e.g. `kb-code-001`
- `Experiment`: arm name, e.g. `rag` or `no-rag`
- `Trial`: numeric trial id

Example:

```bash
sed \
  -e 's|{{.TaskID}}|kb_code_001|g' \
  -e 's|{{.TaskSlug}}|kb-code-001|g' \
  -e 's|{{.Experiment}}|rag|g' \
  -e 's|{{.Trial}}|1|g' \
  -e 's|{{.Goal}}|Fix the broken test|g' \
  -e 's|{{.Repo}}|https://github.com/example/repo.git|g' \
  -e 's|{{.TestCmd}}|go test ./...|g' \
  -e 's|{{.MaxIterations}}|10|g' \
  -e 's|{{.Image}}|registry.example.com/agent-coding-loop:latest|g' \
  -e 's|{{.ModelBaseURL}}|https://api.example.com/v1|g' \
  -e 's|{{.ModelAPIKey}}|model-api-key|g' \
  -e 's|{{.ModelName}}|gpt-4|g' \
  -e 's|{{.PlanMode}}|on|g' \
  -e 's|{{.RetrievalMode}}|off|g' \
  -e 's|{{.KBBaseURL}}||g' \
  eval/k8s/job.yaml.tmpl > job-task-001.yaml
```

## Submit a single job

```bash
kubectl apply -f job-task-001.yaml
```

## Monitor jobs

```bash
# List all eval jobs
kubectl get jobs -l component=eval

# Watch a specific task
kubectl logs -f job/eval-kb-code-001-rag-t1

# Get job status
kubectl get job eval-kb-code-001-rag-t1 -o jsonpath='{.status.conditions[0].type}'
```

## Collect results

Jobs automatically write their output to the `eval-results-pvc`. If you are using the default local `eval/k8s/pvc.yaml`, the results are bind-mounted directly to your host machine at `eval-results/` within this repository.

You no longer need to use `kubectl cp` to extract `state.db` files.

## Results directory contract

The `collect_results.py` and `summarize.py` scripts expect a specific directory layout.

### Input directory structure

```
results/
  <experiment>/
    trial-<n>/
      <task_id>/
        state.db      # required — SQLite database from the agent run
```

- The top-level split is by experiment arm, then by trial, then by `task_id`.
- A single `collect_results.py` invocation should point `--results-dir` at one experiment/trial partition, for example `./results/rag/trial-1/`.
- Each `<task_id>` directory must contain a `state.db` file.
- Directories without `state.db` or with task IDs not in the tasks file are skipped with a warning.

### Collect results into JSONL

```bash
python3 eval/k8s/collect_results.py \
    --results-dir ./results/<experiment>/trial-<n>/ \
    --tasks eval/ab/benchmark_tasks.jsonl \
    --experiment rag \
    --output results-rag.jsonl \
    --strict-mode \
    --trial 1 \
    --trial-count 3
```

### Summarize into a markdown report

```bash
# Single JSONL
python3 eval/k8s/summarize.py \
    --results results-rag.jsonl \
    --output report.md \
    --strict-mode

# Multiple JSONL files (e.g. two experiment arms)
python3 eval/k8s/summarize.py \
    --results results-rag.jsonl results-no-rag.jsonl \
    --output combined-report.md
```

## Cleanup

```bash
# Delete all eval jobs (TTL controller also cleans up after 24h)
kubectl delete jobs -l component=eval
```
