from pymongo import MongoClient
import os
import redis
from rq import Queue

# MONGOUSER = os.environ.get('MONGOUSER')
# MONGOPASS = os.environ.get('MONGOPASS')
# MONGOURL = os.environ.get('MONGOURL')
MONGOAPP = "neonwalletdb"
MONGOURL = "mongodb://127.0.0.1:27017"

client = MongoClient(MONGOURL)
db = client[MONGOAPP]

# db["meta"].insert_one({"name":"lastTrustedBlock", "value":1162327})
# db["meta"].insert_one({"name":"lastTrustedTransaction", "value":1162327})

# redis

# redis_url = os.environ.get('REDISTOGO_URL')

redis_db = redis.from_url("redis://127.0.0.1:6379")

# redis_db.flushdb()

q = Queue(connection=redis_db)

transaction_db = db['transactions']
blockchain_db = db['blockchain']
meta_db = db['meta']
logs_db = db['logs']
address_db = db['addresses']
