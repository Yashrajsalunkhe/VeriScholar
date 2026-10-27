# Sample corpus licensing

`sample_corpus.jsonl.gz` contains plain-text extracts of English Wikipedia
articles, fetched through the public MediaWiki API by
`scripts/build_sample_corpus.py`.

Wikipedia article text is licensed under the
[Creative Commons Attribution-ShareAlike 4.0 International License](https://creativecommons.org/licenses/by-sa/4.0/)
(CC BY-SA 4.0). Each record in the artifact carries the source article's URL
in its `url` field; that URL is the attribution link, and the article's
revision history (linked from the article page) lists its authors.

The extracts are redistributed here unmodified apart from truncation to the
first ~12,000 characters per article. If you redistribute this artifact or
derivatives of it, you must do so under CC BY-SA 4.0 terms.

The engine source code in this repository is separately licensed under
Apache-2.0 (see the repository-root `LICENSE`); the CC BY-SA terms apply to
the sample corpus data only.
