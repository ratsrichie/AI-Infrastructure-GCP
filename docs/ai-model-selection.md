# Dining assistant model selection

Both inference replicas will run Ollama on CPU and use the same answer-model revision. Answers and embeddings will be generated locally. Model weights will be staged on the VM's persistent /models volume.

| Purpose | Ollama tag | Registry short ID | License |
| --- | --- | --- | --- |
| Answer generation | qwen2.5:1.5b-instruct | 65ec06548149 | Apache-2.0 |
| Document and query embeddings | nomic-embed-text:v1.5 | 0a109f422b47 | Apache-2.0 |

Qwen has 1.54 billion parameters. Its Q4_K_M quantization reduces weight storage to approximately 986 MB, making it a candidate for short, source-grounded answers on CPU. The model card lists a maximum context of 32,768 tokens. Start with num_ctx=2048 and num_predict=128 per request. Fit the instructions, question, retrieved text and answer within that budget. Evaluate answer accuracy, latency and memory before changing these settings.

Nomic v1.5 is approximately 274 MB and supports the 768-dimensional vectors required by the MariaDB ingestion script. Use the same model revision for ingestion and retrieval. Prefix each passage with `search_document: ` and each question with `search_query: ` before embedding. Verify the output length before ingesting the 527 passages.

## Integration decisions to confirm

- A: Pass --model nomic-embed-text:v1.5 to scripts/embed.py for ingestion and search. Supply OLLAMA_URL from the integrated Compose configuration. Apply the appropriate prefix once before calling the embedding endpoint.
- B: The shared VM showed 1.9 GiB total RAM and 1.3 GiB available during the setup check. Agree on a larger testing window for two replicas. Measure weights, context cache and runtime overhead under load before finalizing VM capacity or container limits.
- C: Proposed internal replica names are ollama-1 and ollama-2, each on container port 11434. Route answer requests through the private inference load balancer. Establish the internal embedding URL with A and C. Keep inference ports unpublished.

Record the full installed model digests when staging the files. Download sizes are disk sizes. Runtime measurements and retrieval accuracy are pending.

## Sources

- https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct
- https://ollama.com/library/qwen2.5:1.5b-instruct
- https://huggingface.co/nomic-ai/nomic-embed-text-v1.5
- https://ollama.com/library/nomic-embed-text:v1.5
