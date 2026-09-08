# Frontend build

The app used to load Tailwind's Play CDN (`cdn.tailwindcss.com`) and
`unpkg.com/lucide@latest` at runtime. Both were unpinned third-party scripts
trusted by the Content-Security-Policy, and the Play CDN is a runtime JIT
compiler that Tailwind's own documentation says not to use in production.

Now:

- `static/css/tailwind.css` is **compiled and committed**. The production
  image needs no Node.
- `static/js/vendor/lucide-1.43.0.min.js` is the pinned icon library.
- The CSP is `script-src 'self'; style-src 'self'` — no CDN hosts.

## Rebuilding the CSS

Only needed when you add a Tailwind class that is not already in the
compiled file (the page will render without that one style until you do).

```bash
npm install          # once
npm run build:css    # or: npm run watch:css while editing
git add static/css/tailwind.css
```

`tailwind.config.js` scans `templates/**/*.html` and `static/js/*.js`.
Classes assembled at runtime by string concatenation (`text-${color}`) are not
detected; write the full class names in the source so the scanner sees them.

## Updating lucide

```bash
npm install lucide@<version>
cp node_modules/lucide/dist/umd/lucide.min.js static/js/vendor/lucide-<version>.min.js
```

Then update the `<script>` tag in `templates/base.html`.
