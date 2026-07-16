# Paris frontend/public overrides

Paris-branded variants of files whose repo versions are LFUCG/LT-branded.
They were previously ONLY a dirty working copy on the paris-rag box (which
blocked `deploy-code.sh`'s refuse-dirty guard); the live SPA on S3 already
serves them.

**Before running `deploy-spa.sh` for Paris**, copy these over the repo
versions in the build tree:

    cp jurisdictions/paris/frontend-public-overrides/llm-trust.json frontend/public/.well-known/llm-trust.json
    cp jurisdictions/paris/frontend-public-overrides/robots.txt frontend/public/robots.txt

Do NOT leave them applied in the box checkout — that re-dirties the tree.
