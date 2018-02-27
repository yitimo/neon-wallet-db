from pymongo import MongoClient
import redis
from rq import Queue

MONGOAPP = "neonwalletdb"
MONGOURL = "mongodb://127.0.0.1:27017"
REDISURL = "redis://127.0.0.1:6379"

client = MongoClient(MONGOURL)
db = client[MONGOAPP]
redis_db = redis.from_url(REDISURL)

q = Queue(connection=redis_db)

transaction_db = db['transactions']
blockchain_db = db['blockchain']
meta_db = db['meta']
logs_db = db['logs']
address_db = db['addresses']
