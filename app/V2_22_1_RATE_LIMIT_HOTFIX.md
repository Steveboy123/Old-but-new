# V2.22.1 Rate-Limit Hotfix

Changes:
- Honors HTTP 429 / Cloudflare 1015 Retry-After instead of retrying after one second.
- Serializes local OTP authenticated-URL requests across app components.
- Enforces a small minimum gap between OTP REST calls.
- Rise/Fall now uses the shared retry/backoff connector.
- Advanced Account API snapshots are cached for five minutes instead of creating a fresh authenticated URL every minute.
- Existing presets, history and .env credentials are not part of this patch.
