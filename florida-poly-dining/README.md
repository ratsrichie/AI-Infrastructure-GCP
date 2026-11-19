# Florida Poly dining: MariaDB database + Docker Compose

This package builds `flpoly_dining` from the supplied menu PDF. It contains the database portion of the project, not a complete chat application or a deployed GCP system.

## What is included

- **4 restaurants**, including Fire + Ash with an explicitly unavailable menu.
- **521 menu listings**: The Fuse 413, Einstein's 63, Mosaic Cafe 45.
- **527 source-linked RAG chunks**: 521 menu chunks, 4 location-context chunks, 2 dataset-caveat chunks.
- Native `VECTOR(768)` storage and cosine index for embeddings produced later by a local model.
- A database-only Compose YAML with persistent storage, an internal network, health check, restart policy, resource limits, and password files.
- Local Ollama embedding and similarity-search script, sample SQL queries, and a runtime verification script.

The PDF is a historical snapshot collected November 15, 2026. Fuse items cover November 1–13; quoted hours cover November 15–21. This is not a live feed, and it cannot say which historical rotating dishes are served today.

## Start the database

Prerequisites: Docker Engine/Desktop running with Docker Compose v2 (`--wait` support), Bash, and OpenSSL. Extract the entire ZIP before running; keep the folder structure intact.

From inside the `florida-poly-dining` folder:

```bash
bash scripts/setup.sh
docker compose up -d --wait
bash scripts/verify.sh
```

`setup.sh` creates fresh random passwords locally if no password files exist. It does not print them. No passwords are distributed in this package. The Compose file reads those password files as secrets. Never commit generated secrets or database data to Git.

On the first start, the official MariaDB entrypoint imports `init/01_dining.sql`. On later starts, the bind-mounted database persists and is reused. Credentials and initialization scripts are applied only to a fresh data directory. Keep the original secrets with an existing database; regenerating files does not change stored database passwords.

Expected runtime verification:

```text
PASS: 4 restaurants, 521 menu listings, 527 RAG chunks.
```

To open a SQL prompt:

```bash
bash scripts/db-shell.sh
```

Then try:

```sql
SELECT name, menu_status FROM restaurants;
SELECT COUNT(*) FROM menu_items;
SELECT name, portion_text, calories_text FROM menu_items LIMIT 10;
```

The database hostname is `mariadb`, port `3306`, database `flpoly_dining`, username `dining_app`. The password is in `secrets/db_password.txt`. An eventual app must join this Compose project's `dining_internal` network. There is intentionally no publicly published database port. `docker compose exec` works for SQL access without opening one.

## Files and tables

| File | Purpose |
|---|---|
| `init/01_dining.sql` | Creates schema and loads the complete supplied snapshot |
| `compose.yml` | Starts MariaDB 11.8.9 with persistent storage |
| `scripts/setup.sh` | Prepares data directory and random password files |
| `scripts/db-shell.sh` | Opens an authenticated MariaDB prompt |
| `scripts/reimport.sh` | Reimports this exact snapshot safely |
| `scripts/verify.sh` | Checks counts against this snapshot on a running MariaDB |
| `scripts/embed.py` | Generates local embeddings or searches existing vectors |
| `requirements.txt` | Python dependency for the embedding script |
| `example_queries.sql` | Sample queries for the imported dataset |
| `menu_data.json` | Extracted menu rows for inspection/reuse |
| `validation.json` | Build validation results and limitations |
| `docs/source_menu.pdf` | Original supplied PDF for page citations |

| Table | Contents |
|---|---|
| `sources` | Original filename, collection date, source URL, snapshot limitations |
| `restaurants` | Names, buildings, menu availability, buffet admission |
| `restaurant_context` | Preserved descriptions, services, and both conflicting hours sets |
| `menu_items` | Listing, portion/calorie text, tags, price status, provenance, uncertainty flags |
| `rag_chunks` | Source-linked passages to retrieve for the AI |
| `rag_embeddings` | Locally generated vectors; initially empty |

Repeated names under different stations/categories are intentional source listings, not accidental import duplicates. The Fuse has 401 distinct names across 413 listings; Einstein's has 57 across 63. The import uses stable primary keys, a transaction for seeds, and duplicate-key no-ops so importing the exact script twice does not double the data. This is a fixed snapshot loader, not an update/synchronization engine: editing this dataset requires a deliberate migration/new snapshot strategy.

## Important data rules for the assistant

- Only The Fuse has a supplied price: **$18 buffet admission**, supplied by the user. This is not a per-item price. All `item_price_usd` values are SQL `NULL`. Other prices are unknown.
- The source gives conflicting hours. Both sets are retained with their stated week. Disclose the conflict; do not present historical hours as current.
- No menu is published for Fire + Ash. Do not invent dishes.
- Calorie/portion variants remain text exactly as reported; no unsupported matching of variants is inferred. Source anomalies are flagged, including Roast Beef at 11,200 calories, Chocolate Iced Brownies at 3,190 calories, and Harissa Aioli at 560 calories.
- Fuse glitched-meal station assignments and Mosaic category headings are marked unreliable.
- Missing dietary tags do not establish diet compatibility. No verified allergen or cross-contact information is provided.
- VG = Vegan, V = Vegetarian, PR = Good Source of Protein, CF = Climate Friendly.
- The original page number is stored with each listing and chunk. Use it with `docs/source_menu.pdf` for citations; the base dining website URL is also recorded.

## Local embeddings: the next AI step

The SQL file contains text passages, an empty vector table, and its index. It does **not** fabricate vectors. After MariaDB and your team's local Ollama service are running, load `nomic-embed-text` on that Ollama instance:

```bash
ollama pull nomic-embed-text
```

Install `requirements.txt` in your team's ingestion environment, then run `scripts/embed.py` from a machine/container with access to both services. A normal host process cannot use the Compose-only `mariadb` hostname without additional connectivity. Use your app/ingestion container joined to the database network, and a separate network connection from that container to Ollama. The database itself can remain on its internal network.

```bash
python -m pip install -r requirements.txt
DB_HOST=mariadb \
DB_PASSWORD_FILE=/run/secrets/dining_db_password \
OLLAMA_URL=http://ollama:11434 \
python scripts/embed.py
```

Those names assume your eventual ingestion container is given the password secret and network access. This database-only YAML does not create that ingestion container or Ollama. The script calls only the specified local Ollama API. It checks for 768 finite, nonzero dimensions before inserting native MariaDB vectors, commits each successful chunk, and skips previously embedded chunks on rerun. Keep the same model revision for ingestion and queries; record its digest for the final project. Switching models requires an explicit embedding rebuild, even if dimensions match.

Example retrieval using the same environment:

```bash
python scripts/embed.py --query 'What vegan options were listed at The Fuse?'
```

This prints the closest stored passages and their source pages, not an LLM-generated answer. Your application's next step is to pass retrieved passages, the question, and the uncertainty rules above to its local answer model. Metadata caveats should also be supplied consistently by the app, rather than relying solely on vector retrieval to select them. For precise calorie, dietary-tag, or price filtering, use SQL; semantic similarity alone does not guarantee those constraints.

## Persistent storage for the class project

The default bind mount is `./data/db`. It survives container restarts/recreation. Docker Compose does not create an LVM logical volume. On your GCP VM, your infrastructure owner must prepare and persistently mount the LVM volume at `/data/db`, then use:

```bash
export DINING_DB_PATH=/data/db
bash scripts/setup.sh
docker compose up -d --wait
```

Use an empty data directory on first initialization. Preserve the mount and credentials across reboots. The full project still needs VM/startup automation, Secret Manager integration, application/inference containers, and its other requirements; this package supplies the database component. The local generated secrets are a development setup, not a replacement for your GCP secrets workflow.

To stop the database while keeping its data:

```bash
docker compose down
```

To reimport this exact SQL snapshot without deleting data:

```bash
bash scripts/reimport.sh
bash scripts/verify.sh
```

Do not remove the data directory to rerun an import. Schema edits are not applied automatically to an existing volume.

## Validation performed

All 521 listings were extracted from the PDF's structured tables. Per-restaurant and per-category counts and distinct names match the PDF. Generated seed SQL was checked by a separate in-memory round trip for text/NULL preservation, row counts, references, and repeat-import behavior. YAML parsing and structural assertions, Bash syntax checks, and Python compilation were performed.

**MariaDB and Docker are not installed in the build environment, so live MariaDB import, native vector operations, Docker image startup, and Ollama embedding generation were not executed.** The included `verify.sh` is the runtime count check to run on your machine. The in-memory seed check does not substitute for a MariaDB runtime test. Resource limits are starting values; measure and adjust them on your VM.

## Technical references

- MariaDB native vector schema/index syntax: https://mariadb.com/docs/server/reference/sql-structure/vectors/create-table-with-vectors
- MariaDB image variables: https://mariadb.com/docs/server/server-management/automated-mariadb-deployment-and-administration/docker-and-mariadb/mariadb-server-docker-official-image-environment-variables
- Official image health checks: https://mariadb.com/docs/server/server-management/automated-mariadb-deployment-and-administration/docker-and-mariadb/using-healthcheck-sh
- MariaDB 11.8 release series: https://mariadb.com/docs/release-notes/community-server/11.8/what-is-mariadb-118
- Local embedding model: https://ollama.com/library/nomic-embed-text
