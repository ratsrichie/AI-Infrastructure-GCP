#!/usr/bin/env python3
"""Populate native MariaDB vectors from a local Ollama instance. No cloud API calls.
Run on a machine/container that can reach mariadb:3306 and the local Ollama API.
Install requirements.txt first. See README for connection and model setup.
"""
import argparse
import json
import math
import os
from pathlib import Path
from urllib.request import Request, urlopen
import pymysql


def embed(base, model, content):
    request = Request(base.rstrip('/') + '/api/embed',
                      data=json.dumps({'model': model, 'input': content, 'truncate': False}).encode(),
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=180) as response:
        vector = json.load(response)['embeddings'][0]
    if len(vector) != 768 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vector):
        raise ValueError('Expected 768 finite dimensions; use a compatible local embedding model.')
    if not any(vector):
        raise ValueError('Zero embedding is invalid for cosine search.')
    return json.dumps(vector, allow_nan=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='nomic-embed-text')
    parser.add_argument('--ollama-url', default=os.environ.get('OLLAMA_URL', 'http://ollama:11434'))
    parser.add_argument('--query', help='Search existing embeddings instead of ingesting')
    args = parser.parse_args()
    password_file = os.environ.get('DB_PASSWORD_FILE', '/run/secrets/dining_db_password')
    password = Path(password_file).read_text().strip()
    db = pymysql.connect(host=os.environ.get('DB_HOST', 'mariadb'),
                         port=int(os.environ.get('DB_PORT', '3306')),
                         user=os.environ.get('DB_USER', 'dining_app'), password=password,
                         database='flpoly_dining', charset='utf8mb4', autocommit=False,
                         cursorclass=pymysql.cursors.DictCursor)
    try:
        with db.cursor() as cur:
            # One model per vector index: changing model requires explicit re-embedding.
            cur.execute('SELECT DISTINCT model FROM rag_embeddings')
            if any(row['model'] != args.model for row in cur.fetchall()):
                raise ValueError('Existing embeddings use another model. Use the same model/revision or explicitly rebuild embeddings.')
            if args.query:
                vector = embed(args.ollama_url, args.model, args.query)
                cur.execute('''SELECT c.content, c.source_page, s.filename, s.source_url,
                              VEC_DISTANCE_COSINE(e.embedding, VEC_FromText(%s)) AS distance
                              FROM rag_embeddings e JOIN rag_chunks c USING(chunk_id)
                              JOIN sources s ON s.id=c.source_id
                              WHERE e.model=%s ORDER BY distance LIMIT 5''', (vector, args.model))
                print(json.dumps(cur.fetchall(), ensure_ascii=False, indent=2))
                return
            cur.execute('''SELECT c.chunk_id, c.content FROM rag_chunks c
                           LEFT JOIN rag_embeddings e USING(chunk_id)
                           WHERE e.chunk_id IS NULL ORDER BY c.chunk_id''')
            pending = cur.fetchall()
            for i, row in enumerate(pending, 1):
                vector = embed(args.ollama_url, args.model, row['content'])
                cur.execute('''INSERT INTO rag_embeddings(chunk_id,model,embedding)
                               VALUES (%s,%s,VEC_FromText(%s))
                               ON DUPLICATE KEY UPDATE chunk_id=VALUES(chunk_id)''',
                            (row['chunk_id'], args.model, vector))
                db.commit()
                print(f'Embedded {i}/{len(pending)}', flush=True)
            print(f'Complete: {len(pending)} new embeddings. Re-running skips stored chunks.')
    finally:
        db.close()


if __name__ == '__main__':
    main()
