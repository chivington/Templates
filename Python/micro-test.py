import logging
import uasyncio as asyncio
import ujson as json
import gc
import uos as os
import sys
import uerrno as errno
import usocket as socket


log = logging.getLogger('WEB')


def urldecode_plus(s):
    s = s.replace('+', ' ')
    arr = s.split('%')
    res = arr[0]
    for it in arr[1:]:
        if len(it) >= 2: res += chr(int(it[:2], 16)) + it[2:]
        elif len(it) == 0: res += '%'
        else: res += it
    return res

def parse_query_string(s):
    res = {}
    pairs = s.split('&')
    for p in pairs:
        vals = [urldecode_plus(x) for x in p.split('=', 1)]
        if len(vals) == 1: res[vals[0]] = ''
        else: res[vals[0]] = vals[1]
    return res


class HTTPException(Exception):
    def __init__(self, code=400):
        self.code = code


class request:
    def __init__(self, _reader):
        self.reader = _reader
        self.headers = {}
        self.method = b''
        self.path = b''
        self.query_string = b''

    async def read_request_line(self):
        # GET /something/script?param1=val1 HTTP/1.1
        while True:
            rl = await self.reader.readline()
            if rl == b'\r\n' or rl == b'\n': continue # skip empty lines
            break
        rl_frags = rl.split()
        if len(rl_frags) != 3: raise HTTPException(400)
        self.method = rl_frags[0]
        url_frags = rl_frags[1].split(b'?', 1)
        self.path = url_frags[0]
        if len(url_frags) > 1: self.query_string = url_frags[1]

    async def read_headers(self, save_headers=[]):
        # Read and parse HTTP headers until \r\n\r\n: (generator function)
        while True:
            gc.collect()
            line = await self.reader.readline()
            if line == b'\r\n': break
            frags = line.split(b':', 1)
            if len(frags) != 2: raise HTTPException(400)
            if frags[0] in save_headers: self.headers[frags[0]] = frags[1].strip()

    async def read_parse_form_data(self):
        gc.collect()
        if b'Content-Length' not in self.headers: return {}
        if b'Content-Type' not in self.headers: return {}
        size = int(self.headers[b'Content-Length'])
        if size > self.params['max_body_size'] or size < 0: raise HTTPException(413)
        data = await self.reader.readexactly(size)
        ct = self.headers[b'Content-Type'].split(b';', 1)[0]
        try:
            if ct == b'application/json': return json.loads(data)
            elif ct == b'application/x-www-form-urlencoded': return parse_query_string(data.decode())
        except ValueError:
            raise HTTPException(400)


class response:
    def __init__(self, _writer):
        self.writer = _writer
        self.send = _writer.awrite
        self.code = 200
        self.version = '1.0'
        self.headers = {}

    async def _send_headers(self):
        hdrs = 'HTTP/{} {} MSG\r\n'.format(self.version, self.code)
        for k,v in self.headers.items(): hdrs += '{}: {}\r\n'.format(k, v)
        hdrs += '\r\n'
        gc.collect()
        await self.send(hdrs)

    async def error(self, code, msg=None):
        self.code = code
        if msg: self.add_header('Content-Length', len(msg))
        await self._send_headers()
        if msg: await self.send(msg)

    async def redirect(self, location, msg=None):
        self.code = 302
        self.add_header('Location', location)
        if msg: self.add_header('Content-Length', len(msg))
        await self._send_headers()
        if msg: await self.send(msg)

    def add_header(self, key, value):
        self.headers[key] = value

    def add_access_control_headers(self):
        self.add_header('Access-Control-Allow-Origin', self.params['allowed_access_control_origins'])
        self.add_header('Access-Control-Allow-Methods', self.params['allowed_access_control_methods'])
        self.add_header('Access-Control-Allow-Headers', self.params['allowed_access_control_headers'])

    async def start_html(self):
        self.add_header('Content-Type', 'text/html')
        await self._send_headers()

    async def send_file(self, filename, content_type=None, content_encoding=None, max_age=2592000):
        try:
            stat = os.stat(filename)
            slen = str(stat[6])
            self.add_header('Content-Length', slen)
            if content_type: self.add_header('Content-Type', content_type)
            if content_encoding: self.add_header('Content-Encoding', content_encoding)
            self.add_header('Cache-Control', 'max-age={}, public'.format(max_age))
            with open(filename) as f:
                await self._send_headers()
                gc.collect()
                buf = bytearray(128)
                while True:
                    size = f.readinto(buf)
                    if size == 0: break
                    await self.send(buf, sz=size)
        except OSError as e:
            if e.args[0] in (errno.ENOENT, errno.EACCES): raise HTTPException(404)
            else: raise


async def restful_resource_handler(req, resp, param=None):
    data = await req.read_parse_form_data()
    if req.query_string != b'': data.update(parse_query_string(req.query_string.decode()))
    _handler, _kwargs = req.params['_callmap'][req.method]
    gc.collect()
    if param: res = _handler(data, param, **_kwargs)
    else: res = _handler(data, **_kwargs)
    gc.collect()
    if isinstance(res, asyncio.type_gen):
        resp.version = '1.1'
        resp.add_header('Connection', 'close')
        resp.add_header('Content-Type', 'application/json')
        resp.add_header('Transfer-Encoding', 'chunked')
        resp.add_access_control_headers()
        await resp._send_headers()
        for chunk in res:
            chunk_len = len(chunk.encode('utf-8'))
            await resp.send('{:x}\r\n'.format(chunk_len))
            await resp.send(chunk)
            await resp.send('\r\n')
            gc.collect()
        await resp.send('0\r\n\r\n')
    else:
        if type(res) == tuple:
            resp.code = res[1]
            res = res[0]
        elif res is None:
            raise Exception('Result expected')
        if type(res) is dict: res_str = json.dumps(res)
        else: res_str = res
        resp.add_header('Content-Type', 'application/json')
        resp.add_header('Content-Length', str(len(res_str)))
        resp.add_access_control_headers()
        await resp._send_headers()
        await resp.send(res_str)


class webserver:
    def __init__(self, request_timeout=3, max_concurrency=3, backlog=16, debug=False):
        self.loop = asyncio.get_event_loop()
        self.request_timeout = request_timeout
        self.max_concurrency = max_concurrency
        self.backlog = backlog
        self.debug = debug
        self.explicit_url_map = {}
        self.parameterized_url_map = {}
        # Currently opened connections
        self.conns = {}
        # Statistics
        self.processed_connections = 0

    def _find_url_handler(self, req):
        if req.path in self.explicit_url_map:
            return self.explicit_url_map[req.path]
        idx = req.path.rfind(b'/') + 1
        path2 = req.path[:idx]
        if len(path2) > 0 and path2 in self.parameterized_url_map:
            req._param = req.path[idx:].decode()
            return self.parameterized_url_map[path2]
        return (None, None)

    async def _handle_request(self, req, resp):
        await req.read_request_line()
        req.handler, req.params = self._find_url_handler(req)
        if not req.handler:
            await req.read_headers()
            raise HTTPException(404)
        resp.params = req.params
        await req.read_headers(req.params['save_headers'])

    async def _handler(self, reader, writer):
        gc.collect()

        try:
            req = request(reader)
            resp = response(writer)
            await asyncio.wait_for(self._handle_request(req, resp), self.request_timeout)
            if req.method == b'OPTIONS':
                resp.add_access_control_headers()
                resp.add_header('Content-Length', '0')
                await resp._send_headers()
                return
            if req.method not in req.params['methods']:
                raise HTTPException(405)
            gc.collect()
            if hasattr(req, '_param'): await req.handler(req, resp, req._param)
            else: await req.handler(req, resp)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except OSError as e:
            if e.args[0] not in (errno.ECONNABORTED, errno.ECONNRESET, 32):
                try:
                    await resp.error(500)
                except Exception as e:
                    log.exc(e, "")
        except HTTPException as e:
            try:
                await resp.error(e.code)
            except Exception as e:
                log.exc(e)
        except Exception as e:
            log.error(req.path.decode())
            log.exc(e, "")
            try:
                await resp.error(500)
                if self.debug: sys.print_exception(e, resp.writer.s)
            except Exception as e:
                pass
        finally:
            await writer.aclose()
            if len(self.conns) == self.max_concurrency: self.loop.call_soon(self._server_coro)
            del self.conns[id(writer.s)]

    def add_route(self, url, f, **kwargs):
        if url == '' or '?' in url: raise ValueError('Invalid URL')
        params = {
            'methods': ['GET'],
            'save_headers': [],
            'max_body_size': 1024,
            'allowed_access_control_headers': '*',
            'allowed_access_control_origins': '*'
        }
        params.update(kwargs)
        params['allowed_access_control_methods'] = ', '.join(params['methods'])
        params['methods'] = [x.encode() for x in params['methods']]
        params['save_headers'] = [x.encode() for x in params['save_headers']]
        if url.endswith('>'):
            idx = url.rfind('<')
            path = url[:idx]
            idx += 1
            param = url[idx:-1]
            if path.encode() in self.parameterized_url_map: raise ValueError('URL exists')
            params['_param_name'] = param
            self.parameterized_url_map[path.encode()] = (f, params)
        if url.encode() in self.explicit_url_map: raise ValueError('URL exists')
        self.explicit_url_map[url.encode()] = (f, params)

    def add_resource(self, cls, url, **kwargs):
        methods = []
        callmap = {}
        try:
            obj = cls()
        except TypeError:
            obj = cls
        for m in ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']:
            fn = m.lower()
            if hasattr(obj, fn):
                methods.append(m)
                callmap[m.encode()] = (getattr(obj, fn), kwargs)
        self.add_route(url, restful_resource_handler, methods=methods, save_headers=['Content-Length', 'Content-Type'], _callmap=callmap)

    def route(self, url, **kwargs):
        def _route(f):
            self.add_route(url, f, **kwargs)
            return f
        return _route

    def resource(self, url, method='GET', **kwargs):
        def _resource(f):
            self.add_route(url, restful_resource_handler, methods=[method], save_headers=['Content-Length', 'Content-Type'], _callmap={method.encode(): (f, kwargs)})
            return f
        return _resource

    async def _tcp_server(self, host, port, backlog):
        addr = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][-1]
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(addr)
        sock.listen(backlog)
        try:
            while True:
                yield asyncio.IORead(sock)
                csock, caddr = sock.accept()
                csock.setblocking(False)
                self.processed_connections += 1
                hid = id(csock)
                handler = self._handler(asyncio.StreamReader(csock), asyncio.StreamWriter(csock, {}))
                self.conns[hid] = handler
                self.loop.create_task(handler)
                if len(self.conns) == self.max_concurrency: yield False
        except asyncio.CancelledError:
            return
        finally:
            sock.close()

    def run(self, host="127.0.0.1", port=8081, loop_forever=True):
        self._server_coro = self._tcp_server(host, port, self.backlog)
        self.loop.create_task(self._server_coro)
        if loop_forever: self.loop.run_forever()

    def shutdown(self):
        asyncio.cancel(self._server_coro)
        for hid, coro in self.conns.items(): asyncio.cancel(coro)
