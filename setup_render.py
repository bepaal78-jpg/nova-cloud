"""
NOVA Cloud — Render.com automatisches Setup
Liest alle Keys aus ../.env, kein interaktiver Input nötig.
"""

import os, sys, json, subprocess, urllib.request, urllib.error, re

RENDER_API    = "https://api.render.com/v1"
GITHUB_REPO   = "bepaal78-jpg/nova-cloud"
SERVICE_NAME  = "nova-cloud"

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; B = "\033[96m"; N = "\033[0m"

# ── .env lesen ────────────────────────────────────────────────────────────────

def load_env():
    env = {}
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env

# ── HTTP helper ───────────────────────────────────────────────────────────────

def api(method, path, data=None, key=""):
    url = f"{RENDER_API}{path}"
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {}, r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        try:    return json.loads(raw), e.code
        except: return {"error": raw}, e.code

# ── GitHub Secret ─────────────────────────────────────────────────────────────

def set_github_secret(name, value):
    r = subprocess.run(
        ["gh", "secret", "set", name, "--repo", GITHUB_REPO, "--body", value],
        capture_output=True, text=True
    )
    return r.returncode == 0

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{B}{'='*55}\n  NOVA Cloud — Render Setup\n{'='*55}{N}\n")

    env = load_env()
    api_key  = env.get("RENDER_API_KEY", "")
    owner_id = env.get("RENDER_OWNER_ID", "")
    groq_key = env.get("GROQ_API_KEY", "")
    gem_key  = env.get("GEMINI_API_KEY", "")

    if not api_key:
        print(f"{R}RENDER_API_KEY fehlt in .env!{N}")
        sys.exit(1)

    print(f"{G}API Key: {api_key[:12]}...{N}")
    print(f"{G}Owner:   {owner_id}{N}")
    print(f"{G}Groq:    {groq_key[:20]}...{N}")

    # ── Service suchen oder erstellen ─────────────────────────────────────────
    print(f"\n{Y}Suche vorhandenen Service...{N}")
    svcs, status = api("GET", f"/services?name={SERVICE_NAME}&type=web_service", key=api_key)

    existing_id  = None
    existing_url = None

    if status == 200 and isinstance(svcs, list) and svcs:
        existing_id  = svcs[0]["service"]["id"]
        existing_url = svcs[0]["service"]["serviceDetails"].get("url", "")
        print(f"{G}Service gefunden: {existing_id}{N}")
        print(f"{G}URL: {existing_url}{N}")

    if not existing_id:
        print(f"{Y}Erstelle neuen Service...{N}")
        env_vars = [
            {"key": "GROQ_API_KEY",   "value": groq_key},
            {"key": "GEMINI_API_KEY", "value": gem_key},
            {"key": "PYTHON_VERSION", "value": "3.11.0"},
        ]
        build_cmd = "pip install -r requirements.txt"
        start_cmd = "uvicorn main:app --host 0.0.0.0 --port $PORT"
        payload = {
            "autoDeploy": "yes",
            "branch": "master",
            "name": SERVICE_NAME,
            "ownerId": owner_id,
            "repo": f"https://github.com/{GITHUB_REPO}",
            "rootDir": "",
            "type": "web_service",
            "serviceDetails": {
                "env": "python",
                "plan": "free",
                "pullRequestPreviewsEnabled": "no",
                "region": "frankfurt",
                "buildCommand": build_cmd,
                "startCommand": start_cmd,
                "envVars": env_vars,
                "envSpecificDetails": {
                    "buildCommand": build_cmd,
                    "startCommand": start_cmd,
                },
            },
        }
        resp, status = api("POST", "/services", data=payload, key=api_key)
        if status in (200, 201):
            existing_id  = resp["service"]["id"]
            existing_url = resp["service"]["serviceDetails"].get("url", f"https://{SERVICE_NAME}.onrender.com")
            print(f"{G}Service erstellt: {existing_id}{N}")
            print(f"{G}URL: {existing_url}{N}")
        else:
            print(f"{R}Fehler beim Erstellen (HTTP {status}):{N}")
            print(json.dumps(resp, indent=2)[:500])
            sys.exit(1)

    svc_url = existing_url or f"https://{SERVICE_NAME}.onrender.com"

    # ── Deploy Hook ───────────────────────────────────────────────────────────
    print(f"\n{Y}Hole Deploy Hook...{N}")
    hooks, status = api("GET", f"/services/{existing_id}/deploy-hooks", key=api_key)
    hook_url = ""

    if status == 200 and isinstance(hooks, list) and hooks:
        hook_url = hooks[0].get("url", "")
        print(f"{G}Deploy Hook gefunden{N}")
    else:
        hook, status = api("POST", f"/services/{existing_id}/deploy-hooks",
                          data={"name": "github-actions"}, key=api_key)
        if status in (200, 201):
            hook_url = hook.get("url", "")
            print(f"{G}Deploy Hook erstellt{N}")
        else:
            print(f"{Y}Deploy Hook konnte nicht erstellt werden (HTTP {status}){N}")

    if hook_url:
        print(f"{Y}Setze GitHub Secret RENDER_DEPLOY_HOOK_URL...{N}")
        if set_github_secret("RENDER_DEPLOY_HOOK_URL", hook_url):
            print(f"{G}GitHub Secret gesetzt ✓{N}")
        else:
            print(f"{Y}Manuell: gh secret set RENDER_DEPLOY_HOOK_URL --repo {GITHUB_REPO} --body '{hook_url}'{N}")
        # Auch in .env speichern
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        with open(env_path, encoding="utf-8", errors="ignore") as f:
            env_content = f.read()
        if "RENDER_DEPLOY_HOOK" not in env_content:
            with open(env_path, "a", encoding="utf-8") as f:
                f.write(f'\nRENDER_DEPLOY_HOOK="{hook_url}"\n')

    # ── Android App URL aktualisieren ─────────────────────────────────────────
    kt = os.path.join(os.path.dirname(__file__), "..", "nova_android",
                      "app", "src", "main", "java", "de", "nova", "app", "MainActivity.kt")
    if os.path.exists(kt):
        with open(kt, encoding="utf-8") as f:
            content = f.read()
        new_content = re.sub(
            r'const val CLOUD_URL\s*=\s*"[^"]*"',
            f'const val CLOUD_URL     = "{svc_url}"',
            content
        )
        if new_content != content:
            with open(kt, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"\n{G}Android CLOUD_URL → {svc_url} ✓{N}")

    # ── Render API Key auch in Cloud-Env-Vars setzen ──────────────────────────
    print(f"\n{Y}Aktualisiere Env-Variablen auf Render...{N}")
    env_payload = [
        {"key": "GROQ_API_KEY",   "value": groq_key},
        {"key": "GEMINI_API_KEY", "value": gem_key},
    ]
    resp, status = api("PUT", f"/services/{existing_id}/env-vars",
                      data=env_payload, key=api_key)
    if status in (200, 201):
        print(f"{G}Env-Variablen aktualisiert ✓{N}")

    print(f"\n{G}{'='*55}")
    print(f"  Setup abgeschlossen!")
    print(f"{'='*55}{N}")
    print(f"\n  URL:    {svc_url}")
    print(f"  Repo:   https://github.com/{GITHUB_REPO}")
    print(f"  Keepalive: GitHub Actions pingt alle 5min")
    print(f"\n  Server wird jetzt gebaut (~2-3 Minuten)...")

if __name__ == "__main__":
    main()
