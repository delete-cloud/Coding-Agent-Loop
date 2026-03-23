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

The template uses Go template syntax. You can render it with `go run`, `envsubst` (after converting to env-var style), or any Go template tool.

Example using a small Go helper:

```bash
go run eval/k8s/render.go \
  -task-id=task-001 \
  -goal="Fix the broken test in pkg/foo" \
  -repo="https://github.com/example/repo.git" \
  -test-cmd="go test ./..." \
  -max-iterations=10 \
  -image="registry.example.com/agent-coding-loop:latest" \
  -model-base-url="https://api.example.com/v1" \
  -model-api-key="model-api-key" \
  -model-name="gpt-4" \
  -plan-mode=on \
  -retrieval-mode=off \
  -kb-base-url="" \
  > job-task-001.yaml
```

Or with `sed` for quick one-offs:

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

```bash
# List completed eval jobs
kubectl get jobs -l component=eval --field-selector=status.successful=1

# Get logs (contains agent output and final state)
kubectl logs job/eval-task-001

# Copy state.db from a still-running or just-completed pod
POD=$(kubectl get pods -l task-id=task-001 -o jsonpath='{.items[0].metadata.name}')
kubectl cp "$POD:/state/state.db" ./results/task-001-state.db
```

## Cleanup

```bash
# Delete all eval jobs (TTL controller also cleans up after 24h)
kubectl delete jobs -l component=eval
```
