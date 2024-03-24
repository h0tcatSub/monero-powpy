#  Copyright (c) 2019, The Monero Project
#  
#  All rights reserved.
#  
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#  
#  1. Redistributions of source code must retain the above copyright notice, this
#  list of conditions and the following disclaimer.
#  
#  2. Redistributions in binary form must reproduce the above copyright notice,
#  this list of conditions and the following disclaimer in the documentation
#  and/or other materials provided with the distribution.
#  
#  3. Neither the name of the copyright holder nor the names of its contributors
#  may be used to endorse or promote products derived from this software without
#  specific prior written permission.
#  
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
#  ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
#  WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
#  DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
#  FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
#  DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
#  SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
#  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
#  OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
#  OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import socket
import select
import binascii
import pyrx
import struct
import json
import sys
import os
import time
import numpy as np
from multiprocessing import Process, Queue

pool_host = None 
pool_port = None 
pool_pass = None
blob = None
wallet_address = None


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

def pack_nonce(nonce):
    global blob
    b = binascii.unhexlify(blob)
    bin = struct.pack('39B', *bytearray(b[:39]))
    bin += struct.pack('I', nonce)
    bin += struct.pack('{}B'.format(len(b)-43), *bytearray(b[43:]))
    return bin

pack_nonce = np.vectorize(pack_nonce)

seed_hash = None
height    = None

def get_hash(bin):
    global seed_hash
    global height
    hash = pyrx.get_rx_hash(bin, seed_hash, height)
    print(hash)
    return hash
get_hash = np.vectorize(get_hash)

def get_r64(hash):
    r64 = struct.unpack('Q', hash[24:])[0]
    print(r64)
    return r64
get_r64 = np.vectorize(get_r64)

def get_hex_hashs(hash):
    return binascii.hexlify(hash).decode()
get_hex_hashs = np.vectorize(get_hex_hashs)

def worker(q, s):
    started = time.time()
    hash_count = 0
    global blob
    global seed_hash
    global height

    while True:
        job = q.get()

        seed_hash = binascii.unhexlify(job.get('seed_hash'))
        if job.get('login_id'):
            login_id = job.get('login_id')
            print('Login ID: {}'.format(login_id))
        blob = job.get('blob')
        target = job.get('target')
        job_id = job.get('job_id')
        height = job.get('height')
        block_major = int(blob[:2], 16)
        cnv = 0
        if block_major >= 7:
            cnv = block_major - 6
        print('New job with target: {}, RandomX, height: {}'.format(target, height), file = sys.stderr)
        target = struct.unpack('I', binascii.unhexlify(target))[0]
        if target >> 32 == 0:
            target = int(0xFFFFFFFFFFFFFFFF / int(0xFFFFFFFF / target))
        nonce_range = 2 ** int(sys.argv[5])
        
        last_nonce = nonce_range
        nonces = np.arange(nonce_range)
        
        while True:
            bins  = pack_nonce(nonces)
            hashs = get_hash(bins) #pyrx.get_rx_hash(bin, seed_hash, height)
            hash_count += nonce_range
            sys.stdout.write('.')
            sys.stdout.flush()
            hex_hashs = get_hex_hashs(hashs)
            r64s = get_r64(hashs)

            found_nonce = np.any(r64s < target)
            found_nonce = np.isin(np.any(r64s < target), [True])

            print(f"Last nonce : {last_nonce}")
            if found_nonce:
                nonce_index = np.where(r64s < target)[0]
                nonce = nonces[nonce_index]
                hex_hash = hex_hashs[nonce_index]
                
                elapsed = time.time() - started
                hr = int(hash_count / elapsed)
                print('{}Hashrate: {} H/s'.format(os.linesep, hr), file = sys.stderr)
                #if nicehash:
                #    nonce = struct.unpack('I', bin[39:43])[0]
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
                print('[yay!] Submitting hash: {}'.format(hex_hash), file = sys.stderr)
                s.sendall(str(json.dumps(submit)+'\n').encode('utf-8'))
                select.select([s], [], [], 3)

                last_nonce = nonce_range
                nonces = np.arange(nonce_range)
                
                if not q.empty():
                    break
            
            nonces += nonce_range
            last_nonce += nonce_range

            
if __name__ == '__main__':
    pool_host = sys.argv[1]
    pool_port = int(sys.argv[2])
    pool_pass = sys.argv[3]

    wallet_address = sys.argv[4]

    main()

