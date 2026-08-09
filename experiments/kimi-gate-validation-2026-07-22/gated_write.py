#!/usr/bin/env python3
"""Gated write: propose (path, newcontent) to reasoning-core gate; write only on ALLOW."""
import json, sys, urllib.request, os
path, content_file = sys.argv[1], sys.argv[2]
after = open(content_file).read()
before = open(path).read() if os.path.exists(path) else ""
req = urllib.request.Request("http://127.0.0.1:8765/gate",
    data=json.dumps({"path": path, "before": before, "after": after}).encode(),
    headers={"content-type": "application/json"})
r = json.loads(urllib.request.urlopen(req, timeout=300).read())
if r["verdict"] == "ALLOW":
    open(path, "w").write(after)
print(json.dumps(r))
