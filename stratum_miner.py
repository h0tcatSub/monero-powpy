import argparse
import socket
import select
import binascii
import numpy as np  

import pyrx
import struct
import json
import sys
import os
import time
from multiprocessing import Process, Queue


pool_host = 'monerop.com'
pool_port = 4242
pool_pass = 'xx'
wallet_address = '45mdKwTvLemjUiAktUhMPofuc7dxNLJWbReJJ3ZTYXkt6ca6tQ8MCNzdSc1fk1cx1jYeVMz9BZ6ftfwzE1AEDUnxTCVwsMU'
nicehash = False


def main():
    pool_ip = socket.gethostbyname(pool_host)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((pool_ip, pool_port))
    
    q = Queue()
    proc = Process(target=worker, args=(q, s))
    proc.daemon = True
    proc.start()

    login = {
        'method': 'login',
        'params': {
            'login': wallet_address,
            'pass': pool_pass,
            'rigid': '',
            'agent': 'stratum-miner-py/0.1'
        },
        'id':1
    }
    print('Logging into pool: {}:{}'.format(pool_host, pool_port))
    print('Using NiceHash mode: {}'.format(nicehash))
    s.sendall(str(json.dumps(login)+'\n').encode('utf-8'))

    try:
        while 1:
            line = s.makefile().readline()
            r = json.loads(line)
            error = r.get('error')
            result = r.get('result')
            method = r.get('method')
            params = r.get('params')
            if error:
                print('Error: {}'.format(error))
                continue
            if result and result.get('status'):
                print('Status: {}'.format(result.get('status')))
            if result and result.get('job'):
                login_id = result.get('id')
                job = result.get('job')
                job['login_id'] = login_id
                q.put(job)
            elif method and method == 'job' and len(login_id):
                q.put(params)
    except KeyboardInterrupt:
        print('{}Exiting'.format(os.linesep))
        proc.terminate()
        s.close()
        sys.exit(0)


global seed_hash, height, blob

def pack_nonce(nonce):
    global blob
    b = binascii.unhexlify(blob)
    bin = struct.pack('39B', *bytearray(b[:39]))
    if nicehash:
        bin += struct.pack('I', nonce & 0x00ffffff)[:3]
        bin += struct.pack('{}B'.format(len(b)-42), *bytearray(b[42:]))
    else:
        bin += struct.pack('I', nonce)
        bin += struct.pack('{}B'.format(len(b)-43), *bytearray(b[43:]))
    return bin

def decode_hash(hash):
    return binascii.hexlify(hash).decode()

target = None
def find_hash(hash):
    global target
    return (struct.unpack('Q', hash[24:])[0] - target) < 0
def create_r64(hash):
    print(struct.unpack('Q', hash[24:])[0])
    return struct.unpack('Q', hash[24:])[0]
def make_hash(bin):
    return pyrx.get_rx_hash(bin, seed_hash, height)

def worker(q, s):
    global seed_hash, height, target,blob
    started = time.time()
    hash_count = 0

    while True:
        job = q.get()
        if job.get('login_id'):
            login_id = job.get('login_id')
            print('Login ID: {}'.format(login_id))
        blob = job.get('blob')
        target = job.get('target')
        job_id = job.get('job_id')
        height = job.get('height')
        seed_hash = binascii.unhexlify(job.get('seed_hash'))
        print('New job with target: {}, RandomX, height: {}'.format(target, height))
        target = struct.unpack('I', binascii.unhexlify(target))[0]
        if target >> 32 == 0:
            target = int(0xFFFFFFFFFFFFFFFF / int(0xFFFFFFFF / target))
        range_bits = 2 ** 18
        progress = 0
        nonces = range(1, range_bits)
        print("Start mining")
        while 1:
            bins = map(pack_nonce, nonces)
            hash = map(make_hash, bins)#bins, *(seed_hash, height))
            #else:
            #    hash = map(pycryptonight.cn_slow_hash, bins, cnv, 0, height)
            hash_count += range_bits
            sys.stdout.write(f"Progress : {hash_count}")
            sys.stdout.flush()
            hex_hash = map(decode_hash, hash)
            r64 = map(find_hash, hash)
            found_nonce = not all(r64)
            print(found_nonce)
            if found_nonce: #r64 < target:
                print()
                elapsed = time.time() - started
                hr = int(hash_count / elapsed)
                print("[+] FOUND NONCE!!")
                print('{}Hashrate: {} H/s'.format(os.linesep, hr))
                print("Checking Hash...")
                #r64 = list(map(create_r64, hash))
                #r64 = list(r64)
                nonce = r64.index(True) + progress
                bin = pack_nonce(nonce)
                hex_hash = pyrx.get_rx_hash(bin, seed_hash, height)

                #bins, *(seed_hash, height))
                #r64 = list(r64)
                #hex_hash =  list(hex_hash)
                #found_nonce_index = np.where(r64  < target)[0][0] + progress
                #nonce = found_nonce_index + progress
                print(f"nonce : {hex(nonce)}")
                print("Submitting nonce")

                print('Submitting hash: {}'.format(hex_hash))
                hex_hash = hex_hash[found_nonce_index]
                if nicehash:
                    nonce = struct.unpack('I', bins[39:43])[0]
                submit = {
                    'method':'submit',
                    'params': {
                        'id': login_id,
                        'job_id': job_id,
                        'nonce': binascii.hexlify(struct.pack('<I', nonce)).decode(),
                        'result': hex_hash
                    },
                    'id':1
                }
                s.sendall(str(json.dumps(submit)+'\n').encode('utf-8'))
                select.select([s], [], [], 3)
                if not q.empty():
                    break
            progress += range_bits
            nonces = range(progress, range_bits + progress)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--nicehash', action='store_true', help='NiceHash mode')
    parser.add_argument('--host', action='store', help='Pool host')
    parser.add_argument('--port', action='store', help='Pool port')

    parser.add_argument('--user', action='store', help='Miner Address')
    parser.add_argument('--password', action='store', help='Mining password')
    args = parser.parse_args()
    if args.nicehash:
        nicehash = True
    if args.host:
        pool_host = args.host
    if args.port:
        pool_port = int(args.port)
    wallet_address = args.user
    pool_pass = args.password
    main()
