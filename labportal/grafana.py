"""Grafana annotations from the labs: a run starting / finishing, a failover cut in a test, a steering change — so the
dashboards explain their own dips. GRAFANA_URL / GRAFANA_USER / GRAFANA_PASSWORD (default: the NMS, admin / admin);
failures are logged and swallowed — telemetry never breaks a run."""
import logging, os, time
import requests

URL = os.environ.get("GRAFANA_URL", "http://10.0.0.10:3001"); AUTH = (os.environ.get("GRAFANA_USER", "admin"), os.environ.get("GRAFANA_PASSWORD", "admin"))
log = logging.getLogger("labportal.grafana")


def annotate(text, tags=(), start=None, end=None):
    """One annotation (a point, or a region when `end` is given); returns its id or None. Timestamps in seconds."""
    body = {"text": text, "tags": list(tags), "time": int((start or time.time()) * 1000)}
    if end: body["timeEnd"] = int(end * 1000)
    try:
        r = requests.post(f"{URL}/api/annotations", json=body, auth=AUTH, timeout=5); r.raise_for_status(); return r.json().get("id")
    except Exception as e:  # noqa: BLE001
        log.warning("grafana annotation failed: %s", e); return None


def update(ann_id, text=None, end=None, tags=None):
    """Turn a point into a region (set `end`) or change the text / tags of an existing annotation."""
    if ann_id is None: return
    body = {}
    if text is not None: body["text"] = text
    if end is not None: body["timeEnd"] = int(end * 1000)
    if tags is not None: body["tags"] = list(tags)
    try: requests.patch(f"{URL}/api/annotations/{ann_id}", json=body, auth=AUTH, timeout=5).raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("grafana annotation update failed: %s", e)


def delete(ann_id):
    if ann_id is None: return
    try: requests.delete(f"{URL}/api/annotations/{ann_id}", auth=AUTH, timeout=5)
    except Exception: pass  # noqa: BLE001
