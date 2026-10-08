"""Local inspection gateway. Fixed upstream; no code execution or arbitrary URL."""
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

HOP={'connection','transfer-encoding','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','upgrade'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass  # No cookies, access token or request content in logs.

    def forward(self):
        if not self.path.startswith('/') or self.path.startswith('//'):
            self.send_error(400);return
        try:length=int(self.headers.get('Content-Length','0'))
        except ValueError:self.send_error(400);return
        if not 0<=length<=8*1024*1024 or self.headers.get('Transfer-Encoding'):
            self.send_error(413);return
        headers={k:v for k,v in self.headers.items() if k.lower() not in HOP}
        client=HTTPConnection('app',8000,timeout=60)
        try:
            client.request(self.command,self.path,body=self.rfile.read(length),headers=headers)
            response=client.getresponse();body=response.read(16*1024*1024+1)
            if len(body)>16*1024*1024:self.send_error(502);return
            self.send_response(response.status)
            for key,value in response.getheaders():
                if key.lower() not in HOP|{'content-length'}:self.send_header(key,value)
            self.send_header('Content-Length',str(len(body)));self.end_headers()
            if self.command!='HEAD':self.wfile.write(body)
        except OSError:self.send_error(502,'Laravel inspection unavailable')
        finally:client.close()

    do_GET=do_HEAD=do_POST=do_PUT=do_PATCH=do_DELETE=do_OPTIONS=forward


if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',8000),Handler).serve_forever()
