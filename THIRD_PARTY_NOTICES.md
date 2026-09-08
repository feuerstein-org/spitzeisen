# Third-party notices

Python packages used as runtime and development dependencies retain their own package metadata
and licenses; their resolved versions are recorded in `uv.lock`.

The runtime does not vendor an OpenAPI parser, Smithy generator, Java binaries, or upstream Python
runtime. The deferred generator's dependency versions, adapted writer attribution, and bundled
Apache-2.0 license remain available with its source at commit
`0f2d1a6fb861d4018532fa4ed3f30b0af9666519`. Restore those notices and licenses if that implementation
is restored; see [the deferred Smithy design](docs/smithy-future.md).

The documentation archive retains an unapplied patch for
[smithy-translate](https://github.com/disneystreaming/smithy-translate), an Apache-2.0 project.
Copyright 2022 Disney Streaming. The [Apache-2.0 license](docs/archive/LICENSE-APACHE-2.0) is
retained with the patch. The patch is historical contribution material and is not included in the
runtime package.
