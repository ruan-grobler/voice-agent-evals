"""Write results.json, report.md, report.html and the dead letter file for one run."""

from __future__ import annotations

import html
import json
from pathlib import Path

from voice_agent_evals.baseline import BaselineDiff
from voice_agent_evals.checks import CHECK_DESCRIPTIONS, CHECK_ORDER
from voice_agent_evals.jsonutil import atomic_write_text, write_json
from voice_agent_evals.llm import ROLES
from voice_agent_evals.pricing import PRICES_AS_OF
from voice_agent_evals.results import RunResult, Tally

FICTION_NOTE = "All businesses, callers, names and numbers in this report are fictional."


def pct(tally: Tally) -> str:
    rate = tally.rate
    return "n/a" if rate is None else f"{rate:.0%}"


def money(value: float | None) -> str:
    return "unknown" if value is None else f"${value:.2f}"


def _cost_note(run: RunResult) -> str:
    note = f"Prices: Anthropic list prices, {PRICES_AS_OF}."
    if "hand-crafted" in run.provenance:
        note += " Token counts in hand-crafted cassettes are estimates (characters / 4)."
    return note


def render_markdown(
    run: RunResult, *, diff: BaselineDiff | None = None, names: dict[str, str] | None = None
) -> str:
    names = names or {}
    scenarios = run.scenario_tally()
    checks = run.check_tallies()
    all_checks = Tally()
    for tally in checks.values():
        all_checks.passed += tally.passed
        all_checks.failed += tally.failed
        all_checks.errored += tally.errored
    lines = [
        f"# Voice agent eval report: {run.agent_id} ({run.agent_name})",
        "",
        f"Source: {run.provenance}. Models: "
        + ", ".join(f"{role} `{run.models.get(role, '?')}`" for role in ROLES)
        + ".",
        "",
        FICTION_NOTE,
        "",
        "## Summary",
        "",
        "| Scenarios passed | Checks passed | Errors | Cost of this run |",
        "|---|---|---|---|",
        f"| {scenarios.label()} ({pct(scenarios)}) | {all_checks.label()} ({pct(all_checks)}) "
        f"| {len(run.errors)} | {money(run.total_cost())} |",
        "",
        "## Pass rate per check",
        "",
        "| Check | What it checks | Passed | Rate |",
        "|---|---|---|---|",
    ]
    for name in CHECK_ORDER:
        tally = checks[name]
        lines.append(f"| `{name}` | {CHECK_DESCRIPTIONS[name]} | {tally.label()} | {pct(tally)} |")
    lines += [
        "",
        "## Pass rate per scenario",
        "",
        "| Scenario | Business | Covers | Result | Runs passed | Checks passed | Failed checks |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in run.summaries():
        failed = ", ".join(f"`{c}`" for c in s.failed_checks) or ""
        lines.append(
            f"| {s.scenario_id} | {names.get(s.business, s.business)} | {s.covers} "
            f"| {s.status.upper()} | {s.outcome.label()} | {s.checks_passed.label()} | {failed} |"
        )
    if diff is not None:
        lines += ["", *render_diff_markdown(diff)]
    failures = [(r, c) for r in run.runs for c in r.checks if c.status in {"fail", "error"}]
    if failures or run.errors:
        lines += ["", "## Failures", ""]
        for r, c in failures:
            lines.append(f"- **{r.scenario_id}** `{c.check}` ({c.status}): {c.detail}")
        for r in run.errors:
            if not r.checks:
                lines.append(f"- **{r.scenario_id}** did not run: {r.error}")
    lines += [
        "",
        "## Tokens and cost",
        "",
        "| Role | Model | Input tokens | Output tokens | Cost |",
        "|---|---|---|---|---|",
    ]
    usage = run.usage_by_role()
    costs = run.cost_by_role()
    for role in ROLES:
        u = usage[role]
        lines.append(
            f"| {role} | `{run.models.get(role, '?')}` | {u.input_tokens:,} "
            f"| {u.output_tokens:,} | {money(costs[role])} |"
        )
    lines += ["", _cost_note(run), "", "## Transcripts", ""]
    for r in run.runs:
        lines += [
            f"<details><summary>{r.scenario_id} (trial {r.trial}): {r.status.upper()}</summary>",
            "",
            "```text",
            r.transcript.render() or "(no transcript)",
            "```",
        ]
        if r.judge:
            lines.append("")
            lines.append(f"Judge: {r.judge.summary}")
            for v in r.judge.criteria:
                lines.append(f"- `{v.id}` {v.verdict}: {v.reason}")
        lines += ["", "</details>", ""]
    return "\n".join(lines).rstrip() + "\n"


def render_diff_markdown(diff: BaselineDiff) -> list[str]:
    verdict = "PASS" if diff.passed else "FAIL"
    lines = [
        f"## Regression gate against baseline `{diff.baseline_agent}`",
        "",
        f"**{verdict}**: {len(diff.check_regressions)} check regressions across "
        f"{len(diff.scenario_regressions)} scenarios, {len(diff.errors)} errors, "
        f"{len([c for c in diff.fixed if c.check != 'scenario'])} checks fixed.",
        "",
    ]
    if diff.cost_change is not None:
        lines.append(
            f"Cost per run: {money(diff.cost_before)} -> {money(diff.cost_after)} "
            f"({diff.cost_change:+.0%})."
        )
        lines.append("")
    rows = [c for c in diff.changes if c.check != "scenario" or c.kind in {"missing", "new"}]
    if rows:
        lines += ["| Change | Scenario | Check | Baseline | This run |", "|---|---|---|---|---|"]
        for c in rows:
            before = "n/a" if c.before is None else f"{c.before:.0%}"
            after = "n/a" if c.after is None else f"{c.after:.0%}"
            lines.append(f"| {c.kind} | {c.scenario} | `{c.check}` | {before} | {after} |")
    for error in diff.errors:
        lines.append(f"- error: {error}")
    return lines


_CSS = """
:root{--bg:#f7f7f4;--card:#fff;--ink:#1d1d1b;--muted:#5d5d57;--line:#e2e1da;
--pass:#1f7a4d;--pass-bg:#e3f3ea;--fail:#a8321f;--fail-bg:#fbe7e2;--warn:#8a5a00;--warn-bg:#fbf0d9}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--card:#1f1f1c;--ink:#ecebe4;
--muted:#a5a49b;--line:#34332e;--pass:#6fd19b;--pass-bg:#173326;--fail:#ff8f7a;
--fail-bg:#3a1d17;--warn:#f0c062;--warn-bg:#352a12}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}
h2{font-size:18px;margin:32px 0 8px}.muted{color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:16px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.tile b{display:block;font-size:26px}.wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line)}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:13px;color:var(--muted)}code{font-size:13px}
.badge{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600}
.pass{color:var(--pass);background:var(--pass-bg)}.fail{color:var(--fail);background:var(--fail-bg)}
.error,.skipped{color:var(--warn);background:var(--warn-bg)}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:8px 0;
padding:8px 12px}
summary{cursor:pointer}pre{white-space:pre-wrap;font-size:13px;margin:8px 0}
"""


def _badge(status: str) -> str:
    return f'<span class="badge {html.escape(status)}">{html.escape(status.upper())}</span>'


def render_html(
    run: RunResult, *, diff: BaselineDiff | None = None, names: dict[str, str] | None = None
) -> str:
    names = names or {}
    e = html.escape
    scenarios = run.scenario_tally()
    checks = run.check_tallies()
    all_checks = Tally()
    for tally in checks.values():
        all_checks.passed += tally.passed
        all_checks.failed += tally.failed
        all_checks.errored += tally.errored
    out = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>Eval report {e(run.agent_id)}</title><style>{_CSS}</style></head><body><main>",
        f"<h1>Voice agent eval report: {e(run.agent_id)}</h1>",
        f"<div class='muted'>{e(run.agent_name)}. Source: {e(run.provenance)}. "
        f"{e(FICTION_NOTE)}</div>",
        "<div class='tiles'>",
        f"<div class='tile'><span class='muted'>Scenarios passed</span>"
        f"<b>{e(scenarios.label())}</b>{e(pct(scenarios))}</div>",
        f"<div class='tile'><span class='muted'>Checks passed</span>"
        f"<b>{e(all_checks.label())}</b>{e(pct(all_checks))}</div>",
        f"<div class='tile'><span class='muted'>Errors</span><b>{len(run.errors)}</b>"
        "scenarios that did not run</div>",
        f"<div class='tile'><span class='muted'>Cost of this run</span><b>"
        f"{e(money(run.total_cost()))}</b>{e(_cost_note(run))}</div>",
        "</div>",
        "<h2>Pass rate per check</h2><div class='wrap'><table><tr><th>Check</th>"
        "<th>What it checks</th><th>Passed</th><th>Rate</th></tr>",
    ]
    for name in CHECK_ORDER:
        tally = checks[name]
        out.append(
            f"<tr><td><code>{e(name)}</code></td><td>{e(CHECK_DESCRIPTIONS[name])}</td>"
            f"<td>{e(tally.label())}</td><td>{e(pct(tally))}</td></tr>"
        )
    out.append(
        "</table></div><h2>Pass rate per scenario</h2><div class='wrap'><table><tr>"
        "<th>Scenario</th><th>Business</th><th>Covers</th><th>Result</th><th>Runs passed</th>"
        "<th>Checks passed</th><th>Failed checks</th></tr>"
    )
    for s in run.summaries():
        failed = ", ".join(f"<code>{e(c)}</code>" for c in s.failed_checks)
        out.append(
            f"<tr><td>{e(s.scenario_id)}</td><td>{e(names.get(s.business, s.business))}</td>"
            f"<td>{e(s.covers)}</td><td>{_badge(s.status)}</td><td>{e(s.outcome.label())}</td>"
            f"<td>{e(s.checks_passed.label())}</td><td>{failed}</td></tr>"
        )
    out.append("</table></div>")
    if diff is not None:
        verdict = "pass" if diff.passed else "fail"
        out.append(
            f"<h2>Regression gate against baseline <code>{e(diff.baseline_agent)}</code></h2>"
            f"<p>{_badge(verdict)} {len(diff.check_regressions)} check regressions across "
            f"{len(diff.scenario_regressions)} scenarios, {len(diff.errors)} errors.</p>"
        )
        rows = [c for c in diff.changes if c.check != "scenario" or c.kind in {"missing", "new"}]
        if rows:
            out.append(
                "<div class='wrap'><table><tr><th>Change</th><th>Scenario</th><th>Check</th>"
                "<th>Baseline</th><th>This run</th></tr>"
            )
            for c in rows:
                before = "n/a" if c.before is None else f"{c.before:.0%}"
                after = "n/a" if c.after is None else f"{c.after:.0%}"
                out.append(
                    f"<tr><td>{e(c.kind)}</td><td>{e(c.scenario)}</td><td><code>{e(c.check)}"
                    f"</code></td><td>{e(before)}</td><td>{e(after)}</td></tr>"
                )
            out.append("</table></div>")
    out.append(
        "<h2>Tokens and cost</h2><div class='wrap'><table><tr><th>Role</th><th>Model</th>"
        "<th>Input tokens</th><th>Output tokens</th><th>Cost</th></tr>"
    )
    usage = run.usage_by_role()
    costs = run.cost_by_role()
    for role in ROLES:
        u = usage[role]
        out.append(
            f"<tr><td>{e(role)}</td><td><code>{e(run.models.get(role, '?'))}</code></td>"
            f"<td>{u.input_tokens:,}</td><td>{u.output_tokens:,}</td>"
            f"<td>{e(money(costs[role]))}</td></tr>"
        )
    out.append("</table></div><h2>Transcripts and findings</h2>")
    for r in run.runs:
        out.append(
            f"<details><summary>{e(r.scenario_id)} {_badge(r.status)} {e(r.title)}</summary>"
        )
        for check in r.checks:
            out.append(
                f"<div>{_badge(check.status)} <code>{e(check.check)}</code> {e(check.detail)}</div>"
            )
        if r.error and not r.checks:
            out.append(f"<div>{_badge('error')} {e(r.error)}</div>")
        out.append(f"<pre>{e(r.transcript.render() or '(no transcript)')}</pre>")
        if r.judge:
            out.append(f"<div class='muted'>Judge: {e(r.judge.summary)}</div><ul>")
            for v in r.judge.criteria:
                out.append(
                    f"<li><code>{e(v.id)}</code> {_badge(v.verdict)} {e(v.reason)} "
                    f"<span class='muted'>Evidence: {e(v.evidence)}</span></li>"
                )
            out.append("</ul>")
        out.append("</details>")
    out.append("</main></body></html>")
    return "\n".join(out) + "\n"


def write_reports(
    run: RunResult,
    out_dir: Path,
    *,
    diff: BaselineDiff | None = None,
    names: dict[str, str] | None = None,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    results = out_dir / "results.json"
    write_json(results, run.to_dict())
    written.append(results)
    md = out_dir / "report.md"
    atomic_write_text(md, render_markdown(run, diff=diff, names=names))
    written.append(md)
    page = out_dir / "report.html"
    atomic_write_text(page, render_html(run, diff=diff, names=names))
    written.append(page)
    dead = out_dir / "dead-letter.jsonl"
    if run.errors:
        lines = [
            json.dumps(
                {
                    "scenario_id": r.scenario_id,
                    "trial": r.trial,
                    "error": r.error,
                    "partial_transcript": r.transcript.to_dict(),
                },
                ensure_ascii=False,
            )
            for r in run.errors
        ]
        atomic_write_text(dead, "\n".join(lines) + "\n")
        written.append(dead)
    else:
        dead.unlink(missing_ok=True)
    return written
