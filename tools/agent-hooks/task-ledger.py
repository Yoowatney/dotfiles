#!/usr/bin/env python3
"""Private, append-only task accounting. No prompt or response text is stored.

Record a stable job ID across runs/retries/checks. Reports select root requests
in [since, until), then include every recorded descendant and associated session.
Tokens are whole-session totals: use a dedicated session per job for attribution.
Explicit complete/check verdicts are required; a Stop hook is never a verdict.
Dates without offsets use --timezone (default Asia/Seoul); explicit offsets win.
Reports are JSON and expose known subtotals separately from incomplete totals.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid
from zoneinfo import ZoneInfo

KINDS = ("request", "run", "check", "complete", "reassign", "session_stop")


def timestamp(value, zone="UTC"):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=ZoneInfo(zone)) if parsed.tzinfo is None else parsed


def now():
    return datetime.now(timezone.utc).isoformat()


def read_events(path):
    events, corrupt = [], 0
    if not path.exists():
        return events, corrupt
    with path.open() as stream:
        for line in stream:
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not row.get("job_id") or not row.get("event_id") or row.get("kind") not in KINDS:
                    raise ValueError("invalid event")
                timestamp(row["at"])
                events.append(row)
            except (ValueError, TypeError, KeyError):
                corrupt += 1
    return events, corrupt


def append_event(state, event):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    path = state / "events.jsonl"
    with (state / ".lock").open("a") as lock:
        os.chmod(state / ".lock", 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        events, _ = read_events(path)
        if any(row["event_id"] == event["event_id"] for row in events):
            return False
        # Recover a torn final write without concatenating the next event to it.
        with path.open("ab+") as stream:
            os.chmod(path, 0o600)
            stream.seek(0, 2)
            if stream.tell():
                stream.seek(-1, 2)
                if stream.read(1) != b"\n":
                    stream.write(b"\n")
            stream.write((json.dumps(event, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
    return True


def number(usage, key):
    value = usage.get(key, 0)
    return max(0, value) if isinstance(value, (int, float)) else 0


def valid_usage(usage, cache_keys):
    if not isinstance(usage, dict) or not {"input_tokens", "output_tokens"} <= usage.keys():
        return False
    return all(type(usage[key]) is int and usage[key] >= 0
               for key in ("input_tokens", "output_tokens", *cache_keys) if key in usage)


def parent_thread(payload):
    if not isinstance(payload, dict):
        return None
    if payload.get("parent_thread_id"):
        return payload["parent_thread_id"]
    source = payload.get("source")
    if isinstance(source, dict):
        subagent = source.get("subagent")
        spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
        if isinstance(spawn, dict):
            return spawn.get("parent_thread_id")
    return None


def session_usage(codex_root, claude_root, wanted, since, until):
    """Read only counters, not assistant content. Deduplicate before summing."""
    codex, claude, corrupt, observed, undated, models = {}, {}, 0, set(), set(), defaultdict(set)
    session_parents = {}
    incomplete = set()
    for provider, root in (("codex", codex_root), ("claude", claude_root)):
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            sid = None
            rows = []
            dates = []
            file_models = set()
            file_parent = None
            file_bad = False
            try:
                with path.open("rb") as stream:
                    for line in stream:
                        try:
                            row = json.loads(line)
                            if not isinstance(row, dict):
                                raise ValueError("invalid record")
                            message = row.get("message")
                            payload = row.get("payload")
                            if isinstance(message, dict) and message.get("model"):
                                file_models.add(str(message["model"]))
                            if row.get("type") in ("session_meta", "turn_context") and isinstance(payload, dict) and payload.get("model"):
                                file_models.add(str(payload["model"]))
                            # Do not retain transcript content while indexing counters.
                            if row.get("type") in ("event_msg", "assistant"):
                                if provider == "codex" and (row.get("payload") or {}).get("type") == "token_count":
                                    rows.append({"type": "event_msg", "payload": row["payload"]})
                                elif provider == "claude" and isinstance(row.get("message"), dict):
                                    message = row["message"]
                                    rows.append({"type": "assistant", "uuid": row.get("uuid"),
                                                 "message": {"id": message.get("id"), "usage": message.get("usage")}})
                            if row.get("timestamp"):
                                try:
                                    dates.append(timestamp(row["timestamp"]))
                                except (ValueError, TypeError):
                                    pass
                            if row.get("type") == "session_meta":
                                sid = row.get("payload", {}).get("id", sid)
                                file_parent = parent_thread(row.get("payload")) or file_parent
                            sid = row.get("sessionId", sid)
                        except (ValueError, TypeError, AttributeError, UnicodeError):
                            corrupt += 1
                            file_bad = True
            except (OSError, UnicodeError):
                corrupt += 1
                if sid:
                    incomplete.add(sid)
                continue
            # Older transcripts can be identified by their session UUID filename.
            if sid not in wanted:
                sid = next((s for s in wanted if path.stem == s or path.stem.endswith("-" + s)), sid)
            if sid:
                if file_bad:
                    incomplete.add(sid)
                models[sid].update(file_models)
                if file_parent and file_parent != sid:
                    session_parents[sid] = file_parent
                if dates and any(since <= date < until for date in dates):
                    observed.add(sid)
                elif not dates:
                    undated.add(sid)
            if not sid:
                continue
            for row in rows:
                if provider == "codex":
                    payload = row.get("payload") or {}
                    if row.get("type") != "event_msg" or payload.get("type") != "token_count":
                        continue
                    info = payload.get("info") or {}
                    usage = info.get("total_token_usage") if isinstance(info, dict) else None
                    if not valid_usage(usage, ("cached_input_tokens",)):
                        if usage is not None or not isinstance(info, dict):
                            incomplete.add(sid)
                            corrupt += 1
                        continue
                    inclusive = number(usage, "input_tokens") + number(usage, "output_tokens")
                    exclusive = inclusive - min(number(usage, "cached_input_tokens"), number(usage, "input_tokens"))
                    # total_token_usage is cumulative; repeated snapshots are not bills.
                    if sid not in codex or inclusive >= codex[sid][0]:
                        codex[sid] = (inclusive, exclusive)
                else:
                    message = row.get("message") or {}
                    usage = message.get("usage")
                    if row.get("type") != "assistant" or not isinstance(usage, dict):
                        continue
                    if not valid_usage(usage, ("cache_read_input_tokens", "cache_creation_input_tokens")):
                        incomplete.add(sid)
                        corrupt += 1
                        continue
                    exclusive = number(usage, "input_tokens") + number(usage, "output_tokens")
                    inclusive = exclusive + number(usage, "cache_read_input_tokens") + number(usage, "cache_creation_input_tokens")
                    # Streaming records can repeat one API message with updated usage.
                    mid = message.get("id") or row.get("uuid")
                    if not mid:
                        incomplete.add(sid)
                        corrupt += 1
                        continue
                    key = (sid, mid)
                    previous = claude.get(key, (0, 0))
                    claude[key] = (max(inclusive, previous[0]), max(exclusive, previous[1]))
    result = dict(codex)
    for (sid, _), usage in claude.items():
        old = result.get(sid, (0, 0))
        result[sid] = (old[0] + usage[0], old[1] + usage[1])
    return result, corrupt, observed, undated, models, session_parents, incomplete


def fraction(numerator, denominator, **extra):
    return {"numerator": numerator, "denominator": denominator, **extra,
            "rate": numerator / denominator if denominator else None}


def report(events, corrupt, since, until, args):
    events = list(events)
    parents = {e["job_id"]: e["parent_job_id"] for e in events if e.get("parent_job_id")}
    def root(job):
        seen = set()
        while job in parents and job not in seen:
            seen.add(job)
            job = parents[job]
        return job
    requests = {}
    for e in events:
        if e["kind"] == "request" and root(e["job_id"]) == e["job_id"]:
            requests.setdefault(e["job_id"], timestamp(e["at"]))
    jobs = sorted(job for job, at in requests.items() if since <= at < until)
    cohort = [e for e in events if root(e["job_id"]) in jobs]
    recorded_event_count = len(cohort)
    sessions = {e["session_id"] for e in cohort if e.get("session_id")}
    usage, bad_sessions, observed, undated, models, session_parents, incomplete = session_usage(args.codex_root, args.claude_root, sessions, since, until)
    session_jobs = {e["session_id"]: e["job_id"] for e in events if e.get("session_id")}
    discovered = set()
    # Native subagents may not emit lifecycle hooks. Their session_meta graph
    # still associates all descendants with the original request, without text.
    pending_parents = dict(session_parents)
    while pending_parents:
        progressed = False
        for sid, parent_sid in list(pending_parents.items()):
            if sid in session_jobs:
                del pending_parents[sid]
                continue
            if parent_sid not in session_jobs:
                continue
            parent_job = session_jobs[parent_sid]
            job = f"{parent_job}:session:{sid}"
            parents[job] = parent_job
            session_jobs[sid] = job
            events.append({"job_id": job, "parent_job_id": parent_job, "session_id": sid,
                           "kind": "run", "event_id": "discovered-" + sid, "model": "codex"})
            if root(job) in jobs:
                discovered.add(sid)
            del pending_parents[sid]
            progressed = True
        if not progressed:
            break
    cohort = [e for e in events if root(e["job_id"]) in jobs]
    grouped = {job: [e for e in cohort if root(e["job_id"]) == job] for job in jobs}
    sessions = {e["session_id"] for e in cohort if e.get("session_id")}
    ownership = defaultdict(set)
    for e in events:
        if e.get("session_id"):
            ownership[e["session_id"]].add(root(e["job_id"]))
    shared = {sid for sid in sessions if len(ownership[sid]) > 1}
    run_jobs = {e["job_id"] for e in cohort if e["kind"] == "run"}
    session_jobs = {e["job_id"] for e in cohort if e.get("session_id")}
    missing_session_jobs = run_jobs - session_jobs
    missing_sessions = (sessions - usage.keys()) | (sessions & incomplete)
    known_inclusive = sum(usage[sid][0] for sid in sessions if sid in usage and sid not in shared)
    known_exclusive = sum(usage[sid][1] for sid in sessions if sid in usage and sid not in shared)
    missing_jobs, no_runs, no_results, unknown_checks, check_fails, checks = [], [], [], 0, 0, 0
    no_checks, pending, explicit_unknown, censored = [], [], [], []
    sonnet_jobs, reassigned = set(), set()
    unknown_models, unrecorded_transitions = [], []
    job_rows = []
    for job, rows in grouped.items():
        sids = {e["session_id"] for e in rows if e.get("session_id")}
        job_models = {model.lower() for sid in sids for model in models.get(sid, set())}
        job_models.update(str(e[key]).lower() for e in rows for key in ("model", "from_model") if e.get(key))
        if not any(any(family in model for family in ("sonnet", "opus", "haiku", "codex", "gpt", "o1", "o3", "o4")) for model in job_models):
            unknown_models.append(job)
        if any("sonnet" in model.lower() for sid in sids for model in models.get(sid, set())):
            sonnet_jobs.add(job)
        missing = not sids or bool(sids & (missing_sessions | shared)) or any(root(j) == job for j in missing_session_jobs)
        if missing:
            missing_jobs.append(job)
        if not any(e["kind"] == "run" for e in rows):
            no_runs.append(job)
        completions = [e for e in rows if e["kind"] == "complete" and e["job_id"] == job]
        complete = completions[-1] if completions else {}
        if not completions:
            pending.append(job)
        elif complete.get("verdict", "unknown") == "unknown":
            explicit_unknown.append(job)
        if not any(timestamp(e["at"]) < until and e.get("verdict", "unknown") != "unknown" for e in completions):
            censored.append(job)
        if complete.get("verdict", "unknown") == "unknown":
            no_results.append(job)
        check_rows = {}
        for e in rows:
            if e["kind"] == "check":
                # Updates to a check ID replace its previous verdict; attempts stay distinct.
                check_rows[(e["job_id"], e.get("check_id", e["event_id"]), e.get("attempt", 1))] = e
            if e["kind"] in ("run", "reassign") and "sonnet" in str(e.get("model", "")).lower():
                sonnet_jobs.add(job)
            if e["kind"] == "reassign" and "sonnet" in str(e.get("from_model", "")).lower():
                sonnet_jobs.add(job)
                if "opus" in str(e.get("model", "")).lower():
                    reassigned.add(job)
        for e in check_rows.values():
            verdict = e.get("verdict", "unknown")
            if verdict == "unknown":
                unknown_checks += 1
            else:
                checks += 1
                check_fails += verdict == "fail"
        if not check_rows:
            no_checks.append(job)
        if job not in reassigned and any("sonnet" in m for m in job_models) and any("opus" in m for m in job_models):
            unrecorded_transitions.append(job)
        job_rows.append({"job_id": job, "session_ids": sorted(sids),
                         "session_models": {sid: sorted(models.get(sid, set())) for sid in sorted(sids)},
                         "verdict": complete.get("verdict", "unknown"),
                         "inclusive_tokens": None if missing else sum(usage[s][0] for s in sids),
                         "exclusive_tokens": None if missing else sum(usage[s][1] for s in sids)})
    complete_tokens = not missing_jobs and bool(jobs)
    check_metric = fraction(check_fails, checks + unknown_checks, unknown=unknown_checks)
    check_metric.update({"attempted_denominator": checks + unknown_checks, "verified_denominator": checks,
                         "verified_only_rate": check_fails / checks if checks else None,
                         "missing_job_count": len(no_checks),
                         "lower_bound_observed_checks": check_fails / (checks + unknown_checks) if checks + unknown_checks else None,
                         "upper_bound_observed_checks": (check_fails + unknown_checks) / (checks + unknown_checks) if checks + unknown_checks else None})
    if unknown_checks or no_checks:
        check_metric["rate"] = None
    reassign_metric = fraction(len(reassigned), len(sonnet_jobs))
    reassign_metric.update({"model_unknown_jobs": unknown_models,
                            "unrecorded_transition_job_ids": unrecorded_transitions,
                            "unknown_reason_jobs": len(unrecorded_transitions),
                            "recorded_only_rate": len(reassigned) / len(sonnet_jobs) if sonnet_jobs else None})
    if unknown_models or unrecorded_transitions:
        reassign_metric["rate"] = None
    return {"since": since.isoformat(), "until": until.isoformat(), "timezone": args.timezone, "as_of": now(),
            "cohort": "root requests in [since, until); all linked session lifetime usage",
            "jobs": len(jobs), "job_ids": jobs, "events": recorded_event_count,
            "discovered_session_ids": sorted(discovered),
            "tokens": {"inclusive": known_inclusive if complete_tokens else None,
                       "exclusive": known_exclusive if complete_tokens else None,
                       "known_inclusive": known_inclusive, "known_exclusive": known_exclusive,
                       "inclusive_per_job": known_inclusive / len(jobs) if complete_tokens else None,
                       "exclusive_per_job": known_exclusive / len(jobs) if complete_tokens else None,
                       "denominator": len(jobs), "covered_jobs": len(jobs) - len(missing_jobs),
                       "missing_jobs": len(missing_jobs)},
            "no_result": fraction(len(no_results), len(jobs)),
            "censored_at_until_job_ids": censored,
            "check_failure": check_metric,
            "sonnet_to_opus": reassign_metric,
            "missing": {"run_jobs": no_runs, "result_jobs": no_results,
                        "pending_result_jobs": pending, "unknown_result_jobs": explicit_unknown,
                        "check_jobs": no_checks,
                        "token_jobs": missing_jobs, "session_ids": sorted(missing_sessions),
                        "shared_session_ids": sorted(shared),
                        "incomplete_session_ids": sorted(sessions & incomplete),
                        "session_job_ids": sorted(missing_session_jobs),
                        "unassociated_session_ids": sorted(observed - ownership.keys()),
                        "undated_unassociated_session_ids": sorted(undated - ownership.keys()),
                        "orphan_job_ids": sorted({e["job_id"] for e in events if root(e["job_id"]) not in requests}),
                        "corrupt_event_lines": corrupt, "corrupt_session_lines": bad_sessions},
            "job_details": job_rows}


def transcript_outcome(path):
    text, model = "", None
    with Path(path).expanduser().open() as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("type") != "assistant":
                continue
            message = row.get("message") or {}
            if not isinstance(message, dict):
                continue
            model = message.get("model", model)
            content = message.get("content", [])
            text = content if isinstance(content, str) else "\n".join(
                block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
    return text, model


def transcript_parent(path):
    with Path(path).expanduser().open() as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                return parent_thread(row.get("payload")) if row.get("type") == "session_meta" else None
    return None


def hook(args, state):
    try:
        payload = json.load(sys.stdin)
        sid = payload.get("session_id") or payload.get("sessionId")
        if not sid:
            return
        events, _ = read_events(state / "events.jsonl")
        previous = next((e for e in reversed(events) if e.get("session_id") == sid), {})
        parent = payload.get("parent_session_id") or os.environ.get("HERDR_PARENT_SESSION_ID") or previous.get("parent_session_id")
        if not parent and payload.get("transcript_path"):
            try:
                parent = transcript_parent(payload["transcript_path"])
            except (OSError, ValueError, TypeError):
                pass
        parent_job = next((e["job_id"] for e in reversed(events) if parent and e.get("session_id") == parent), None)
        metadata = payload.get("metadata") or {}
        job = os.environ.get("HERDR_TASK_ID") or metadata.get("task_id") or payload.get("task_id")
        if not job:
            job = previous.get("job_id") or parent_job
        if not job:
            return
        parent_job_id = os.environ.get("HERDR_PARENT_TASK_ID")
        # Native children inherit their parent's environment. Give each child
        # session its own outcome so its success cannot complete the root job.
        # A separately assigned pane job already differs from the parent job.
        ancestors = {e["job_id"]: e["parent_job_id"] for e in events if e.get("parent_job_id")}
        ancestor, seen = parent_job, set()
        while ancestor and ancestor != job and ancestor not in seen:
            seen.add(ancestor)
            ancestor = ancestors.get(ancestor)
        if parent and parent != sid and ancestor == job:
            job = previous.get("job_id") if previous.get("parent_job_id") == parent_job else f"{parent_job}:session:{sid}"
            parent_job_id = parent_job
        elif previous.get("parent_job_id") == job:
            parent_job_id = job
            job = previous["job_id"]
        elif previous.get("job_id") == job:
            parent_job_id = parent_job_id or previous.get("parent_job_id")
        final_text = payload.get("last_assistant_message")
        model = payload.get("model") or os.environ.get("HERDR_TASK_MODEL")
        if args.hook_event == "Stop" and payload.get("transcript_path") and (not final_text or not model):
            try:
                transcript_text, transcript_model = transcript_outcome(payload["transcript_path"])
                final_text = final_text or transcript_text
                model = model or transcript_model
            except (OSError, ValueError, TypeError):
                pass
        kind = "run" if args.hook_event == "SessionStart" else "session_stop"
        fields = {"job_id": job, "session_id": sid, "kind": kind, "at": now(), "verdict": "unknown"}
        for key, value in (("parent_session_id", parent), ("model", model),
                           ("parent_job_id", parent_job_id),
                           ("attempt", int(os.environ.get("HERDR_TASK_ATTEMPT", "1")))):
            if value:
                fields[key] = value
        # Hook retries for the same lifecycle notification do not create extra runs.
        identity = f"{job}:{sid}:{args.hook_event}:{payload.get('hook_event_id', '')}"
        fields["event_id"] = "hook-" + hashlib.sha256(identity.encode()).hexdigest()
        append_event(state, fields)
        if args.hook_event == "Stop":
            role = os.environ.get("HERDR_TASK_ROLE", "worker")
            marker = "TASK_CHECK" if role == "check" else "TASK_RESULT"
            text = final_text or ""
            verdicts = re.findall(r"^\s*" + marker + r":\s*(pass|fail|unknown)\s*$", text, re.MULTILINE) if isinstance(text, str) else []
            # Conflicting outcomes, prose, and missing output never imply success.
            verdict = verdicts[0] if len(set(verdicts)) == 1 else "unknown"
            if role == "check" or verdicts:
                outcome = dict(fields, kind="check" if role == "check" else "complete", verdict=verdict)
                outcome["check_id"] = os.environ.get("HERDR_CHECK_ID") or sid
                identity += ":outcome:" + verdict
                outcome["event_id"] = "hook-" + hashlib.sha256(identity.encode()).hexdigest()
                append_event(state, outcome)
    except Exception:
        # Accounting must never block an agent, modify its output, or leak hook input.
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get("AGENT_TASK_LEDGER_DIR", "~/.local/state/agent-task-ledger")).expanduser())
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record")
    record.add_argument("kind", choices=KINDS)
    record.add_argument("--job-id", required=True)
    for name in ("parent-job-id", "session-id", "parent-session-id", "check-id", "model", "from-model", "reason", "event-id"):
        record.add_argument("--" + name)
    record.add_argument("--attempt", type=int, default=1)
    record.add_argument("--verdict", choices=("pass", "fail", "unknown"), default="unknown")
    record.add_argument("--at", default=None)
    hook_parser = sub.add_parser("hook")
    hook_parser.add_argument("hook_event", choices=("SessionStart", "Stop"))
    lookup = sub.add_parser("job-for-session", help="Print the last associated stable job ID, or exit 1")
    lookup.add_argument("session_id")
    for command in ("report", "compare"):
        command_parser = sub.add_parser(command)
        command_parser.add_argument("--codex-root", type=Path, default=Path("~/.codex/sessions").expanduser())
        command_parser.add_argument("--claude-root", type=Path, default=Path("~/.claude/projects").expanduser())
        command_parser.add_argument("--timezone", default="Asia/Seoul")
        if command == "report":
            command_parser.add_argument("--since", required=True)
            command_parser.add_argument("--until", required=True)
        else:
            command_parser.add_argument("--baseline", required=True, help="Baseline local calendar day, YYYY-MM-DD")
            command_parser.add_argument("--trial", required=True, help="Trial local calendar day, YYYY-MM-DD")
    args = parser.parse_args()
    if args.command == "hook":
        hook(args, args.state_dir)
        return
    if args.command == "record":
        if args.at:
            timestamp(args.at)
        if args.kind == "reassign" and (not args.reason or not args.from_model or not args.model):
            parser.error("reassign requires --reason, --from-model and --model")
        event = {k: v for k, v in vars(args).items() if v is not None and k not in {"state_dir", "command"}}
        event["at"] = args.at or now()
        event["event_id"] = args.event_id or str(uuid.uuid4())
        added = append_event(args.state_dir, event)
        print(json.dumps({"event_id": event["event_id"], "added": added}))
        return
    events, corrupt = read_events(args.state_dir / "events.jsonl")
    if args.command == "job-for-session":
        job = next((e["job_id"] for e in reversed(events) if e.get("session_id") == args.session_id), None)
        if job:
            print(job)
        else:
            sys.exit(1)
        return
    if args.command == "report":
        since, until = timestamp(args.since, args.timezone), timestamp(args.until, args.timezone)
        if until <= since:
            parser.error("--until must be after --since")
        result = report(events, corrupt, since, until, args)
    else:
        result = {}
        for name in ("baseline", "trial"):
            start = timestamp(getattr(args, name), args.timezone)
            result[name] = report(events, corrupt, start, start + timedelta(days=1), args)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
