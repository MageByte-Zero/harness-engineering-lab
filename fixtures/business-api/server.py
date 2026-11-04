"""Loopback-only synthetic read service; no orders are created or modified."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--mode', choices=['normal', 'forbidden', 'limited', 'slow', 'invalid'], default='normal')
    parser.add_argument('--log', type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get('BUSINESS_API_TOKEN')
    if not token:
        parser.error('Set BUSINESS_API_TOKEN')
    records = json.loads(Path(__file__).with_name('records.json').read_text())
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open('x', encoding='utf-8') as log:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # Base logger includes unsanitized request text.

            def do_GET(self):
                segments = self.path.split('/')
                valid = (len(segments) in (5, 6) and segments[:4] == ['', 'api', 'v1', 'orders']
                         and (len(segments) == 5 or segments[5] == 'events'))
                oid = segments[4] if valid else None
                kind = 'events' if valid and len(segments) == 6 else 'status'
                authorized = hmac.compare_digest(self.headers.get('Authorization', ''), f'Bearer {token}')
                code, body = 200, None
                if not authorized:
                    code = 401
                elif args.mode == 'forbidden':
                    code = 403
                elif args.mode == 'limited':
                    code = 429
                elif oid not in records:
                    code = 404
                elif args.mode == 'invalid':
                    body = {'order_id': oid, 'state': 'BROKEN'}
                else:
                    body = records[oid][kind]
                # Safe service-side evidence: never retain headers, tokens, or arbitrary paths.
                log.write(json.dumps({'order_id': oid if oid in records else None,
                                      'kind': kind, 'status': code, 'mode': args.mode}) + '\n')
                log.flush()
                if args.mode == 'slow':
                    time.sleep(0.4)
                encoded = json.dumps(body if body is not None else {'error': 'request_failed'}).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
        print(f'Synthetic service on 127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
