"""Prometheus text exposition helpers shared by the lab portals: the metric line formatter and the run / test metrics every
portal exposes (the lab-specific gauges are the portal's own)."""
import time


def _esc(v): return str(v).replace("\\", "\\\\").replace('"', '\\"')


def line(name, labels, value):
    lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items()) if labels else ""
    return f"{name}{{{lab}}} {value}"


def run_metrics(lab, runs):
    """lab_run_last_success / lab_run_last_started_seconds per run mode, lab_tests_last_passed / failed from the most recent
    run that executed the Robot suites (runs: RunRegistry.list(), newest first)."""
    out = ["# HELP lab_run_last_success 1 if the most recent run of the mode succeeded", "# TYPE lab_run_last_success gauge",
           "# HELP lab_run_last_started_seconds When the most recent run of the mode started", "# TYPE lab_run_last_started_seconds gauge",
           "# HELP lab_tests_last_passed Passed tests of the most recent run that ran the suites", "# TYPE lab_tests_last_passed gauge",
           "# HELP lab_tests_last_failed Failed tests of that run", "# TYPE lab_tests_last_failed gauge"]
    seen = set(); tested = False
    for r in runs:
        if r["mode"] not in seen and r["status"] not in ("running", "queued"):
            seen.add(r["mode"]); out += [line("lab_run_last_success", {"lab": lab, "mode": r["mode"]}, int(r["status"] == "success")), line("lab_run_last_started_seconds", {"lab": lab, "mode": r["mode"]}, int(r["started"]))]
        if not tested and r.get("tests"):
            tested = True; out += [line("lab_tests_last_passed", {"lab": lab}, r["tests"]["passed"]), line("lab_tests_last_failed", {"lab": lab}, r["tests"]["failed"])]
    return out


def exposition(lines):
    return "\n".join(lines) + "\n"


def generated(lab):
    return ["# HELP lab_metrics_generated_seconds When the portal answered this scrape", "# TYPE lab_metrics_generated_seconds gauge", line("lab_metrics_generated_seconds", {"lab": lab}, int(time.time()))]
