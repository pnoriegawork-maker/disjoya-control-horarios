import os
import sys
import time
import json
import hashlib
import urllib.request
import urllib.error

def get_token():
    if "VERCEL_TOKEN" in os.environ:
        return os.environ["VERCEL_TOKEN"]
    token_file = os.path.join(os.path.dirname(__file__), ".vercel_token")
    if os.path.exists(token_file):
        with open(token_file, "r") as f:
            return f.read().strip()
    return ""

TOKEN = get_token()
PROJECT_ID = "prj_2wIG5BaHBHuH2R9LZH3GpWh8XAjJ"
PROJECT_NAME = "disjoya-control-horarios"

def deploy():
    if not TOKEN:
        print("[X] No se encontró VERCEL_TOKEN ni archivo .vercel_token")
        sys.exit(1)

    base_dir = os.path.abspath(os.path.dirname(__file__))
    print(f"[*] Escaneando archivos en: {base_dir}")

    explicit_files = ["requirements.txt", "vercel.json", "api/index.py"]
    paths = []

    for ef in explicit_files:
        if os.path.exists(os.path.join(base_dir, ef)):
            paths.append(ef)

    for root_folder in ["app", "static"]:
        full_root = os.path.join(base_dir, root_folder)
        if not os.path.exists(full_root):
            continue
        for r, _, fnames in os.walk(full_root):
            if "__pycache__" in r:
                continue
            for f in fnames:
                if f.endswith(".pyc"):
                    continue
                full_p = os.path.join(r, f)
                rel_p = os.path.relpath(full_p, base_dir).replace("\\", "/")
                paths.append(rel_p)

    print(f"[*] Total de archivos identificados para despliegue: {len(paths)}")

    payload_files = []
    headers_common = {
        "Authorization": f"Bearer {TOKEN}",
        "User-Agent": "Antigravity-Disjoya-Deployer"
    }

    uploaded = 0
    cached = 0

    for rel_path in paths:
        full_path = os.path.join(base_dir, rel_path)
        with open(full_path, "rb") as f:
            content = f.read()

        sha = hashlib.sha1(content).hexdigest()
        size = len(content)

        upload_req = urllib.request.Request(
            "https://api.vercel.com/v2/files",
            data=content,
            headers={
                **headers_common,
                "Content-Length": str(size),
                "x-now-digest": sha,
                "Content-Type": "application/octet-stream"
            }
        )

        try:
            res = urllib.request.urlopen(upload_req)
            if res.status == 200:
                uploaded += 1
            else:
                cached += 1
        except urllib.error.HTTPError as e:
            if e.code == 409:
                cached += 1
            else:
                print(f"[!] Error subiendo {rel_path}: {e.code} - {e.read().decode('utf-8', errors='ignore')}")

        payload_files.append({
            "file": rel_path,
            "sha": sha,
            "size": size
        })

    print(f"[*] Archivos sincronizados: {uploaded} nuevos/modificados, {cached} en caché.")

    # Crear Despliegue en Producción
    print(f"[*] Solicitando compilación y despliegue a Vercel Producción...")
    deployment_payload = {
        "name": PROJECT_NAME,
        "project": PROJECT_ID,
        "target": "production",
        "files": payload_files
    }

    dep_req = urllib.request.Request(
        "https://api.vercel.com/v13/deployments",
        data=json.dumps(deployment_payload).encode("utf-8"),
        headers={
            **headers_common,
            "Content-Type": "application/json"
        }
    )

    try:
        res = urllib.request.urlopen(dep_req)
        dep_data = json.loads(res.read().decode("utf-8"))
        dep_id = dep_data["id"]
        dep_url = dep_data.get("url", "")
        print(f"[+] Despliegue creado con ID: {dep_id}")
        print(f"[+] URL de compilación: https://{dep_url}")
    except urllib.error.HTTPError as e:
        print(f"[X] Error creando despliegue: {e.code} - {e.read().decode('utf-8', errors='ignore')}")
        sys.exit(1)

    print("[*] Esperando que Vercel finalice la compilación...")
    for _ in range(30):
        time.sleep(3)
        check_req = urllib.request.Request(
            f"https://api.vercel.com/v13/deployments/{dep_id}",
            headers=headers_common
        )
        try:
            res = urllib.request.urlopen(check_req)
            status_data = json.loads(res.read().decode("utf-8"))
            state = status_data.get("readyState") or status_data.get("status")
            print(f"    - Estado actual: {state}")
            if state == "READY":
                print("\n=======================================================")
                print("🎉 DESPLIEGUE EN PRODUCCIÓN EXITOSO")
                print("=======================================================")
                print(f"🔗 URL Principal: https://disjoya-control-horarios.vercel.app")
                print(f"🔗 URL Despliegue: https://{dep_url}")
                print("=======================================================\n")
                return True
            elif state in ["ERROR", "CANCELED"]:
                print(f"[X] El despliegue terminó en estado: {state}")
                print(json.dumps(status_data.get("error", {}), indent=2))
                return False
        except Exception as e:
            print(f"    - Error consultando estado: {e}")

    print("[!] Tiempo de espera agotado. El despliegue continuará compilando en Vercel.")
    return False

if __name__ == "__main__":
    deploy()
