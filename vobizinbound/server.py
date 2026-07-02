import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PORT = int(os.environ.get("PORT", "8000"))

VOBIZ_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Speak>Thank you for calling. Please call back to our company number eight nine five one two nine two seven eight eight.</Speak>
  <Hangup/>
</Response>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._handle()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        self._handle()

    def do_HEAD(self):
        self._send_headers("application/xml; charset=utf-8", len(VOBIZ_XML))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle(self):
        if self.path == "/health":
            body = b"ok\n"
            self._send_headers("text/plain; charset=utf-8", len(body))
            self.wfile.write(body)
            return

        body = VOBIZ_XML.encode("utf-8")
        self._send_headers("application/xml; charset=utf-8", len(body))
        self.wfile.write(body)

    def _send_headers(self, content_type, content_length):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"vobiz inbound XML server listening on :{PORT}", flush=True)
    server.serve_forever()
