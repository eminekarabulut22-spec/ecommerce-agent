# scripts/

CLI entry points for running the project outside the test suite.

- `run_demo.py` - posts a product image to a running API instance and prints the JSON result:

  ```bash
  uvicorn ecommerce_agent.api.main:app --reload   # terminal 1
  python scripts/run_demo.py path/to/photo.jpg    # terminal 2
  ```

- `batch_process_images.py` - posts every supported image (`.jpg`, `.jpeg`, `.png`, `.webp`)
  in a directory to a running API instance's `/products/process` endpoint, one at a time, and
  prints a concise result line per image (filename, outcome, product id if saved, reason
  otherwise). Non-image files (e.g. `README.md`) and unsupported extensions are skipped. A
  failure on one image doesn't stop the rest of the batch.

  ```bash
  uvicorn ecommerce_agent.api.main:app --reload                # terminal 1
  python scripts/batch_process_images.py                       # terminal 2, defaults to data/sample_images
  python scripts/batch_process_images.py --dir some/other/dir --url http://localhost:8000
  ```

- `seed_demo_products.py` - inserts 5 demo products directly into the database (no running API,
  no Anthropic/external API calls). Each product is built from one of the sample images in
  `data/sample_images/` (`bisiklet.jpeg`, `buzpateni.jpeg`, `snorkel.webp`, `stuhl.webp`,
  `tisch.webp`); titles, categories, descriptions, and prices are generic placeholders derived
  only from the filenames - no brand, model number, GTIN, or precise spec is invented. Each
  product gets a stable id derived from its image filename, so re-running the script upserts the
  same 5 rows instead of creating duplicates.

  ```bash
  python scripts/seed_demo_products.py                                     # seeds DATABASE_URL from .env
  python scripts/seed_demo_products.py --database-url sqlite:///./demo.db  # seeds a specific DB
  ```
