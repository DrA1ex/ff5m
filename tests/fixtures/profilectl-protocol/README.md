# Profile control protocol examples

These are independently reviewed examples of the public CLI protocol, not a
complete snapshot schema. FF5M runs its actual parser, client and Recovery
behavior against them. Private keeps matching copies in
`src/overclocking/tests/fixtures/protocol` and checks required example fields
against its real executable. Additional output fields are allowed.

- `cpu-trial.snapshot`: a loaded 1248 MHz CPU trial in boot `boot-b`, armed in
  `boot-a`, with the live CPU at 480 MHz and the persistent boot selecting the
  stock fallback. The candidate is active; the confirmed profile is not.
- `emmc-hs25.snapshot`: a confirmed setting added by a test manifest row: HS52
  timing at 25 MHz on HS200 eMMC. Its setting id and label come from the row.

Copy changes to both repositories deliberately when the contract changes. Do not
generate expectations from a current implementation or import the other
repository at test runtime. Each repository must pass its own tests alone.
