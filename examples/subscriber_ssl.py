from h2pubsub import Client
from eventlet.green.OpenSSL import SSL, crypto


def alpn_callback(conn, protocols):
    if b'h2' in protocols:
        return b'h2'
    raise RuntimeError('No acceptable protocol offered!')


def npn_advertise_cb(conn):
    return [b'h2']

context = SSL.Context(SSL.SSLv23_METHOD)
context.set_verify(SSL.VERIFY_NONE, lambda *args: True)
context.use_privatekey_file('server.key')
context.use_certificate_file('server.crt')
context.set_npn_advertise_callback(npn_advertise_cb)
context.set_alpn_select_callback(alpn_callback)
context.set_cipher_list('ECDHE+AESGCM')
context.set_tmp_ecdh(crypto.get_elliptic_curve(u'prime256v1'))

client = Client(address='localhost', port=443, ssl_context=context)
client.subscribe('/test')

while True:
    print(client.receive())
