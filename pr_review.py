#!/usr/bin/env python3

# LLM review Pull Request
#
# Requirements:
# - OLLAMA server + model
# - BitBucket token with permissions
#   Manage account / HTTP access tokens
# - Cron job that runs this script
#   /etc/cron.d/pr_review:
#   MAILTO=it-info@example.com
#   0 */2 * * * root /opt/pr_review.py

import requests
import re
import sys
import textwrap
from datetime import datetime, timedelta

BITBUCKET_API_TOKEN = "bitbucket-token"
JIRA_API_TOKEN = "jira-token"

## All PR
try:
    response = requests.get("https://stash.in.example.com/rest/api/latest/projects/IT/repos/ansible/pull-requests", headers={"Authorization": f"Bearer {BITBUCKET_API_TOKEN}", "Accept": "application/json;charset=UTF-8"}, params={"state": "OPEN", "limit": 99}, timeout=10,)
    response.raise_for_status()
    page = response.json()
    values = page.get("values") or []
    if not values:
        print(f"No open pull requests.")
        sys.exit()
except BaseException as error:
    print(f'Get pull requests ERROR: {error}', file=sys.stderr)
    sys.exit(1)

TWO_HOURS_AGO = datetime.now() - timedelta(hours=2)
RECENT_PRS = []
RECENT_PRS = [{"id": pr["id"], "title": pr["title"]} for pr in response.json()["values"] if datetime.fromtimestamp(pr["createdDate"] / 1000) > TWO_HOURS_AGO]

# PR diff
for pr in RECENT_PRS:
    BITBUCKET_PR_ID = pr.get("id")
    if not BITBUCKET_PR_ID:
        continue
    BITBUCKET_PR_TITLE = pr.get("title") or ""
    BITBUCKET_PR_DESCRIPTION = pr.get("description") or ""
    # Reset per-PR state to avoid leaking Jira info between PRs
    ISSUE_SUMMARY = None
    # PR target (base) ref – use this to read the "original" files
    BASE_REF = ((pr.get("toRef") or {}).get("id")) or ""
    if not BASE_REF:
        try:
            d = requests.get(f"https://stash.in.example.com/rest/api/latest/projects/IT/repos/ansible/pull-requests/{BITBUCKET_PR_ID}", headers={"Authorization": f"Bearer {BITBUCKET_API_TOKEN}"}, timeout=20,)
            BASE_REF = (d.json().get("toRef") or {}).get("id") or ""
            d.raise_for_status()
            if not BASE_REF:
                continue
        except BaseException as error:
            print(f'Get pull request ref ERROR: {error}', file=sys.stderr)
            continue
    
    # Full unified diff for the PR
    try:
        response = requests.get(f"https://stash.in.example.com/rest/api/latest/projects/IT/repos/ansible/pull-requests/{BITBUCKET_PR_ID}.diff", headers={"Authorization": f"Bearer {BITBUCKET_API_TOKEN}"}, params={"contextLines": 5}, timeout=60,)
        response.raise_for_status()
        GIT_DIFF = response.text or ""
        if not GIT_DIFF.strip():
            continue
    except BaseException as error:
        print(f'Get PR git diff ERROR: {error}', file=sys.stderr)
        continue
    
    # Find associated Jira issue
    jira_issues = []
    try:
        ADMIN_PATTERN = re.compile(r'\bADMIN-\d+\b')
        # Primary ADMIN-* issue from PR title/description
        JIRA_ISSUE_ID = next((m.group(0) for text in (BITBUCKET_PR_TITLE, BITBUCKET_PR_DESCRIPTION) for m in [ADMIN_PATTERN.search(text or "")] if m), None,)
        if JIRA_ISSUE_ID:
            seen = set()
            queue = [JIRA_ISSUE_ID]
            # Collect primary + mentioned + linked ADMIN-* issues
            while queue:
                key = queue.pop(0)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    resp = requests.get(f"https://jira.in.example.com/rest/api/latest/issue/{key}", headers={"Authorization": f"Bearer {JIRA_API_TOKEN}"}, timeout=20,)
                    resp.raise_for_status()
                except BaseException as error:
                    print(f'Get Jira issue ERROR: {error}', file=sys.stderr)
                    continue
                
                issue = resp.json()
                fields = issue.get("fields") or {}
                summary = fields.get("summary") or ""
                rendered = issue.get("renderedFields") or {}
                description = rendered.get("description") or fields.get("description") or ""
                if isinstance(description, dict):  # Jira ADF sometimes comes back as dict
                    description = "[non-text description]"
                jira_issues.append((key, summary, str(description)))
                text_block = f"{summary} {description}"
                for m in ADMIN_PATTERN.finditer(text_block):
                    if m.group(0) not in seen:
                        queue.append(m.group(0))
                for link in fields.get("issuelinks") or []:
                    for side in ("inwardIssue", "outwardIssue"):
                        linked = link.get(side) or {}
                        linked_key = linked.get("key")
                        if linked_key and ADMIN_PATTERN.fullmatch(linked_key) and linked_key not in seen:
                            queue.append(linked_key)
    except Exception:
        jira_issues = []
    
    # Environment/policy block here (before Jira context)
    PREFIX_LIMITATIONS = textwrap.dedent("""
    ENVIRONMENT FACTS (treat as hard constraints)
    - This repo configures internal Linux hosts managed by sysadmins (manual runs) and AWX (scheduled templates).
    - Supported OS: the latest 3 supported LTS/stable releases of Ubuntu, Debian, AlmaLinux, Oracle Linux (systemd everywhere).
    - Ansible: currently supported Ansible Core; modules must use Fully Qualified Collection Names (FQCN).
    - Allowed collections in this repo:
      community.zabbix, community.crypto, community.mysql, community.general, community.postgresql,
      community.docker, community.proxmox, ansible.posix, awx.awx, plus ansible.builtin.
    - Hosts generally have Internet access:
      - OS packages must come via mirror.in.example.com (apt/yum/dnf proxy/mirror).
      - Artifacts/binaries must come from nexus.in.example.com.
      - Any external URL usage is suspicious and should be flagged unless explicitly justified in Jira.
    - Typical execution:
      - CLI: ansible-playbook playbook.yml -l <host_or_group> (partial rollout is common).
      - AWX: job templates/schedules (non-interactive; must be safe unattended).
    
    
    REPO CONVENTIONS (use when evaluating correctness)
    - Many top-level *.yml files are standalone playbooks (not role snippets).
    - Shared logic lives under roles/, variables under group_vars/ and host_vars/, inventory under inventory/.
    - If a PR changes a role task file, review its defaults/vars/handlers/meta for consistency (new vars, handler names).
    - Ansible-lint runs in TeamCity for each PR; assume lint will catch style, but still flag semantic/operational issues.
    
    
    OPERATIONAL INVARIANTS (flag violations as issues)
    - Must be safe under partial rollout (-l): avoid assumptions that “all hosts are updated at once”.
    - Idempotency: avoid tasks that always change; prefer modules over shell/command; guard command tasks.
    - Restarts/reloads must be handler-based; avoid unconditional restarts (especially for sshd, sudo, network, proxy, DBs).
    - Multi-distro correctness: package/service names, paths, and repo config must be conditional on facts
      (ansible_facts['os_family'], distribution, major version).
    - Package sources: apt/yum repo configuration must point to mirror.in.example.com (not public repos).
    - Artifacts: get_url/unarchive/docker_image/etc must use nexus.in.example.com when pulling binaries/images.
    - Secrets must not be logged (use no_log where needed).
    - AWX safety: no interactive prompts; pause only if explicitly required by Jira and guarded.
    """).strip() + "\n\n\n"
    
    # Build Jira context prefix
    if jira_issues:
        lines = ["Jira context for requirements:"]
        for i, (k, s, dsc) in enumerate(jira_issues):
            dsc = (dsc[:2000] + "\n...[truncated]...") if len(dsc) > 2000 else dsc
            lines += [f"{'Primary' if i == 0 else 'Related'}: {k}", f"Summary: {s}", f"Description: {dsc}", ""]
        PREFIX_JIRA = "\n".join(lines).strip() + "\n\n"
        PREFIX_COMBINED = PREFIX_LIMITATIONS + PREFIX_JIRA
    
    # LLM review of the diff
    LLM_REQUEST = textwrap.dedent("""
    Review this Ansible pull request.
    
    Given ORIGINAL FILE (from target branch) + UNIFIED DIFF (PR changes), list ONLY detected issues:
    - syntax/errors
    - incompatibilities
    - unused/unknown vars or waste code
    - changes that do NOT match Jira requirements, or missing required changes
    
    Output format:
    - If none: "No issues found."
    - Else: bullets with title, short description, and location (file + approx line/snippet).
    """).strip() + "\n\n\n"
    
    parts = [PREFIX_COMBINED + LLM_REQUEST]
    
    raw_cache = {}  # path@ref -> text (per PR)
    roles_touched = set()
    
    # split diff into per-file, fetch originals via /raw
    for block in [b for b in re.split(r"(?m)^(?=diff --git )", GIT_DIFF) if b.strip()]:
        old_m = re.search(r"^---\s+(.+)$", block, re.M)
        new_m = re.search(r"^\+\+\+\s+(.+)$", block, re.M)
        old_raw = (old_m.group(1).strip() if old_m else "")
        new_raw = (new_m.group(1).strip() if new_m else "")
        old_path = "" if old_raw.endswith("/dev/null") else re.sub(r"^(?:src|dst)://", "", re.sub(r"^(?:a/|b/)", "", old_raw))
        new_path = "" if new_raw.endswith("/dev/null") else re.sub(r"^(?:src|dst)://", "", re.sub(r"^(?:a/|b/)", "", new_raw))
        display_path = new_path or old_path or "(unknown path)"
        orig_path = old_path  # base branch usually has old path (renames)
        
        mrole = re.match(r"^roles/([^/]+)/", display_path)
        if mrole:
            roles_touched.add(mrole.group(1))
        
        orig = ""
        if orig_path:
            ck = f"{orig_path}@{BASE_REF}"
            if ck in raw_cache:
                orig = raw_cache[ck]
            else:
                rr = requests.get(f"https://stash.in.example.com/rest/api/latest/projects/IT/repos/ansible/raw/{orig_path}", headers={"Authorization": f"Bearer {BITBUCKET_API_TOKEN}"}, params={"at": BASE_REF}, timeout=30,)
                orig = rr.text if rr.status_code == 200 else f"[could not load original: HTTP {rr.status_code}]"
                raw_cache[ck] = orig
                
        # deterministic truncation (avoid ctx blowups)
        if len(orig) > 12000:
            orig = orig[:6000] + "\n...[truncated]...\n" + orig[-6000:]
        blk = block if len(block) <= 12000 else (block[:6000] + "\n...[truncated]...\n" + block[-6000:])
        parts.append(
            f"=== FILE: {display_path} ===\n"
            f"[ORIGINAL FILE @ {BASE_REF} | path={orig_path or '(new file)'}]\n{orig}\n\n"
            f"[UNIFIED DIFF]\n{blk}\n"
        )
    
    if roles_touched:
        roles_sorted = sorted(list(roles_touched))[:8]
        parts.append(
            "=== ROLE CONTEXT (from target branch) ===\n"
            "The following files are included to help resolve variables/handlers/meta referenced by role task changes.\n"
            f"Role set (capped to 8): {', '.join(roles_sorted)}\n"
        )
        
        for role in roles_sorted:
            parts.append(f"--- ROLE: {role} (ref {BASE_REF}) ---")
            for subdir in ("defaults", "vars", "handlers", "meta"):
                dir_path = f"roles/{role}/{subdir}"
                start = 0
                found_any = False
                collected = 0
                while True:
                    br = requests.get(                        f"https://stash.in.example.com/rest/api/latest/projects/IT/repos/ansible/browse/{dir_path}", headers={"Authorization": f"Bearer {BITBUCKET_API_TOKEN}"}, params={"at": BASE_REF, "limit": 200, "start": start}, timeout=20,)
                    if br.status_code != 200:
                        break
                    
                    j = br.json() or {}
                    children = (j.get("children") or {})
                    values = children.get("values") or []
                    if not values:
                        break  # not a dir or empty dir
                    
                    for child in values:
                        if child.get("type") != "FILE":
                            continue
                        comps = ((child.get("path") or {}).get("components")) or []
                        if not comps:
                            continue
                        name = comps[-1].lower()
                        if not name.endswith((".yml", ".yaml")):
                            continue
                        file_path = "/".join(comps)
                        found_any = True
                        ck = f"{file_path}@{BASE_REF}"
                        if ck in raw_cache:
                            txt = raw_cache[ck]
                        else:
                            rr = requests.get(
                                f"https://stash.in.example.com/rest/api/latest/projects/IT/repos/ansible/raw/{file_path}", headers={"Authorization": f"Bearer {BITBUCKET_API_TOKEN}"}, params={"at": BASE_REF}, timeout=30,)
                            txt = rr.text if rr.status_code == 200 else f"[could not load: HTTP {rr.status_code}]"
                            raw_cache[ck] = txt
                        
                        if len(txt) > 12000:
                            txt = txt[:6000] + "\n...[truncated]...\n" + txt[-6000:]
                        
                        parts.append(f"[ROLE FILE] {file_path}\n{txt}\n")
                        collected += 1
                        if collected >= 30:
                            parts.append(f"[ROLE FILES NOTE] Capped: only first 30 YAML files from {dir_path}\n")
                            break
                    
                    if collected >= 30:
                        break
                    
                    if children.get("isLastPage", True):
                        break
                    start = children.get("nextPageStart", start + 200)
                
                if not found_any:
                    parts.append(f"[ROLE DIR] {dir_path}: (missing or empty)\n")
    
    LLM_REQUEST = "\n\n".join(parts)
    if len(LLM_REQUEST) > 180000:
        LLM_REQUEST = LLM_REQUEST[:180000] + "\n...[hard truncated to fit context]...\n"
    data = {"model": "deepseek-r1:14b", "stream": False, "messages": [{ "role": "user", "content": f"{LLM_REQUEST}" }], "options": { "num_ctx": 20480 }}
    try:
        response = requests.post("http://ollama-test.example.com:11434/api/chat", json=data, headers={"Content-Type": "application/json"})
        response.raise_for_status()
        content = (((response.json() or {}).get("message") or {}).get("content") or "").strip()
    except BaseException as error:
        print(f'Get LLM response ERROR: {error}', file=sys.stderr)
        continue
    
    if not content:
        continue
    content = re.sub(r"<think>.*?</think>\n?", "", content, flags=re.DOTALL).strip()
    # optional: avoid noise if no issues
    if content.strip() == "No issues found.":
        continue
    comment = f"AI-generated review (may be incorrect):\n\n{content}"
    if len(comment) > 30000:
        comment = comment[:30000] + "\n...[truncated]...\n"
    # PR comment
    try:
        response = requests.post(f"https://stash.in.example.com/rest/api/latest/projects/IT/repos/ansible/pull-requests/{BITBUCKET_PR_ID}/comments", json={"text": comment}, headers={"Authorization": f"Bearer {BITBUCKET_API_TOKEN}", "Accept": "application/json;charset=UTF-8", "Content-Type": "application/json"})
        response.raise_for_status()
    except BaseException as error:
        print(f'Post BitBucket comment ERROR: {error}', file=sys.stderr)
        continue


# References:
# Time to run: 
# - deepseek-r1:14b(9Gb): 8CPU,26GBRAM: 8min per PR
# - gemma3:12b(8Gb): 8CPU,36GBRAM: too long
# Cron script runs every 2 hours. Review opened Pull Request of the last 2 hours.
# https://docs.atlassian.com/bitbucket-server/rest/5.16.0/bitbucket-rest.html#idm8297336928
# https://docs.atlassian.com/software/jira/docs/api/REST/9.10.0/
# https://ollama.com/library/deepseek-r1:14b
