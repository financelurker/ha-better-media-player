# Brand icon

`icon.svg` is the source of the icon in `custom_components/better_media_player/brand/`
(`icon.png` 256×256, `icon@2x.png` and `logo.png` 512×512).

- Home Assistant 2026.3 and newer shows it on the integration and device pages.
- HACS reads `custom_components/better_media_player/brand/icon.png` from the repository
  (at the release tag, otherwise the default branch) and, once downloaded, from the
  installed copy. The release ZIP contains the `brand/` folder as well.

## Origin

The icon was drawn from scratch for this project, as hand-written SVG from basic shapes:
a rounded tile with a turquoise gradient and, on it, a display (outline and stand) with a
play/pause symbol and two sound arcs. No template, icon set, stock image or other
third-party artwork was used. Play, pause and sound arcs are generic symbols.

## Changing it

Edit `icon.svg`, then render the PNGs (Playwright with Chromium) and commit both:

```bash
cd scripts/brand
node render.js
```

The source stays outside the integration folder, so it is not part of the release ZIP.
