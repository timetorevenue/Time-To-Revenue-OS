# Third-party notices

TTROS Core is released under the MIT licence (see `LICENSE`). Third-party components keep their own licences.
Audit date: 2026-11-26. Dependencies are installed from their registries and are not vendored in this repository.

## Python (backend and tools)
76 installed packages from `requirements.txt` and `dashboard/backend/requirements.txt`, including transitive ones.
Licences: MIT, BSD-2/3-Clause, Apache-2.0, MIT-0, PSF-2.0, MIT-CMU and one MPL-2.0 package (`certifi`, used unmodified;
its source is available from its upstream project). No GPL, AGPL, SSPL or unlicensed packages were found.

## JavaScript (dashboard frontend)
385 packages in `dashboard/frontend/package-lock.json`: MIT, ISC, BSD-2/3-Clause, Apache-2.0, 0BSD, MIT-0,
OFL-1.1 (the two font packages above) and CC-BY-4.0 (`caniuse-lite`, browser-support data by the caniuse project and contributors;
attribution: https://github.com/browserslist/caniuse-lite). No GPL, AGPL or unspecified licences.

## Fonts
No font files are stored in this repository and none are hot-linked; Studio page styles use the system font stack.
The dashboard depends on two npm font packages, installed by `npm ci` and not vendored here:
- `@fontsource/dm-sans`: DM Sans, Copyright 2014 The DM Sans Project Authors (https://github.com/googlefonts/dm-fonts), SIL Open Font License 1.1.
- `@fontsource/ibm-plex-mono`: IBM Plex Mono, Copyright 2017 IBM Corp., SIL Open Font License 1.1.
Each package ships its own licence file; keep it with any redistributed build output (for example `dashboard/frontend/dist`).

## Graphify
`graphifyy` 0.9.11 is an optional external tool (MIT, Copyright (c) 2026 Safi Shamsi). It is not included.

## Referenced, not included
`skills-lock.json` pins three third-party skills by hash. Their text is not in this repository and their licence has not
been reviewed; do not vendor them without reviewing it.

## Original content
Code, tests, agent packages, workflow definitions and documentation in this repository are original to the TTROS project
and released under MIT. Example data is synthetic. No third-party headers or copyright notices were found in the original code.
Contributions are accepted under the same licence (see `CONTRIBUTING.md`).
