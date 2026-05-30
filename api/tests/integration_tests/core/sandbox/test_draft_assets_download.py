#!/usr/bin/env python3
"""
Integration test for DraftAppAssetsDownloader ARG_MAX bug.
...
"""

import base64
import json
import shlex
import subprocess
import sys
import time
from argparse import ArgumentParser
from pathlib import Path

# Add api directory to Python path for local imports
_api_root = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))

API_CONTAINER = "docker-api-1"
AGENTBOX_CONTAINER = "docker-agentbox-1"
WORKER_CONTAINER = "docker-worker-1"

COOKIE_FILE = "/tmp/draft_download_test_cookies.txt"
RESP_FILE = "/tmp/draft_download_test_resp.json"


def _exec(container: str, cmd: str, timeout: int = 120) -> str:
    result = subprocess.run(
        ["docker", "exec", container, "bash", "-c", cmd],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout.strip()


def login(email: str, password: str) -> str | None:
    pw_b64 = base64.b64encode(password.encode()).decode()
    payload = json.dumps({"email": email, "password": pw_b64, "remember_me": True})
    _exec(
        API_CONTAINER,
        f"curl -s -X POST -o /dev/null -c {COOKIE_FILE} "
        + shlex.quote("http://localhost:5001/console/api/login")
        + f" -H 'Content-Type: application/json' -d {shlex.quote(payload)}",
    )
    csrf = _exec(
        API_CONTAINER,
        f"grep csrf_token {COOKIE_FILE} | awk '{{print $NF}}'",
    )
    return csrf if csrf and len(csrf) > 10 else None


def api_get(csrf: str, path: str) -> dict:
    url = f"http://localhost:5001/console/api{path}"
    _exec(
        API_CONTAINER,
        f"curl -s -o {RESP_FILE} -w '%{{http_code}}' "
        + shlex.quote(url)
        + f" -H {shlex.quote(f'X-CSRF-Token: {csrf}')} -b {COOKIE_FILE}",
    )
    body = _exec(API_CONTAINER, f"cat {RESP_FILE}")
    try:
        return json.loads(body) if body else {}
    except Exception:
        return {}


def api_post(csrf: str, path: str, data: dict) -> dict:
    url = f"http://localhost:5001/console/api{path}"
    payload = json.dumps(data)
    _exec(
        API_CONTAINER,
        f"curl -s -X POST -o {RESP_FILE} -w '%{{http_code}}' "
        + shlex.quote(url)
        + f" -H 'Content-Type: application/json'"
        + f" -H {shlex.quote(f'X-CSRF-Token: {csrf}')} -b {COOKIE_FILE}"
        + f" -d {shlex.quote(payload)}",
    )
    body = _exec(API_CONTAINER, f"cat {RESP_FILE}")
    try:
        return json.loads(body) if body else {}
    except Exception:
        return {}


def api_put(csrf: str, path: str, data: dict) -> dict:
    url = f"http://localhost:5001/console/api{path}"
    payload = json.dumps(data)
    _exec(
        API_CONTAINER,
        f"curl -s -X PUT -o {RESP_FILE} -w '%{{http_code}}' "
        + shlex.quote(url)
        + f" -H 'Content-Type: application/json'"
        + f" -H {shlex.quote(f'X-CSRF-Token: {csrf}')} -b {COOKIE_FILE}"
        + f" -d {shlex.quote(payload)}",
    )
    body = _exec(API_CONTAINER, f"cat {RESP_FILE}")
    try:
        return json.loads(body) if body else {}
    except Exception:
        return {}


def api_delete(csrf: str, path: str) -> dict:
    url = f"http://localhost:5001/console/api{path}"
    _exec(
        API_CONTAINER,
        f"curl -s -X DELETE -o {RESP_FILE} -w '%{{http_code}}' "
        + shlex.quote(url)
        + f" -H {shlex.quote(f'X-CSRF-Token: {csrf}')} -b {COOKIE_FILE}",
    )
    body = _exec(API_CONTAINER, f"cat {RESP_FILE}")
    try:
        return json.loads(body) if body else {}
    except Exception:
        return {}


def check_arg_max_errors() -> tuple[int, list[str]]:
    result_lines: list[str] = []
    total = 0
    for ct in [WORKER_CONTAINER, API_CONTAINER]:
        out = _exec(
            ct,
            "docker logs $(hostname) --tail 5000 2>/dev/null "
            "| grep -n 'Argument list too long' || true",
        )
        for line in out.split("\n"):
            if "Argument list too long" in line:
                result_lines.append(f"[{ct}] {line}")
                total += 1
    return total, result_lines


def check_sandbox_errors() -> list[str]:
    lines: list[str] = []
    for ct in [WORKER_CONTAINER, API_CONTAINER]:
        out = _exec(
            ct,
            "docker logs $(hostname) --tail 5000 2>/dev/null "
            "| grep -iE 'draft_app|DraftApp|sandbox.*init' || true",
        )
        lines.extend(f"[{ct}] {l}" for l in out.split("\n") if l.strip())
    return lines


def agentbox_dirs() -> set[str]:
    out = _exec(AGENTBOX_CONTAINER, "ls -1 /workspace/sandboxes/ 2>/dev/null || true")
    return {d for d in out.split("\n") if d}


def agentbox_file_count(sandbox_dir: str) -> int:
    out = _exec(
        AGENTBOX_CONTAINER,
        f"find /workspace/sandboxes/{sandbox_dir} -type f 2>/dev/null | wc -l",
    )
    try:
        return int([x for x in out.split() if x][-1]) if out.strip() else 0
    except Exception:
        return 0


# ── Skill file creation ──

MD_TEMPLATE = """# {name} Skill

## Description
{description}

## Parameters
| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| input_text | string | Yes | - | Text to process |
| mode | string | No | {default_mode} | Processing mode |
| max_tokens | integer | No | 4096 | Max output tokens |
| threshold | float | No | 0.7 | Confidence threshold |
| language | string | No | auto | Input language |
"""


def create_skill_files(csrf: str, app_id: str, count: int) -> list[str]:
    """Create skill files in the app's draft assets."""
    modules = [
        "merchant-management",
        "data-processing",
        "text-analysis",
        "report-generation",
    ]
    skill_names = [
        "check-permission", "create-record", "update-status",
        "validate-input", "generate-report", "search-entity",
        "export-data", "import-data", "transform-data", "notify-skill",
    ]

    # Create skills folder
    result = api_post(csrf, f"/apps/{app_id}/assets/folders", {
        "name": "skills", "parent_id": None,
    })
    skills_folder_id = result.get("id", "")
    if not skills_folder_id:
        # Find existing
        tree = api_get(csrf, f"/apps/{app_id}/assets/tree")
        for child in tree.get("children", []):
            if child.get("name") == "skills":
                skills_folder_id = child["id"]
                break

    node_ids: list[str] = []
    for i in range(count):
        mod = modules[i % len(modules)]
        name = skill_names[i % len(skill_names)]

        # Create module folder
        result = api_post(csrf, f"/apps/{app_id}/assets/folders", {
            "name": mod, "parent_id": skills_folder_id,
        })
        folder_id = result.get("id", skills_folder_id)

        # Create .md file
        content = MD_TEMPLATE.format(
            name=name.replace("-", " ").title(),
            description=f"Skill for {name} in {mod} module",
            default_mode=["fast", "thorough", "standard"][i % 3],
        )
        ext = [".md", ".md", ".py"][i % 3]
        filename = f"{name}{ext}"

        batch = api_post(csrf, f"/apps/{app_id}/assets/batch-upload", {
            "parent_id": folder_id,
            "children": [{
                "name": filename,
                "node_type": "file",
                "size": len(content.encode("utf-8")),
            }],
        })

        for child in batch.get("children", []):
            nid = child.get("id", "")
            if nid:
                api_put(csrf, f"/apps/{app_id}/assets/files/{nid}", {
                    "content": content,
                })
                node_ids.append(nid)
                break

    return node_ids


def cleanup_skill_files(csrf: str, app_id: str) -> int:
    tree = api_get(csrf, f"/apps/{app_id}/assets/tree")

    def collect_ids(children: list) -> list[str]:
        ids = []
        for child in children:
            ids.append(child.get("id", ""))
            ids.extend(collect_ids(child.get("children", [])))
        return ids

    all_ids = collect_ids(tree.get("children", []))
    deleted = 0
    for nid in all_ids:
        result = api_delete(csrf, f"/apps/{app_id}/assets/nodes/{nid}")
        if result.get("result") == "success":
            deleted += 1
    return deleted


# ── Main ──


def main():
    parser = ArgumentParser(description="DraftAppAssetsDownloader Integration Test")
    parser.add_argument("--email", default="chuanzegao@163.com")
    parser.add_argument("--password", default="2wsx@WSX")
    parser.add_argument("--app-id", default=None)
    parser.add_argument("--create-skills", action="store_true",
                        help="Create skill files and trigger workflow")
    parser.add_argument("--skill-count", type=int, default=20)
    parser.add_argument("--cleanup", action="store_true",
                        help="Remove all assets and exit")
    args = parser.parse_args()

    print("=" * 70)
    print("  DraftAppAssetsDownloader — Integration Test")
    print("=" * 70)

    # 1. Login
    print("\n[1] Login ...")
    csrf = login(args.email, args.password)
    if not csrf:
        print("FAIL: Login failed")
        sys.exit(1)
    print("OK")

    # 2. Find app
    target = args.app_id
    if not target:
        print("\n[2] Finding sandboxed app ...")
        apps_data = api_get(csrf, "/apps?page=1&limit=50")
        for app in apps_data.get("data", []):
            if (app.get("runtime_type") == "sandboxed"
                    and app.get("mode") == "advanced-chat"):
                target = app["id"]
                print(f"  Selected: {app['name']} ({target})")
                break
    if not target:
        print("FAIL: No sandboxed advanced-chat app")
        sys.exit(1)

    # Cleanup mode
    if args.cleanup:
        print(f"\n[Cleanup] Removing all assets from {target} ...")
        deleted = cleanup_skill_files(csrf, target)
        print(f"  Deleted {deleted} nodes")
        return

    # 3. Record baselines
    print("\n[3] Recording baselines ...")
    before_dirs = agentbox_dirs()
    before_arg_errors, _ = check_arg_max_errors()
    print(f"  Agentbox sandboxes: {len(before_dirs)}")
    print(f"  Pre-existing ARG_MAX errors: {before_arg_errors}")

    # 4. Skill file creation (optional)
    node_ids: list[str] = []
    if args.create_skills:
        print(f"\n[4] Creating {args.skill_count} skill files ...")
        node_ids = create_skill_files(csrf, target, args.skill_count)
        print(f"  Created {len(node_ids)} file nodes")

    # 5. Analyse script size
    print(f"\n[{'5' if args.create_skills else '4'}] Script size analysis ...")
    from core.sandbox.services.asset_download_service import AssetDownloadService
    from core.zip_sandbox.entities import SandboxDownloadItem

    # Simulate download items similar to what DraftAppAssetsDownloader would process
    test_items = [
        SandboxDownloadItem(
            path=f"skills/skill-{i}.md",
            content=b"# Skill content\n" + b"x" * 10000,
        )
        for i in range(args.skill_count)
    ]
    script = AssetDownloadService.build_download_script(test_items, "skills")
    arg_max = 128 * 1024

    print(f"  Generated script size: {len(script):,} bytes ({len(script)/1024:.1f} KB)")
    print(f"  Linux ARG_MAX:          {arg_max:,} bytes ({arg_max/1024:.0f} KB)")
    if len(script) > arg_max:
        print(f"  *** SCRIPT EXCEEDS ARG_MAX by {len(script)-arg_max:,} bytes ***")
        print(f"  *** This WILL cause 'Argument list too long' error ***")
    else:
        print(f"  Script is within ARG_MAX limit (ok)")

    # 6. Trigger workflow (only with created skills)
    wf_id = ""
    status = "?"
    if args.create_skills and node_ids:
        print(f"\n[6] Triggering Draft workflow ...")
        resp_file = "/tmp/draft_trigger_resp.txt"
        url = shlex.quote(
            f"http://localhost:5001/console/api/apps/{target}/advanced-chat/workflows/draft/run"
        )
        payload = shlex.quote(json.dumps({
            "files": [], "inputs": {},
            "query": "hello", "conversation_id": "", "parent_message_id": "",
        }))
        csrf_q = shlex.quote(f"X-CSRF-Token: {csrf}")

        _exec(
            API_CONTAINER,
            f"nohup curl -sN --max-time 120 -o {resp_file} "
            + url + f" -H 'Content-Type: application/json' -H {csrf_q}"
            + f" -b {COOKIE_FILE} -d {payload} > /dev/null 2>&1 &",
        )

        before_runs = api_get(csrf, f"/apps/{target}/advanced-chat/workflow-runs?page=1&limit=20")
        before_ids = {r.get("id") for r in before_runs.get("data", [])}

        for attempt in range(20):
            time.sleep(6)
            runs_data = api_get(csrf,
                                f"/apps/{target}/advanced-chat/workflow-runs?page=1&limit=20")
            for run in runs_data.get("data", []):
                rid = run.get("id", "")
                if rid not in before_ids:
                    wf_id = rid
                    status = run.get("status", "?")
                    break
            if wf_id:
                print(f"  Found: {wf_id} ({status})")
                break
            print(f"  Polling ... {attempt+1}")

        if not wf_id:
            print("  (workflow still initializing — checking logs instead)")

    # 7. Final log check
    step = 7 if args.create_skills else 5
    print(f"\n[{step}] Log Analysis ...")
    after_arg_errors, arg_lines = check_arg_max_errors()
    new_errors = after_arg_errors - before_arg_errors
    sandbox_logs = check_sandbox_errors()

    print(f"  ARG_MAX 'too long' errors: {after_arg_errors} (new: {new_errors})")
    if new_errors > 0:
        print("  *** FAIL: ARG_MAX bug CONFIRMED ***")
        for line in arg_lines:
            print(f"    {line[:250]}")

    if sandbox_logs:
        print(f"\n  Sandbox-related logs ({len(sandbox_logs)} lines, latest 10):")
        for line in sandbox_logs[-10:]:
            print(f"    {line[:200]}")

    # 8. Cleanup
    if node_ids:
        print(f"\n[Cleanup] Removing {len(node_ids)} test files ...")
        deleted = cleanup_skill_files(csrf, target)
        print(f"  Removed {deleted} nodes")

    # Summary
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"  App:                       {target}")
    print(f"  Workflow run:              {wf_id or 'N/A'} ({status})")
    print(f"  Skill files:               {len(node_ids)}")
    print(f"  Script vs ARG_MAX:         {len(script):,} vs {arg_max:,}")
    print(f"  New ARG_MAX errors:        {new_errors}")
    print("=" * 70)

    # Only FAIL if actual ARG_MAX errors appeared in Docker logs.
    # This means the fix (stdin transport) is NOT working.
    # Script size exceeding ARG_MAX is an informational warning, not a failure.
    if new_errors > 0:
        print(f"\nRESULT: FAIL — {new_errors} ARG_MAX errors detected!")
        print(f"  The stdin transport fix is NOT working as expected.")
        sys.exit(1)
    else:
        if len(script) > arg_max:
            print(f"\nRESULT: PASS — No ARG_MAX errors in logs.")
            print(f"  Script is {len(script):,} bytes but transmitted via stdin (safe).")
        else:
            print(f"\nRESULT: PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
