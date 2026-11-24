# ADR-006: The widget is an iframe document served by the Go API

- Status: Accepted
- Date: 2026-11-24

## Context

A site owner embeds the chat widget on their site. The widget talks to the public API
with a guest session token. Two risks shape the design:

- **Token theft.** Any script on the host page (the site's own code, analytics, ads,
  an XSS) can read what lives in the host page's JavaScript and storage.
- **Embedding on other sites.** The `site_key` is public by definition. Anyone can copy
  it and put the widget on their own site, spending the owner's budget.

Third-party cookies are blocked or partitioned by modern browsers, so a cookie-based
session inside a cross-site widget is not reliable.

## Decision

The widget runs in an iframe whose document is served by the Go API.

- The site adds a small loader script. The loader creates an iframe with
  `src = <go-api-origin>/embed/{site_key}`. Static widget assets come from `web`.
- `GET /embed/{site_key}` returns a small HTML document with the header
  `Content-Security-Policy: frame-ancestors <allowed_origins>`, built from
  `app.sites.allowed_origins`. The browser refuses to show the iframe on any other site.
- The widget calls the API from inside the iframe, so requests are same-origin with the
  Go API. The host page does not make API calls and does not see the token.
- The session token is random (at least 256 bits), short-lived, and stored in the
  database only as a hash. In the browser it lives in memory and in the iframe origin's
  storage (partitioned by the browser per top-level site). It is not a cookie and is
  never posted to the host page.
- `Origin`, `document.referrer` and `ancestorOrigins` are never used to identify the
  site or to authorize a request. The site is identified by `site_key`; the session by
  its token.
- CORS on the public API allows only the admin UI origin (`ADMIN_UI_ORIGIN`) with
  credentials. Widget requests do not need CORS because they are same-origin.
- `frame-ancestors` and CORS only control browsers. Scripts outside a browser are
  limited by rate limits and budgets (spec §9.2), not by the secrecy of `site_key`.

## Consequences

Trade-off: the browser enforces where the widget may be embedded and keeps the token
away from the host page, in exchange for the Go API serving a small HTML document and
the loader managing an iframe.

Positive:

- A script on the host page cannot read the session token: the same-origin policy
  separates the host page from the iframe.
- Embedding on a site that is not in `allowed_origins` fails in the browser, with no
  custom checks in our code.
- No third-party cookies, so the widget works the same in browsers that block them.
- No CORS rules for guest traffic.

Negative:

- The Go API serves HTML, not only JSON, and must set the CSP header per site. Changes
  to `allowed_origins` must reach this header without a long cache.
- Partitioned storage means a session does not follow the user from one host site to
  another. This is expected for a per-site widget.
- The iframe needs `postMessage` for sizing and open/close events. Messages from the
  host page are treated as untrusted input and never carry the token.
- CSP does not stop abuse by scripts, `curl` or bots. Rate limits, daily site budgets
  and an optional human check (spec §9.2) remain necessary.

## Alternatives considered

- **Widget script runs directly on the host page (rejected).** The simplest
  integration, but the token is in the host page's JavaScript and storage, where any
  script on that page can read it. API calls become cross-origin and need open CORS.
- **Session token in a cookie (rejected).** Inside a cross-site iframe it is a
  third-party cookie: blocked or partitioned by browsers, and it brings CSRF concerns.
- **Check `Origin` or `Referer` to allow only listed sites (rejected).** These headers
  are set by the client. Any non-browser client can fake them, so they give a false
  sense of protection. CSP `frame-ancestors` is enforced by the browser itself.
