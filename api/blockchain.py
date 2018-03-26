import requests
import json
import sys
from rq import Queue
from .db import db as blockchain_db
from .util import MAINNET_SEEDS, TESTNET_SEEDS
import time
from .scripts import add_fees

nodeAPI = "http://192.168.0.134:20332" # "http://192.168.1.15:20332" "http://192.168.0.134:20332"
net = "TestNet"

# helper for making node RPC request
def rpcRequest(method, params, nodeAPI=nodeAPI):
    return requests.post(nodeAPI, json={"jsonrpc": "2.0", "method": method, "params": params, "id": 0}, timeout=5).json()

# get a block with the specified index from the node
# second param of 1 indicates verbose, which returns destructured hash
def getBlock(index, nodeAPI=nodeAPI):
    return rpcRequest("getblock", [index,1], nodeAPI)

# get the current block height from the node
def getBlockCount(nodeAPI=False):
    if nodeAPI == False:
        nodeAPI = get_highest_node()
    return rpcRequest("getblockcount", [], nodeAPI)

def checkSeeds():
    seed_list = MAINNET_SEEDS if net == "MainNet" else TESTNET_SEEDS
    seeds = []
    for test_rpc in seed_list:
        print(test_rpc)
        try:
            start = time.time()
            data = getBlockCount(test_rpc)
            getBlock(int(data["result"])-1, test_rpc)
            elapsed = time.time() - start
            seeds.append({"url": test_rpc, "status": True, "block_height": int(data["result"]), "time": elapsed })
        except:
            seeds.append({"url": test_rpc, "status": False, "block_height": None, "time": None})
            continue
        print(seeds[-1])
    blockchain_db['meta'].update_one({"name": "node_status"}, {"$set": {"nodes": seeds}}, upsert=True)
    return True

# get the node with the highest block height
def get_highest_node():
    nodes_data = blockchain_db['meta'].find_one({"name":"node_status"})["nodes"]
    return sorted([x for x in nodes_data if x["block_height"] != None], key=lambda x: (x["block_height"], -1*x["time"]), reverse=True)[0]["url"]

# get the latest block count and store last block in the database
def storeBlockInDB(block_index, nodeAPI=False):
    if not nodeAPI:
        nodeAPI = get_highest_node()
    print("using {}".format(nodeAPI))
    data = getBlock(block_index, nodeAPI=nodeAPI)
    block_data = data["result"]
    # do transaction processing first, so that if anything goes wrong we don't update the chain data
    # the chain data is used for the itermittant syncing/correction step
    success, total_sys, total_net = storeBlockTransactions(block_data)
    if success:
        lastBlock = blockchain_db['blockchain'].find_one({"index": block_data["index"]-1})
        print(lastBlock)
        if lastBlock and 'sys_fee' in lastBlock and 'net_fee' in lastBlock:
            block_data['sys_fee'] = lastBlock['sys_fee'] + total_sys
            block_data['net_fee'] = lastBlock['net_fee'] + total_net
        blockchain_db['blockchain'].update_one({"index": block_data["index"]}, {"$set": block_data}, upsert=True)
        return True
    return False

def convert_txid(txid):
    if len(txid) == 66:
        return txid[2:]
    else:
        return txid

# store all the transactions in a block in the database
# if the transactions already exist, they will be updated
# if they don't exist, they will be replaced
def storeBlockTransactions(block):
    transactions = block['tx']
    out = []
    total_sys = 0.0
    total_net = 0.0
    for t in transactions: # 遍历交易
        t['txid'] = convert_txid(t['txid']) # 去掉0x
        t['block_index'] = block["index"]
        t['sys_fee'] = float(t['sys_fee'])
        t['net_fee'] = float(t['net_fee'])
        total_sys += t['sys_fee'] # 累加费用
        total_net += t['net_fee'] # 累加费用
        if 'vin' in t: # 存在输入
            input_transaction_data = []
            for vin in t['vin']:
                vin['txid'] = convert_txid(vin['txid'])
                try:
                    print("trying...")
                    # 从已有存储找到这条输入对应的tx
                    lookup_t = blockchain_db['transactions'].find_one({"txid": vin['txid']})
                    # print(lookup_t)
                    # 把找到的tx的输出拿出来
                    input_transaction_data.append(lookup_t['vout'][vin['vout']])
                    # print(input_transaction_data)
                    # 把这个加载最后的tx的txid设置为这条输入的txid(因为output本身没有txid)
                    input_transaction_data[-1]['txid'] = vin['txid']
                except:
                    print("failed on transaction lookup")
                    # print(vin['txid'])
                    return False
            t['vin_verbose'] = input_transaction_data
        if 'claims' in t: # 存在GAS提取 包含的也是个txid
            claim_transaction_data = []
            key_data = []
            for claim in t["claims"]:
                claim['txid'] = convert_txid(claim['txid'])
                print("claim", claim)
                key_data.append({"key": "{}_{}".format(claim['txid'], claim['vout'])})
                try:
                    lookup_t = blockchain_db['transactions'].find_one({"txid": claim['txid']})
                    # print("lookup", lookup_t)
                    claim_transaction_data.append(lookup_t['vout'][claim['vout']])
                    # print(claim_transaction_data)
                    claim_transaction_data[-1]['txid'] = claim['txid']
                except:
                    print("failed on transaction lookup")
                    print(claim['txid'])
            t['claims_verbose'] = claim_transaction_data
            t['claims_keys_v1'] = key_data
        # 也就是说前面的txid会累积在input中 通通赋值到最新的tx中 字段为vin_verbose
        blockchain_db['transactions'].update_one({"txid": t["txid"]}, {"$set": t}, upsert=True)
    return True, total_sys, total_net

def storeLatestBlockInDB():
    nodeAPI = get_highest_node()
    print("updating latest block with {}".format(nodeAPI))
    currBlock = getBlockCount(nodeAPI=nodeAPI)["result"]
    print("current block {}".format(currBlock))
    # height - 1 = current block
    storeBlockInDB(currBlock-1, nodeAPI)

def log_event_worker(data):
    if "type" in data and data["type"] in ["CLAIM", "SEND", "LOGIN"]:
        blockchain_db["events"].insert_one(data)

def update_sys_fees():
    add_fees()
