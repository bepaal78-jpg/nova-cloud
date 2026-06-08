"""
NOVA Cloud — Render.com automatisches Setup
Führe dieses Script aus um den Server komplett einzurichten.
Du brauchst nur deinen Render API Key (1x kopieren).
"""

import os, sys, urllib.request, urllib.parse, json, subprocess

RENDER_API = "https://api.render.com/v1"
GITHUB_REPO = "bepaal78-jpg/nova-cloud"
SERVICE_NAME = "nova-cloud"

# Farben
G = "\033[92m"  # grün
R = "\033[91m"  # rot
Y = "\033[93m"  # gelb
B = "\033[96m"  # cyan
N = "\033[0m"   # reset

def _req(method, path, data=None, api_key=""):
    url = f"{RENDER_API}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return json.loads(body) if body else {}, e.code

def add_github_secret(secret_name, secret_value):
    """Fügt ein Secret zu GitHub Repo hinzu"""
    try:
        # Erst public key holen
        result = subprocess.run(
            ["gh", "api", f"/repos/{GITHUB_REPO}/actions/secrets/public-key"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return False
        pk = json.loads(result.stdout)

        # Secret encrypten (Base64 + libsodium) — simplified via gh CLI
        result = subprocess.run(
            ["gh", "secret", "set", secret_name,
             "--repo", GITHUB_REPO,
             "--body", secret_value],
            capture_output=True, text=True
        )
        return result.returncode == 0
    except Exception as e:
        print(f"  {Y}GitHub Secret konnte nicht gesetzt werden: {e}{N}")
        return False

def main():
    print(f"\n{B}{'='*55}")
    print(f"  NOVA Cloud — Render.com Auto-Setup")
    print(f"{'='*55}{N}\n")

    print(f"{Y}Schritt 1: Render API Key{N}")
    print("  Gehe zu: https://dashboard.render.com/u/settings → API Keys")
    print("  Erstelle einen neuen Key und füge ihn hier ein:\n")
    api_key = input("  Render API Key: ").strip()
    if not api_key:
        print(f"{R}Kein Key eingegeben. Abbruch.{N}")
        sys.exit(1)

    # Owner ID holen
    print(f"\n{Y}Verbinde mit Render...{N}")
    owners, status = _req("GET", "/owners", api_key=api_key)
    if status != 200 or not owners:
        print(f"{R}Fehler beim Abrufen der Render-Account-Daten (HTTP {status}){N}")
        print("Prüfe ob der API Key korrekt ist.")
        sys.exit(1)

    owner = owners[0]["owner"] if isinstance(owners, list) else owners
    owner_id = owner.get("id") or (owners[0].get("id") if isinstance(owners, list) else None)
    print(f"{G}Account: {owner.get('name', 'unbekannt')} (ID: {owner_id}){N}")

    # Env vars einlesen
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
    groq_key = ""
    gemini_key = ""
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("GROQ_API_KEY="):
                    groq_key = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("GEMINI_API_KEY="):
                    gemini_key = line.split("=", 1)[1].strip().strip('"')

    if not groq_key:
        print(f"\n{Y}Schritt 2: Groq API Key{N}")
        groq_key = input("  Groq API Key (von console.groq.com): ").strip()
    else:
        print(f"\n{G}Groq API Key aus .env gelesen ✓{N}")

    if not gemini_key:
        print(f"\n{Y}Schritt 3: Gemini API Key (optional, Enter zum Überspringen){N}")
        gemini_key = input("  Gemini API Key: ").strip()
    else:
        print(f"{G}Gemini API Key aus .env gelesen ✓{N}")

    # Service erstellen
    print(f"\n{Y}Erstelle Render Web Service...{N}")
    env_vars = [{"key": "GROQ_API_KEY", "value": groq_key}]
    if gemini_key:
        env_vars.append({"key": "GEMINI_API_KEY", "value": gemini_key})
    env_vars.append({"key": "PYTHON_VERSION", "value": "3.11.0"})

    service_data = {
        "autoDeploy": "yes",
        "branch": "master",
        "name": SERVICE_NAME,
        "ownerId": owner_id,
        "repo": f"https://github.com/{GITHUB_REPO}",
        "rootDir": ".",
        "serviceDetails": {
            "buildCommand": "pip install -r requirements.txt",
            "startCommand": "uvicorn main:app --host 0.0.0.0 --port $PORT",
            "envVars": env_vars,
            "plan": "free",
            "pullRequestPreviewsEnabled": "no",
            "region": "frankfurt",
        },
        "type": "web_service",
    }

    svc, status = _req("POST", "/services", data=service_data, api_key=api_key)
    if status in (200, 201):
        svc_id  = svc["service"]["id"]
        svc_url = svc["service"]["serviceDetails"].get("url", f"https://{SERVICE_NAME}.onrender.com")
        print(f"{G}Service erstellt! ID: {svc_id}{N}")
        print(f"{G}URL: {svc_url}{N}")
    elif status == 409:
        # Service existiert schon
        print(f"{Y}Service existiert bereits. Suche vorhandenen Service...{N}")
        svcs, _ = _req("GET", f"/services?name={SERVICE_NAME}&ownerId={owner_id}", api_key=api_key)
        if svcs and isinstance(svcs, list):
            svc_id  = svcs[0]["service"]["id"]
            svc_url = svcs[0]["service"]["serviceDetails"].get("url", f"https://{SERVICE_NAME}.onrender.com")
            print(f"{G}Gefunden: {svc_url}{N}")
        else:
            print(f"{R}Konnte vorhandenen Service nicht finden.{N}")
            sys.exit(1)
    else:
        print(f"{R}Fehler beim Erstellen des Services (HTTP {status}):{N}")
        print(json.dumps(svc, indent=2))
        sys.exit(1)

    # Deploy Hook holen
    print(f"\n{Y}Hole Deploy Hook URL...{N}")
    hooks, status = _req("GET", f"/services/{svc_id}/deploy-hooks", api_key=api_key)
    if status == 200 and hooks:
        hook_url = hooks[0].get("url", "") if isinstance(hooks, list) else ""
    else:
        # Neuen Hook erstellen
        hook, status = _req("POST", f"/services/{svc_id}/deploy-hooks",
                            data={"name": "github-actions"}, api_key=api_key)
        hook_url = hook.get("url", "") if status in (200, 201) else ""

    if hook_url:
        print(f"{G}Deploy Hook: {hook_url[:50]}...{N}")
        # In GitHub Secrets speichern
        print(f"\n{Y}Speichere Deploy Hook in GitHub Secrets...{N}")
        if add_github_secret("RENDER_DEPLOY_HOOK_URL", hook_url):
            print(f"{G}GitHub Secret RENDER_DEPLOY_HOOK_URL gesetzt ✓{N}")
        else:
            print(f"{Y}Manuell setzen: gh secret set RENDER_DEPLOY_HOOK_URL --repo {GITHUB_REPO} --body '{hook_url}'{N}")

    print(f"\n{G}{'='*55}")
    print(f"  Setup abgeschlossen!")
    print(f"{'='*55}{N}")
    print(f"\n  Server URL:  {svc_url}")
    print(f"  GitHub Repo: https://github.com/{GITHUB_REPO}")
    print(f"\n  Nächste Schritte:")
    print(f"  1. Server wird gerade gebaut (~3 Minuten)")
    print(f"  2. Die Android-App wird automatisch neu gebaut")
    print(f"  3. Ab jetzt: jeder git push deployt automatisch")
    print(f"\n  Keepalive-CronJob läuft auf GitHub Actions alle 5 Minuten ✓")

    # Android App URL aktualisieren
    update_android_url(svc_url)

def update_android_url(url):
    kt_file = os.path.join(
        os.path.dirname(__file__), "..",
        "nova_android", "app", "src", "main", "java", "de", "nova", "app", "MainActivity.kt"
    )
    if not os.path.exists(kt_file):
        return
    with open(kt_file, "r", encoding="utf-8") as f:
        content = f.read()
    import re
    new_content = re.sub(
        r'const val CLOUD_URL\s*=\s*"[^"]*"',
        f'const val CLOUD_URL     = "{url}"',
        content
    )
    if new_content != content:
        with open(kt_file, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"\n{G}Android App CLOUD_URL aktualisiert → {url}{N}")
        print(f"{Y}APK neu bauen mit: Gradle Sync + Build in Android Studio{N}")

if __name__ == "__main__":
    main()
