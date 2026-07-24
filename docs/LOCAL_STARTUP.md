# Local startup

Use your private Desktop launcher (kept outside this repository) to start both services:

```bash
/path/to/start_procureai_local.sh all
```

It starts FastAPI at `http://localhost:8000` and Vite at `http://localhost:5173`. The same launcher also supports `backend`, `frontend`, and `check` modes.

On the first run it creates `backend/.venv` and installs the frontend packages. That requires network access and can take several minutes because the backend has AI and browser-search dependencies. Later launches reuse the installed dependencies and only reinstall when `requirements.txt` or `package-lock.json` changes.

## Local configuration

Copy the backend template and add only credentials you are authorized to use:

```bash
cd /path/to/procureai-checkout
cp backend/.env.example backend/.env
```

Put only credentials you are authorised to use in your private local environment. `.env.local` is intended only for Vite variables such as `VITE_API_BASE_URL`.

## Fast marketplace price sources

Standard-product comparison now checks configured marketplace APIs before the
slower supplement search. When at least three relevant API items with published
EUR prices are returned, the page-scraping phase is skipped for that request.
Without configured marketplace credentials, Germany/default requests use
Idealo plus web research; an explicit non-German market uses only
country-qualified web research.

The primary optional integration is SerpApi Google Shopping. Set a private
SerpApi account key before starting the backend:

```bash
SERPAPI_API_KEY=...
SERPAPI_COUNTRY=de
SERPAPI_TIMEOUT_SECONDS=8
SERPAPI_CACHE_TTL_SECONDS=180
```

The selected target country is mapped to supported Google Shopping `gl`, `hl`,
and `google_domain` values (for example Germany -> `de` / `de` / `google.de`,
Poland -> `pl` / `pl` / `google.pl`). Only results that explicitly state `EUR`
or `€` are added to the EUR comparison table; the system does not invent
currency conversion. Poland often returns PLN results, so only explicit EUR
offers enter the table; insufficient results are supplemented by Poland-aware
web research, never by Idealo's German market. `Europe` / `EU` is not mapped
to Germany as a single Google Shopping market. Leave `SERPAPI_API_KEY` blank
to disable it completely.

The official eBay Browse API remains available as a secondary source:

```bash
EBAY_CLIENT_ID=...
EBAY_CLIENT_SECRET=...
EBAY_MARKETPLACE_ID=EBAY_DE
```

An organisation can also connect an approved marketplace or price-data API
through the normalized connector:

```bash
MARKETPLACE_API_URL=https://approved-provider.example/search
MARKETPLACE_API_KEY=...
MARKETPLACE_API_METHOD=GET
MARKETPLACE_PRICED_SHORT_CIRCUIT_MIN=3
```

The connector sends `query`, `limit`, and `country`, and accepts a JSON list
or an `items`/`results` array. Each item must include a product title, a EUR
price, and a product URL. Amazon Product Advertising API, Taobao Open Platform,
and other marketplaces each require their own approved developer account and
credentials; there is no legal, free, universal API for every marketplace.

## Troubleshooting

- If a port is occupied, stop the old process or use `PORT=8100 FRONTEND_PORT=5174 /Users/jianboyue/Desktop/start_procureai_local.sh all`.
- The project declares Node `22.14.0` in `.node-version`; the launcher warns when a different version is active.
- The default host is `127.0.0.1`, so the development server is not exposed to the local network. Set `HOST` and `FRONTEND_HOST` explicitly only when LAN access is required, and update CORS origins accordingly.
