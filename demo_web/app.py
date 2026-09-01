from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import config
import handlers


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/health":
            status, ctype, body = handlers.health()
        elif parsed.path == "/add":
            a = int(qs.get("a", ["0"])[0])
            b = int(qs.get("b", ["0"])[0])
            status, ctype, body = handlers.add(a, b)
        elif parsed.path == "/stats":
            nums = [int(x) for x in qs.get("nums", [""])[0].split(",") if x != ""]
            status, ctype, body = handlers.stats(nums)
        else:
            status, ctype, body = 404, "text/plain", "not found"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args):
        pass


def main():
    server = HTTPServer((config.HOST, config.PORT), Handler)
    print(f"listening on http://{config.HOST}:{config.PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
