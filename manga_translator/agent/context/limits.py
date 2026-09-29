"""Local transport defaults, separate from model context-token limits."""

MIB = 1024 * 1024

# On 2026-09-29 the configured api.deepseek.com/responses endpoint accepted
# 48 MiB of JSON and rejected 48 MiB + 1 byte with HTTP 413. This is a measured
# gateway boundary, not a general provider/model guarantee. Leave 4 MiB of
# headroom for estimation differences and gateway framing.
MAX_REQUEST_BYTES = 44 * MIB
ENVELOPE_RESERVE_BYTES = 256 * 1024

# Base64 expands 32 MiB to about 42.67 MiB. The request capability separately
# accounts for all messages and tool schemas before admitting the final body.
MAX_IMAGE_BATCH_BYTES = 32 * MIB
MAX_IMAGE_BYTES = 8_000_000
MAX_IMAGE_DIMENSION = 4096
MIN_IMAGE_DIMENSION = 1024
