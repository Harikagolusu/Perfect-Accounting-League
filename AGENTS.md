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

## Architecture
- PAL reads company-scoped records from the FastAPI backend. There is no seeded or sample dataset; modules without a backend render empty states instead of placeholder records. Local-only session state lives in `src/lib/store.tsx`.
- Every company-owned record carries `companyId` and pages read it via `useScoped`, so the UI never shows another company's data (backend must still enforce this).
- File exports go through `src/lib/export.ts`; only CSV (and review XML) is generated in-browser, XLSX returns null so the UI never fakes a download.
- Shared UI pieces live in `src/components/pal/`; colours come only from tokens in `src/styles.css`.
