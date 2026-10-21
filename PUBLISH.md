# Publishing checklist

Nothing in this repo has been pushed or published. Ruan does the steps below himself.

## GitHub description (one line)

Regression tests for AI receptionists: 15 caller scenarios, code checks plus an LLM judge, and a CI gate that blocks a prompt change that breaks a call.

## Topics

`voice-ai`, `ai-agents`, `llm-evaluation`, `evals`, `llm-as-a-judge`, `claude`, `anthropic`, `prompt-engineering`, `regression-testing`, `python`, `pytest`, `popia`, `south-africa`

## Before making the repo public

1. Read the honesty note in the README and keep it while the cassettes are hand-crafted.
2. Spot check five transcripts and their judge labels in `cassettes/handcrafted-src/` (for example v1 and v2 of 05, 09 and 13), so you can stand behind the calibration labels yourself.
3. Run `make check` and `make demo` once more, and confirm `docs/demo-output.txt` is unchanged.
4. Run `make secret-scan` (or `gitleaks detect` if you install it) on the final history.
5. Create the GitHub repo, push, and confirm the CI badge goes green on the first run.

## To make the demo live (optional, costs money)

1. Create an Anthropic Console account and an API key at console.anthropic.com, and set a monthly spend limit on it first.
2. `cp .env.example .env`, paste the key into `.env` (it is gitignored), then `set -a; source .env; set +a`.
3. `make record` re-records all 30 cassettes from the real API. Estimated cost from the hand-crafted token counts: about $0.76 for v1 plus $1.17 for v2 at list prices. The real figure will differ (thinking tokens are not in the estimate), and `VAE_BUDGET_USD` (default $5) stops the run if it goes over.
4. `make calibrate-live` measures the real judge against the labels. Paste the result into `docs/judge-calibration.md`.
5. Run `make demo` again, commit the new cassettes and `docs/demo-output.txt`, and update the README numbers and the honesty note from the new output. Expect the live v1 to fail in slightly different places than the hand-crafted v1, which is the point of recording.
6. Optional: a Retell account and a chat agent that shares your voice agent's response engine, to build the adapter in `docs/retell-adapter.md`.

## Secret scan

Method: regex scan of every commit's patch on all branches (`make secret-scan`, `scripts/secret_scan.py`), because gitleaks is not installed on the build machine. Patterns: Anthropic, OpenAI style, AWS, GitHub, Slack, Google, Stripe live and Twilio keys, private key blocks, and generic `api_key` / `secret` / `token` / `password` assignments.

Result on the full history, every commit on every branch, 21 October 2026: **0 findings.**

Positive control: the same script run on a throwaway repo with a planted, randomly generated fake `sk-ant-` string reported 1 finding (redacted), so the scan is not silently blind.

The test suite also scans the working tree for the same patterns on every run (`tests/integration/test_repo_hygiene.py`), and checks that `.env` is not tracked and `.env.example` holds no values. No real key was read, printed or written while building this repo.
