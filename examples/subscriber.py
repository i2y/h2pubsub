from h2pubsub import Client

client = Client(address='localhost', port=443)
client.subscribe('/test')

while True:
    print(client.receive())


