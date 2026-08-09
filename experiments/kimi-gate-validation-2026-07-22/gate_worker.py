"""reasoning-core gate worker: oracles + neural score_change, loopback only."""
import ast, json, os, re, sys, tempfile, py_compile, subprocess, time
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, "/tmp/rc/repo")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
from src.s2_core import score_change

REPO = "/tmp/rc/repo"
TW = "/tmp/rc/experiment/taskworld"
RULES = {"forbid_import": ["subprocess", "ctypes"],
         "forbid_pattern": ["eval(", "exec(", "os.system"]}
CAP = float(os.environ.get("RC_GATE_CAP", "0.35"))  # frozen after calibration

def oracle_check(src):
    fails = []
    try:
        ast.parse(src)
    except SyntaxError as e:
        return [f"ast.parse SyntaxError: {e}"]
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(src); tmp = f.name
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        fails.append(f"py_compile: {str(e)[:120]}")
    finally:
        os.unlink(tmp)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, dir="/tmp") as f:
        f.write(src); tmp = f.name
    p = subprocess.run([f"{REPO}/.venv/bin/python","-m","ruff","check",
                        "--config", f"{TW}/ruff.toml","--output-format","concise", tmp],
                       capture_output=True, text=True)
    for line in p.stdout.strip().splitlines():
        if re.search(r":\d+:\d+:", line):
            fails.append("ruff: " + line.split(":",3)[-1].strip())
    os.unlink(tmp)
    for imp in RULES["forbid_import"]:
        if re.search(rf"^\s*(import|from)\s+{re.escape(imp)}\b", src, re.M):
            fails.append(f"rules.yaml forbid_import: {imp}")
    for pat in RULES["forbid_pattern"]:
        if pat in src:
            fails.append(f"rules.yaml forbid_pattern: {pat}")
    return fails

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/score_only":
            r = score_change(body["path"], body.get("before",""), body["after"])
            out = {"coherence_delta": r.coherence_delta, "ais": r.architectural_impact_score,
                   "regression": r.regression_detected, "risk": list(r.risk_vector)}
        elif self.path == "/gate":
            t0 = time.time()
            verdict, reasons = "ALLOW", []
            fails = oracle_check(body["after"])
            if fails:
                verdict, reasons = "BLOCK", fails
                cd = None
            else:
                r = score_change(body["path"], body.get("before",""), body["after"])
                cd = r.coherence_delta
                cold = not body.get("before","").strip() or len(body.get("before","")) < 32
                if not cold and cd > CAP:
                    verdict = "BLOCK"; reasons = [f"coherence_delta {cd:.3f} > cap {CAP}"]
            out = {"verdict": verdict, "reasons": reasons, "coherence_delta": cd,
                   "ms": round((time.time()-t0)*1000)}
        elif self.path == "/oracle_only":
            out = {"fails": oracle_check(body["after"])}
        else:
            self.send_response(404); self.end_headers(); return
        data = json.dumps(out).encode()
        self.send_response(200); self.send_header("content-type","application/json")
        self.send_header("content-length", str(len(data))); self.end_headers()
        self.wfile.write(data)

if __name__ == "__main__":
    print("gate worker up, cap=", CAP, flush=True)
    HTTPServer(("127.0.0.1", 8765), H).serve_forever()
