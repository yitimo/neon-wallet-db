from flask import Flask, Blueprint
from flask import jsonify
from bson import json_util
import json
from pymongo import MongoClient
from flask import request
from flask.ext.cache import Cache
from flask_cors import CORS, cross_origin
from .db import q, transaction_db, blockchain_db, meta_db, logs_db, address_db
from .blockchain import storeBlockInDB, get_highest_node, log_event_worker, convert_txid
from .util import ANS_ID, ANC_ID, calculate_bonus
import random
from werkzeug.contrib.cache import MemcachedCache
import time
from .cache import cache

api = Blueprint('api',__name__)

NET = "PrivNet"

symbol_dict = {ANS_ID: "NEO", ANC_ID: "GAS"}

def db2json(db_obj):
    return json.loads(json.dumps(db_obj, indent=4, default=json_util.default))

# return a dictionary of spent (txids, vout) => transaction when spent
# TODO: add vout to this
def get_vin_txids(txs):
    spent_ids = {"NEO":{}, "GAS":{}}
    for tx in txs:
        for tx_sent in tx["vin_verbose"]:
            asset_symbol = symbol_dict[tx_sent["asset"]]
            spent_ids[asset_symbol][(convert_txid(tx_sent["txid"]), tx_sent["n"])] = tx
    return spent_ids

# return a dictionary of claimed (txids, vout) => transaction when claimed
def get_claimed_txids(txs):
    claimed_ids = {}
    for tx in txs:
        for tx_claimed in tx["claims"]:
            claimed_ids[(convert_txid(tx_claimed["txid"]), tx_claimed['vout'])] = tx
    return claimed_ids

def balance_for_transaction(address, tx):
    neo_out, neo_in = 0, 0
    gas_out, gas_in = 0.0, 0.0
    neo_sent, gas_sent = False, False
    if "vin_verbose" in tx:
        for tx_info in tx['vin_verbose']:
            if tx_info['address'] == address:
                if tx_info['asset'] == ANS_ID or (tx_info['asset'] == "0x" + ANS_ID):
                    neo_out += int(tx_info['value'])
                    neo_sent = True
                if tx_info['asset'] == ANC_ID or (tx_info['asset'] == "0x" + ANC_ID):
                    gas_out += float(tx_info['value'])
                    gas_sent = True
    if "vout" in tx:
        for tx_info in tx['vout']:
            if tx_info['address'] == address:
                if tx_info['asset'] == ANS_ID or (tx_info['asset'] == "0x" + ANS_ID):
                    neo_in += int(tx_info['value'])
                    neo_sent = True
                if tx_info['asset'] == ANC_ID or (tx_info['asset'] == "0x" + ANC_ID):
                    gas_in += float(tx_info['value'])
                    gas_sent = True
    return {"txid": convert_txid(tx['txid']), "block_index":tx["block_index"],
        "NEO": neo_in - neo_out,
        "GAS": gas_in - gas_out,
        "neo_sent": neo_sent,
        "gas_sent": gas_sent}

# walk over "vout" transactions to collect those that match desired address
def info_received_transaction(address, tx):
    out = {"NEO":[], "GAS":[]}
    neo_tx, gas_tx = [], []
    if not "vout" in tx:
        return out
    for i,obj in enumerate(tx["vout"]):
        if obj["address"] == address:
            if obj["asset"] == ANS_ID or (obj["asset"] == "0x" + ANS_ID):
                neo_tx.append({"value": int(obj["value"]), "index": obj["n"], "txid": convert_txid(tx["txid"])})
            if obj["asset"] == ANC_ID or (obj["asset"] == "0x" + ANC_ID):
                gas_tx.append({"value": float(obj["value"]), "index": obj["n"], "txid": convert_txid(tx["txid"])})
    out["NEO"] = neo_tx
    out["GAS"] = gas_tx
    return out

def info_sent_transaction(address, tx):
    out = {"NEO":[], "GAS":[]}
    neo_tx, gas_tx = [], []
    if not "vin_verbose" in tx:
        return out
    for i,obj in enumerate(tx["vin_verbose"]):
        if obj["address"] == address:
            if obj["asset"] == ANS_ID or (obj["asset"] == "0x" + ANS_ID):
                neo_tx.append({"value": int(obj["value"]), "index": obj["n"], "txid": convert_txid(obj["txid"]), "sending_id":convert_txid(tx["txid"])})
            if obj["asset"] == ANC_ID or (obj["asset"] == "0x" + ANC_ID):
                gas_tx.append({"value": float(obj["value"]), "index": obj["n"], "txid": convert_txid(obj["txid"]), "sending_id":convert_txid(tx["txid"])})
    out["NEO"] = neo_tx
    out["GAS"] = gas_tx
    return out

# get the amount sent to an address from the vout list
def amount_sent(address, asset_id, vout):
    total = 0
    for obj in vout:
        if obj["address"] == address and asset_id == obj["asset"]:
            if asset_id == ANS_ID or (asset_id == "0x" + ANS_ID):
                total += int(obj["value"])
            else:
                total += float(obj["value"])
    return total

def get_past_claims(address):
    return [t for t in transaction_db.find({
        "$and":[
        {"type":"ClaimTransaction"},
        {"vout":{"$elemMatch":{"address":address}}}]})]

def is_valid_claim(tx, address, spent_ids, claim_ids):
    return convert_txid(tx['txid']) in spent_ids and not convert_txid(tx['txid']) in claim_ids and len(info_received_transaction(address, tx)["NEO"]) > 0

# return node status
@api.route("/v2/network/nodes")
def nodes():
    nodes = meta_db.find_one({"name": "node_status"})["nodes"]
    return jsonify({"net": NET, "nodes": nodes})

# return node status
@api.route("/v2/network/best_node")
def highest_node():
    nodes = meta_db.find_one({"name": "node_status"})["nodes"]
    highest_node = get_highest_node()
    return jsonify({"net": NET, "node": highest_node})

def compute_sys_fee(block_index):
    block_key = "sys_fee_{}".format(block_index)
    if cache.get(block_key):
        print("using cache")
        return cache.get(block_key)
    print(block_index)
    print("slowest")
    fees = [float(x["sys_fee"]) for x in transaction_db.find({ "$and":[
                    {"sys_fee": {"$gt": 0}},
                    {"block_index": {"$lte": block_index}}]})]
    total = int(sum(fees))
    cache.set(block_key, total, timeout=10000)
    return total

def compute_sys_fee_diff(index1, index2):
    fees = [float(x["sys_fee"]) for x in transaction_db.find({ "$and":[
                {"sys_fee": {"$gt": 0}},
                {"block_index": {"$gte": index1}},
                {"block_index": {"$lt": index2}} ]})]
    total = int(sum(fees))
    return total

def compute_net_fee(block_index):
    fees = [float(x["net_fee"]) for x in transaction_db.find({ "$and":[
            {"net_fee": {"$gt": 0}},
            {"block_index": {"$lt": block_index}}]})]
    return int(sum(fees))

# return node status
@api.route("/v2/block/sys_fee/<block_index>")
@cache.cached(timeout=500)
def sysfee(block_index):
    sys_fee = compute_sys_fee(int(block_index))
    return jsonify({"net": NET, "fee": sys_fee})

# return changes in balance over time
@api.route("/v2/address/history/<address>")
@cache.cached(timeout=15)
def balance_history(address):
    transactions = [t for t in transaction_db.find({"$or":[
        {"vout":{"$elemMatch":{"address":address}}},
        {"vin_verbose":{"$elemMatch":{"address":address}}}
    ]}).sort("block_index",-1).limit(20)]
    transactions = db2json({ "net": NET,
                             "name":"transaction_history",
                             "address":address,
                             "history": [balance_for_transaction(address, x) for x in transactions]})
    return jsonify(transactions)

def get_db_height():
    return [x for x in blockchain_db.find().sort("index", -1).limit(1)][0]["index"]

# get current block height
@api.route("/v2/block/height")
def block_height():
    height = get_db_height()
    return jsonify({"net": NET, "block_height": height})

# get transaction data from the DB
@api.route("/v2/transaction/<txid>")
@cache.cached(timeout=500)
def get_transaction(txid):
    return jsonify({**db2json(transaction_db.find_one({"txid": convert_txid(txid)})), "net": NET} )

def collect_txids(txs):
    store = {"NEO": {}, "GAS": {}}
    # 遍历里面的tx
    # 只留下输入的txid和index
    for tx in txs:
        for k in ["NEO", "GAS"]:
            for tx_ in tx[k]:
                store[k][(convert_txid(tx_["txid"]), tx_["index"])] = tx_
    return store

# get balance and unspent assets
@api.route("/v2/address/balance/<address>")
@cache.cached(timeout=15)
def get_balance(address):
    transactions = [t for t in transaction_db.find({"$or":[
        {"vout":{"$elemMatch":{"address":address}}}, # 输出 地址为这个地址的交易
        {"vin_verbose":{"$elemMatch":{"address":address}}} # 输入地址为这个地址的交易
    ]})]
    # 拿出这些tx中属于这个地址所有输入
    info_sent = [info_sent_transaction(address, t) for t in transactions]
    # 拿出这些tx中属于这个地址所有输出
    info_received = [info_received_transaction(address, t) for t in transactions]
    sent = collect_txids(info_sent)
    received = collect_txids(info_received)
    # received中不在sent中的条目为未花费的tx
    unspent = {k:{k_:v_ for k_,v_ in received[k].items() if (not k_ in sent[k])} for k in ["NEO", "GAS"]}
    # 顺便把余额总量算出来
    totals = {k:sum([v_["value"] for k_,v_ in unspent[k].items()]) for k in ["NEO", "GAS"]}
    return jsonify({
        "net": NET,
        "address": address,
        "NEO": {"balance": totals["NEO"],
                "unspent": [v for k,v in unspent["NEO"].items()]},
        "GAS": { "balance": totals["GAS"],
                "unspent": [v for k,v in unspent["GAS"].items()] }})

def filter_claimed_for_other_address(claims):
    out_claims = []
    for claim in claims.keys():
        tm = time.time()
        tx = transaction_db.find_one({"type":"ClaimTransaction", "claims_keys_v1":{"$elemMatch": {"key": "{}_{}".format(claim[0], claim[1])}}})
        print("time {}".format(time.time() - tm))
        if not tx:
            out_claims.append(claims[claim])
    return out_claims

def compute_claims(claims, transactions, end_block=False):
    block_diffs = []
    for tx in claims:
        obj = {"txid": convert_txid(tx["txid"])}
        obj["start"] = transactions[convert_txid(tx['txid'])]["block_index"]
        obj["value"] = tx["value"]
        obj["index"] = tx["index"]
        if not end_block:
            obj["end"] = transactions[tx['sending_id']]["block_index"]
        else:
            obj["end"] = end_block
        obj["sysfee"] = compute_sys_fee_diff(obj["start"], obj["end"])
        obj["claim"] = calculate_bonus([obj])
        block_diffs.append(obj)
    return block_diffs

def get_address_txs(address):
    query = address_db.find_one({"address": address})
    if query:
        return query["txs"]
    else:
        transactions = {convert_txid(t['txid']):t for t in transaction_db.find({"$or":[
            {"vout":{"$elemMatch":{"address":address}}},
            {"vin_verbose":{"$elemMatch":{"address":address}}}
        ]})}
        address_db.update_one({"address": address}, {"$set": {"txs": transactions}}, upsert=True)
        return transactions

# get available claims at an address
@api.route("/v2/address/claims/<address>")
@cache.cached(timeout=15)
def get_claim(address):
    start = time.time()
    transactions = {convert_txid(t['txid']):t for t in transaction_db.find({"$or":[
        {"vout":{"$elemMatch":{"address":address}}},
        {"vin_verbose":{"$elemMatch":{"address":address}}}
    ]})}
    # get sent neo info
    info_sent = [info_sent_transaction(address, t) for t in transactions.values()]
    sent_neo = collect_txids(info_sent)["NEO"]
    # get received neo info
    info_received = [info_received_transaction(address, t) for t in transactions.values()]
    received_neo = collect_txids(info_received)["NEO"]
    unspent_neo = {k:v for k,v in received_neo.items() if not k in sent_neo}
    # # get claim info
    past_claims = get_past_claims(address)
    claimed_neo = get_claimed_txids(past_claims)
    valid_claims = {k:v for k,v in sent_neo.items() if not k in claimed_neo}
    valid_claims = filter_claimed_for_other_address(valid_claims)
    block_diffs = compute_claims(valid_claims, transactions)
    total = sum([x["claim"] for x in block_diffs])
    # now do for unspent
    height = get_db_height()
    start = time.time()
    unspent_diffs = compute_claims([v for k,v in unspent_neo.items()], transactions, height)
    print("to compute claims: {}".format(time.time() - start))
    unspent_claim_total = sum([x["claim"] for x in block_diffs])
    return jsonify({
        "net": NET,
        "address": address,
        "total_claim": calculate_bonus(block_diffs),
        "total_unspent_claim": calculate_bonus(unspent_diffs),
        "claims": block_diffs})

@api.route("/v2/log", methods=["POST"])
def log_event():
    data = request.get_json()
    q.enqueue(log_event_worker, data)
    return jsonify({"success":"True"})

@api.route("/v2/version")
def version():
    return jsonify({"version":"0.0.7"})
