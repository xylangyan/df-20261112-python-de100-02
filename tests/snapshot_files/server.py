"""Local HTTP stand-in used by the schema snapshot tests."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class WsdlStandIn:
    """In-process HTTP server serving a set of WSDL/XSD documents.

    Routes map a path to either:

    * a ``(content, content_type)`` tuple, or
    * a ``("redirect", target_path)`` tuple answering with a 302 redirect.

    """

    def __init__(self, routes):
        self._routes = routes
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self):
        host, port = self._server.server_address
        return "http://%s:%d" % (host, port)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def _make_handler(self):
        routes = self._routes

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                route = routes.get(path)
                if route is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                if route[0] == "redirect":
                    self.send_response(302)
                    self.send_header("Location", route[1])
                    self.end_headers()
                    return
                content, content_type = route
                if isinstance(content, str):
                    content = content.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        return Handler
