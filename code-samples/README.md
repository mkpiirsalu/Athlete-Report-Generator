# Code samples

Two modules from the private codebase, unchanged. They depend on modules and sample files that aren't published here, so they won't run on their own. Messages shown to users are in Estonian, the app's interface language.

| File | What it shows |
|---|---|
| [`vald_reader.py`](vald_reader.py) | Reads one VALD export (CSV or Excel) without pandas: tries several encodings, detects the delimiter, reads decimal commas and day-first dates, ignores blank filler rows, and recognises the test from the file's contents. A file it can't use comes back with a reason instead of an exception, so one stray file never stops a batch. |
| [`make_synthetic_data.py`](make_synthetic_data.py) | Writes realistic, fully invented exports for the regression tests: every athlete gets their own level and trend across testings, and the same arguments always produce byte-identical files. The screenshots and sample PDFs in this repo were made from its output. |
