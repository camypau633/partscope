<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

- Part data: client fetches via PROXY_POOL in src/lib/parts.ts (first entry is our own /api/public/part CORS proxy, donor URL/key from server secrets), normalizes with normalizeRemote, caches JSON + diagram image blob in IndexedDB; demo data only when every proxy fails.
- PWA is manifest-only (no app-shell service worker) to keep the preview safe; offline data comes from IndexedDB.
