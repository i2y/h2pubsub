from h2pubsub import Client

client = Client(address='localhost', port=443)

while True:
    client.publish('/test', 'aiueo')
