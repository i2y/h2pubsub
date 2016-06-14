import msgpack
import collections
import eventlet
import socket

from logging import getLogger, basicConfig, WARNING
from eventlet.green.OpenSSL import SSL
from h2.connection import H2Connection
from h2.events import (RequestReceived, DataReceived, StreamEnded,
                       ResponseReceived, PushedStreamReceived)


DEFAULT_MAX_MESSAGE_SIZE = 1024
RECV_DATA_SIZE = 65535

_greenlet_pool = eventlet.GreenPool(size=100000)

logger = getLogger(__name__)
basicConfig(level=WARNING)


def get_greenlet_pool():
    return _greenlet_pool


class ConnectionManager(object):

    path_conn_mapping = {}

    @staticmethod
    def add_subscriber(conn_mgr, path='/'):
        if path not in ConnectionManager.path_conn_mapping:
            ConnectionManager.path_conn_mapping[path] = set()
        ConnectionManager.path_conn_mapping[path].add(conn_mgr)

    @staticmethod
    def del_subscriber(conn_mgr, path):
        if path in ConnectionManager.path_conn_mapping:
            conn_set = ConnectionManager.path_conn_mapping[path]
            conn_set.discard(conn_mgr)
            if len(conn_set) == 0:
                del ConnectionManager.path_conn_mapping[path]

    def __init__(self, sock,
                 max_message_size=DEFAULT_MAX_MESSAGE_SIZE,
                 threshold=DEFAULT_MAX_MESSAGE_SIZE * 10):
        self.sock = sock
        self.conn = H2Connection(client_side=False)
        self.stream_path_mapping = {}
        self.path_stream_mapping = {}
        self.max_message_size = max_message_size
        self.threshold = threshold

    def push(self, path, message):
        promised_stream_id = self.conn.get_next_available_stream_id()
        self.conn.push_stream(stream_id=self.path_stream_mapping[path],
                              promised_stream_id=promised_stream_id,
                              request_headers={':authority': socket.gethostname(),
                                               ':method': 'GET',
                                               ':scheme': 'https',
                                               ':path': path})
        self.conn.send_headers(stream_id=promised_stream_id,
                               headers={':status': '200'},
                               end_stream=False)
        self.conn.send_data(stream_id=promised_stream_id,
                            data=msgpack.dumps(message,
                                               encoding='utf-8'),
                            end_stream=True)
        self.conn.outbound_flow_control_window += DEFAULT_MAX_MESSAGE_SIZE
        stream = self.conn._get_stream_by_id(promised_stream_id)
        stream.outbound_flow_control_window += DEFAULT_MAX_MESSAGE_SIZE
        self.sock.sendall(self.conn.data_to_send())

    def run_forever(self):
        self.conn.initiate_connection()
        self.sock.sendall(self.conn.data_to_send())

        while True:
            data = self.sock.recv(RECV_DATA_SIZE)
            if not data:
                break

            for event in self.conn.receive_data(data):
                if isinstance(event, RequestReceived):
                    self.request_received(event.headers, event.stream_id)
                elif isinstance(event, DataReceived):
                    self.data_received(event.data, event.stream_id)
                elif isinstance(event, StreamEnded):
                    logger.info(event)

            self.sock.sendall(self.conn.data_to_send())

    def request_received(self, headers, stream_id):
        headers = collections.OrderedDict(headers)
        method = headers[':method']
        path = headers[':path']
        if method == 'GET':
            if path not in self.path_stream_mapping:
                self.path_stream_mapping[path] = stream_id
                self.add_subscriber(self, path)
        elif method == 'DELETE':
            del self.path_stream_mapping[path]
            self.del_subscriber(self, path)
        else:
            self.stream_path_mapping[stream_id] = method, path

    def data_received(self, data, stream_id):
        if stream_id not in self.stream_path_mapping:
            return

        if len(data) > self.max_message_size:
            logger.warning("Message size exceeds fixed limit")

        if self.conn.remote_flow_control_window(stream_id) < self.threshold:
            self.conn.increment_flow_control_window(self.max_message_size, stream_id)
            self.conn.increment_flow_control_window(self.max_message_size, None)
            self.sock.sendall(self.conn.data_to_send())

        try:
            method, path = self.stream_path_mapping[stream_id]
            if method == 'POST':
                msg = msgpack.loads(data, encoding='utf-8')
                if path in self.path_conn_mapping:
                    for conn_mgr in self.path_conn_mapping[path]:
                        conn_mgr.push(path, msg)
        except Exception as e:
            logger.error(e)
            self.path_conn_mapping[path].remove(conn_mgr)
        finally:
            del self.stream_path_mapping[stream_id]
            self.conn.end_stream(stream_id)

    def wait_(self, stream_id):
        while True:
            data = self.sock.recv(RECV_DATA_SIZE)

            if len(data) == 0:
                self.sock.close()
                break

            for event in self.conn.receive_data(data):
                logger.info(event)

            if self.conn.local_flow_control_window(stream_id) > self.max_message_size:
                break


def alpn_callback(conn, protocols):
    if b'h2' in protocols:
        return b'h2'
    raise RuntimeError('No acceptable protocol offered!')


def npn_advertise_cb(conn):
    return [b'h2']


class Server(object):
    def __init__(self,
                 address='0.0.0.0',
                 port=443,
                 ssl_context=None):
        sock = eventlet.listen((address, port))
        if ssl_context is None:
            self.sock = sock
        else:
            self.sock = SSL.Connection(ssl_context, sock)

    def _run(self):
        while True:
            try:
                new_sock, _ = self.sock.accept()
                manager = ConnectionManager(new_sock)
                eventlet.spawn_n(manager.run_forever)
            except (SystemExit, KeyboardInterrupt):
                break

    def run(self):
        _greenlet_pool.spawn_n(self._run)

    def run_forever(self):
        self.run()
        _greenlet_pool.waitall()


REQUEST_HEADERS = [
    (':authority', socket.gethostname()),
    (':scheme', 'https')
]


class Client(object):

    def __init__(self, address, port,
                 ssl_context=None,
                 max_message_size=DEFAULT_MAX_MESSAGE_SIZE,
                 threshold=DEFAULT_MAX_MESSAGE_SIZE * 10):
        self.max_message_size = max_message_size
        self.threshold = threshold
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if ssl_context is None:
            self.sock = sock
        else:
            self.sock = SSL.Connection(ssl_context, sock)
        self.sock.connect((address, port))
        self.conn = H2Connection(client_side=True)
        self.conn.initiate_connection()
        self.sock.sendall(self.conn.data_to_send())
        self.stream_id = 1

    def setting_acked(self, event):
        logger.info(event)

    def handle_response(self, response_headers):
        for header in response_headers:
            logger.info(header)

    def handle_data(self, data):
        if len(data) == 0:
            return
        return msgpack.loads(data, encoding='utf-8')

    def end_stream(self, stream_id):
        logger.info('close ' + stream_id)
        self.conn.close_connection()
        self.sock.sendall(self.conn.data_to_send())
        self.sock.close()

    def publish(self, path, message):
        request_headers = [(':authority', socket.gethostname()),
                           (':scheme', 'https'),
                           (':method', 'POST'),
                           (':path', path),
                           ('user-agent', 'h2pubsub/1.0.0')]
        stream_id = self.conn.get_next_available_stream_id()
        self.conn.send_headers(stream_id,
                               request_headers,
                               end_stream=False)
        data = msgpack.dumps(message, encoding='utf-8')
        if len(data) > self.max_message_size:
            logger.warning('Message size exceeds fixed limit')
        self.conn.send_data(stream_id,
                            data,
                            end_stream=True)
        self.wait_(stream_id)
        self.sock.sendall(self.conn.data_to_send())

    def subscribe(self, path):
        request_headers = [(':authority', socket.gethostname()),
                           (':scheme', 'https'),
                           (':method', 'GET'),
                           (':path', path),
                           ('user-agent', 'h2pubsub/1.0.0')]
        self.conn.send_headers(self.conn.get_next_available_stream_id(),
                               request_headers,
                               end_stream=False)
        self.sock.sendall(self.conn.data_to_send())

    def unsubscribe(self, path):
        request_headers = [(':authority', socket.gethostname()),
                           (':scheme', 'https'),
                           (':method', 'DELETE'),
                           (':path', path),
                           ('user-agent', 'h2pubsub/1.0.0')]
        self.conn.send_headers(self.conn.get_next_available_stream_id(),
                               request_headers,
                               end_stream=True)
        self.sock.sendall(self.conn.data_to_send())

    def disconnect(self):
        if hasattr(self, 'conn'):
            self.conn.close_connection()
            if hasattr(self, 'sock'):
                self.sock.sendall(self.conn.data_to_send())
        if hasattr(self, 'sock'):
            self.sock.close()

    def receive(self):
        while True:
            data = self.sock.recv(RECV_DATA_SIZE)

            if len(data) == 0:
                self.sock.close()
                break

            for event in self.conn.receive_data(data):
                if isinstance(event, ResponseReceived):
                    self.handle_response(event.headers)
                elif isinstance(event, PushedStreamReceived):
                    if self.conn.inbound_flow_control_window < self.threshold:
                        self.conn.inbound_flow_control_window += self.max_message_size
                        stream = self.conn._get_stream_by_id(event.pushed_stream_id)
                        stream.inbound_flow_control_window += self.max_message_size
                elif isinstance(event, DataReceived):
                    return self.handle_data(event.data)
                elif isinstance(event, StreamEnded):
                    self.end_stream(event.stream_id)
                else:
                    logger.info(event)

    def wait_(self, stream_id):
        while True:
            data = self.sock.recv(RECV_DATA_SIZE)

            if len(data) == 0:
                self.sock.close()
                break

            for event in self.conn.receive_data(data):
                logger.info(event)

            if self.conn.local_flow_control_window(stream_id) > self.max_message_size:
                break

    def __del__(self):
        self.disconnect()
