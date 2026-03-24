# Kubernetes Eval Pipeline

Run agent-coding-loop eval tasks as one-shot K8s Jobs.

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

## Render the job template

The template uses Go template syntax. Render it with `sed` for quick one-offs:

```bash
sed \
  -e 's|{{.TaskID}}|task-001|g' \
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
kubectl logs -f job/eval-task-001

# Get job status
kubectl get job eval-task-001 -o jsonpath='{.status.conditions[0].type}'
```

## Collect results from completed pods

Copy each task's `state.db` into a results directory organized by task ID:

```bash
# Copy state.db from a completed pod
POD=$(kubectl get pods -l task-id=task-001 -o jsonpath='{.items[0].metadata.name}')
kubectl cp "$POD:/state/state.db" ./results/task-001/state.db
```

## Results directory contract

The `collect_results.py` and `summarize.py` scripts expect a specific directory layout.

### Input directory structure

```
results/
  <task_id>/
    state.db          # required — SQLite database from the agent run
  <task_id>/
    state.db
  ...
```

- Each subdirectory is named by `task_id` (must match entries in the tasks JSONL file).
- Each subdirectory must contain a `state.db` file.
- Directories without `state.db` or with task IDs not in the tasks file are skipped with a warning.

### Collect results into JSONL

```bash
python3 eval/k8s/collect_results.py \
    --results-dir ./results/ \
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
