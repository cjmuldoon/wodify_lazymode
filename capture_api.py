"""
mitmproxy addon — captures all Wodify API traffic to /tmp/wodify_capture/.

Usage:
    pip install mitmproxy
    mitmdump -s capture_api.py --listen-port 8080

Then:
    1. System Preferences → Network → Advanced → Proxies
       Set HTTP and HTTPS proxy to 127.0.0.1:8080
    2. Open Keychain Access, trust the mitmproxy certificate
       (mitmproxy will auto-generate it at ~/.mitmproxy/mitmproxy-ca-cert.pem)
       Or install it via: mitmproxy opens a browser guide at http://mitm.it
    3. Open the Wodify app and navigate to the WOD/Programming tab
    4. Watch the output here — all Wodify API calls will be logged and saved
    5. Press Ctrl-C when done, then restore proxy settings
"""

import json
import os
from pathlib import Path
from mitmproxy import http

SAVE_DIR = Path("/tmp/wodify_capture")
SAVE_DIR.mkdir(exist_ok=True)

_counter = 0


class WodifyCapture:
    def response(self, flow: http.HTTPFlow) -> None:
        global _counter
        host = flow.request.pretty_host
        if "wodify" not in host.lower():
            return

        url = flow.request.pretty_url
        method = flow.request.method
        status = flow.response.status_code
        content_type = flow.response.headers.get("content-type", "")

        # Log every Wodify request
        print(f"\n{'='*60}")
        print(f"[{status}] {method} {url}")

        # Save request body if any
        req_body = ""
        if flow.request.content:
            try:
                req_body = flow.request.content.decode("utf-8")
            except Exception:
                req_body = "<binary>"

        # Save response
        resp_body = ""
        if flow.response.content:
            try:
                resp_body = flow.response.content.decode("utf-8")
            except Exception:
                resp_body = "<binary>"

        # Print interesting ones (likely JSON API responses)
        if "json" in content_type or resp_body.strip().startswith("{"):
            print(f"REQUEST BODY: {req_body[:500]}")
            print(f"RESPONSE ({len(resp_body)} bytes): {resp_body[:1000]}")

            _counter += 1
            safe_path = url.split("?")[0].replace("https://", "").replace("/", "_").replace(".", "_")[:80]
            out = {
                "url": url,
                "method": method,
                "status": status,
                "request_headers": dict(flow.request.headers),
                "request_body": req_body,
                "response_body": resp_body,
            }
            save_path = SAVE_DIR / f"{_counter:03d}_{safe_path}.json"
            with open(save_path, "w") as f:
                json.dump(out, f, indent=2)
            print(f"Saved → {save_path}")

        # Highlight anything WOD-related
        url_lower = url.lower()
        if any(kw in url_lower for kw in ["wod", "workout", "program", "athlete"]):
            print(f"  *** WOD-RELATED URL! ***")


addons = [WodifyCapture()]
