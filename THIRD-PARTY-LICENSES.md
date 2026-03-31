# Third-Party Licenses

quadletman includes and depends on open-source software. This file lists the
licenses of all runtime dependencies and vendored assets.

## Vendored JavaScript / CSS Libraries

These files are distributed with quadletman in `quadletman/static/vendor/`.

### Alpine.js 3.14.1

- **License:** MIT
- **Copyright:** (c) 2019-2024 Caleb Porzio
- **Source:** <https://github.com/alpinejs/alpine>

### HTMX 2.0.3

- **License:** BSD 2-Clause
- **Copyright:** (c) 2020, Big Sky Software
- **Source:** <https://github.com/bigskysoftware/htmx>

> Redistribution and use in source and binary forms, with or without
> modification, are permitted provided that the following conditions are met:
>
> 1. Redistributions of source code must retain the above copyright notice,
>    this list of conditions and the following disclaimer.
> 2. Redistributions in binary form must reproduce the above copyright notice,
>    this list of conditions and the following disclaimer in the documentation
>    and/or other materials provided with the distribution.
>
> THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
> AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
> IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
> DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
> FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
> DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
> SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
> CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
> OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
> OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

### xterm.js 5.5.0

- **License:** MIT
- **Copyright:** (c) 2014 The xterm.js authors; (c) 2012-2013 Christopher Jeffrey
- **Source:** <https://github.com/xtermjs/xterm.js>

### xterm-addon-fit

- **License:** MIT
- **Copyright:** (c) 2014 The xterm.js authors
- **Source:** <https://github.com/xtermjs/xterm.js>

### DOMPurify 3.2.6

- **License:** Apache-2.0 AND MPL-2.0 (dual-licensed)
- **Copyright:** (c) 2024 Cure53 and other contributors
- **Source:** <https://github.com/cure53/DOMPurify>

> Licensed under the Apache License, Version 2.0 (the "License"); you may not
> use this file except in compliance with the License. You may obtain a copy of
> the License at <https://www.apache.org/licenses/LICENSE-2.0>.
>
> Alternatively, this software may be used under the Mozilla Public License 2.0.
> You may obtain a copy of the License at
> <https://www.mozilla.org/en-US/MPL/2.0/>.

## Python Runtime Dependencies

These packages are installed at runtime but not distributed with quadletman.

| Package | License |
|---|---|
| FastAPI | MIT |
| Uvicorn | BSD-3-Clause |
| websockets | BSD-3-Clause |
| Jinja2 | BSD-3-Clause |
| aiosqlite | MIT |
| python-pam | MIT |
| six | MIT |
| Pydantic | MIT |
| python-multipart | Apache-2.0 |
| psutil | BSD-3-Clause |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| SQLAlchemy | MIT |
| Alembic | MIT |
| Starlette | BSD-3-Clause |
| anyio | MIT |
| Click | BSD-3-Clause |
| MarkupSafe | BSD-3-Clause |
| annotated-types | MIT |
| pydantic-core | MIT |
| typing_extensions | PSF-2.0 |
| Mako | MIT |
| greenlet | MIT AND PSF-2.0 |
| h11 | MIT |
| cffi | MIT |
| pycparser | BSD-3-Clause |
| certifi | MPL-2.0 |

## Build-Time Tools (not distributed)

| Tool | License |
|---|---|
| Tailwind CSS (pytailwindcss) | MIT |
| ruff | MIT |
| pytest | MIT |
| Babel | BSD-3-Clause |
| Vitest | MIT |

## License Compatibility Notes

All runtime and vendored licenses are permissive and compatible with the
MIT license used by quadletman:

- **MPL-2.0** (certifi, DOMPurify option): weak copyleft at the file level
  only. Modifications to MPL-licensed files must remain under MPL-2.0, but
  the obligation does not extend to the rest of the project.
- **Apache-2.0** (python-multipart, cryptography, DOMPurify option): includes
  a patent grant clause. Compatible with MIT when attribution is preserved.
- **PSF-2.0** (typing_extensions, greenlet): Python Software Foundation license,
  permissive and compatible with MIT.
