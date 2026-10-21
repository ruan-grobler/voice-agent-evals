# Live mode (optional, costs money)

1. Create an Anthropic Console account and an API key at console.anthropic.com, and set a monthly spend limit on it first.
2. `cp .env.example .env`, paste the key into `.env` (it is gitignored), then `set -a; source .env; set +a`.
3. `make record` re-records all 30 cassettes from the real API. Estimated cost from the hand-crafted token counts: about $0.76 for v1 plus $1.17 for v2 at list prices. The real figure will differ (thinking tokens are not in the estimate), and `VAE_BUDGET_USD` (default $5) stops the run if it goes over.
4. `make calibrate-live` measures the real judge against the labels. Paste the result into `docs/judge-calibration.md`.
5. Run `make demo` again, commit the new cassettes and `docs/demo-output.txt`, and update the README numbers and the honesty note from the new output. Expect the live v1 to fail in slightly different places than the hand-crafted v1, which is the point of recording.
6. Optional: a Retell account and a chat agent that shares your voice agent's response engine, to build the adapter in `docs/retell-adapter.md`.

